"""Auto-sync Sales Order Accurate → Inbox (kill switch OFF default)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.normalize import normalize_sales_order
from app.repositories.po_repository import (
    _to_document,
    get_by_accurate_id,
    po_collection,
    require_po_indexes,
)
from app.services.accurate_auto_sync.locks import (
    AutoSyncLockError,
    acquire_job_lock,
    release_job_lock,
    require_owns_job_lock,
)
from app.services.accurate_auto_sync.checkpoint import CONTRACT_SO
from app.services.accurate_auto_sync.discovery import run_discovery_cycle
from app.services.accurate_auto_sync.po_checkpoint import SORT_BACKFILL, SORT_INCREMENTAL
from app.services.accurate_auto_sync.status import (
    disabled_status_view,
    mark_running,
    record_job_result,
)
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, is_po_accepted
from app.services.po_sync import diff_synced_lines

DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_ROWS = 100
DEFAULT_TIMEOUT_SECONDS = 50.0

# Metadata aman pada PO accepted. Bukan customer/alamat/items/operasional.
_SAFE_ACCEPTED_HEADER_KEYS = (
    "ship_zone",
    "buffer_label",
    "seller",
    "priority_label",
    "payment_terms",
    "expedition",
    "notes",
    "eta_ship",
    "po_date",
    "print_user_name",
)


class PoAutoSyncDisabledError(PermissionError):
    code = "PO_AUTO_SYNC_DISABLED"


def is_po_auto_sync_enabled() -> bool:
    return bool(get_settings().po_auto_sync_enabled)


def require_po_auto_sync_enabled() -> None:
    if not is_po_auto_sync_enabled():
        raise PoAutoSyncDisabledError(
            "Auto-sync PO nonaktif (V4_PO_AUTO_SYNC_ENABLED). "
            "Zero Accurate request / zero write."
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _material_line_change(line_diff: dict[str, Any]) -> bool:
    return bool(
        line_diff.get("added_line_ids")
        or line_diff.get("removed_line_ids")
        or line_diff.get("changed_line_ids")
    )


def _not_accepted_filter(accurate_id: str) -> dict[str, Any]:
    return {
        "accurate_id": str(accurate_id),
        "$or": [
            {"operational.acceptance.status": {"$exists": False}},
            {"operational.acceptance.status": {"$ne": ACCEPTANCE_ACCEPTED}},
        ],
    }


def _accepted_filter(accurate_id: str) -> dict[str, Any]:
    return {
        "accurate_id": str(accurate_id),
        "operational.acceptance.status": ACCEPTANCE_ACCEPTED,
    }


def _safe_header_patch(before_header: dict[str, Any], po: Any) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for key in _SAFE_ACCEPTED_HEADER_KEYS:
        fresh = getattr(po, key, None)
        if before_header.get(key) != fresh:
            patch[f"header.{key}"] = fresh
    return patch


def _mark_material_review(
    db: Database,
    *,
    accurate_id: str,
    before: dict[str, Any],
    line_diff: dict[str, Any],
    po: Any,
    owner_token: str,
) -> dict[str, Any]:
    """Accepted + material: set review saja; items/operational snapshot tidak ditimpa."""
    require_owns_job_lock(db, job="po", owner_token=owner_token)
    now = _now()
    set_fields: dict[str, Any] = {
        "updated_at": now,
        "source_synced_at": now,
        "last_line_diff": {
            "added_line_ids": list(line_diff.get("added_line_ids") or []),
            "removed_line_ids": list(line_diff.get("removed_line_ids") or []),
            "changed_line_ids": list(line_diff.get("changed_line_ids") or []),
            "previous_line_count": line_diff.get("previous_line_count"),
            "current_line_count": line_diff.get("current_line_count"),
            "material_skipped": True,
        },
        "operational.sync_review_required": True,
        "operational.sync_review_reason": (
            "Perubahan line/qty Accurate pada PO diterima — "
            "memerlukan review; items tidak ditimpa otomatis."
        ),
    }
    # Metadata aman boleh ikut; customer/alamat tidak.
    before_header = before.get("header") if isinstance(before.get("header"), dict) else {}
    set_fields.update(_safe_header_patch(before_header, po))

    updated = po_collection(db).find_one_and_update(
        _accepted_filter(accurate_id),
        {"$set": set_fields},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        # Race: acceptance berubah — jangan tulis review pada PO belum diterima.
        return {"outcome": "skipped", "reason": "acceptance_race", "accurate_id": accurate_id}
    return {"outcome": "review_required", "accurate_id": accurate_id, "line_diff": line_diff}


def _upsert_unaccepted(
    db: Database,
    po: Any,
    *,
    line_diff: dict[str, Any],
    owner_token: str,
    branch_id: str | None = None,
    set_branch_on_insert: bool = False,
) -> dict[str, Any]:
    """Insert/update penuh hanya jika PO belum accepted (atomic filter).

    ``branch_id`` kanonis hanya di ``$setOnInsert`` / ``$set`` saat kosong —
    tidak menimpa cabang manual/existing.
    """
    require_owns_job_lock(db, job="po", owner_token=owner_token)
    require_po_indexes(db)
    doc = _to_document(po)
    accurate_id = str(doc["accurate_id"])
    now = doc["updated_at"]
    set_fields = {
        "source_po_id": doc["source_po_id"],
        "po_no": doc["po_no"],
        "origin": doc["origin"],
        "header": doc["header"],
        "line_ids": doc["line_ids"],
        "updated_at": now,
        "source_synced_at": now,
        "last_line_diff": {
            "added_line_ids": list(line_diff.get("added_line_ids") or []),
            "removed_line_ids": list(line_diff.get("removed_line_ids") or []),
            "changed_line_ids": list(line_diff.get("changed_line_ids") or []),
            "previous_line_count": line_diff.get("previous_line_count"),
            "current_line_count": line_diff.get("current_line_count"),
        },
    }
    set_on_insert: dict[str, Any] = {
        "created_at": now,
        "accurate_id": accurate_id,
        "operational": {"inbox_status": "PO Baru"},
    }
    if set_branch_on_insert and branch_id:
        set_on_insert["branch_id"] = branch_id
    try:
        result = po_collection(db).find_one_and_update(
            _not_accepted_filter(accurate_id),
            {
                "$set": set_fields,
                "$setOnInsert": set_on_insert,
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # Race: dokumen accepted sudah ada (unique accurate_id).
        return {"outcome": "skipped", "reason": "acceptance_race", "accurate_id": accurate_id}

    if result is None:
        return {"outcome": "skipped", "reason": "acceptance_race", "accurate_id": accurate_id}

    # Existing tanpa branch_id: jangan auto-heal di jalur sync rutin.
    return {
        "outcome": "updated",
        "accurate_id": accurate_id,
        "line_diff": line_diff,
        "doc": result,
    }


def _apply_safe_accepted_metadata(
    db: Database,
    *,
    accurate_id: str,
    before: dict[str, Any],
    po: Any,
    owner_token: str,
) -> dict[str, Any]:
    require_owns_job_lock(db, job="po", owner_token=owner_token)
    before_header = before.get("header") if isinstance(before.get("header"), dict) else {}
    patch = _safe_header_patch(before_header, po)
    if not patch:
        return {"outcome": "unchanged", "accurate_id": accurate_id}
    now = _now()
    patch.update({"updated_at": now, "source_synced_at": now})
    updated = po_collection(db).find_one_and_update(
        _accepted_filter(accurate_id),
        {"$set": patch},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        return {"outcome": "skipped", "reason": "acceptance_race", "accurate_id": accurate_id}
    return {"outcome": "updated", "accurate_id": accurate_id}


def sync_one_listed_sales_order(
    db: Database,
    accurate_id: str,
    *,
    client: AccurateReadClient,
    owner_token: str,
) -> dict[str, Any]:
    """Fetch detail → normalize → conditional upsert dengan guard acceptance + lease."""
    require_owns_job_lock(db, job="po", owner_token=owner_token)
    from app.services.po_repair_maintenance import WriterFence, po_repair_writer_section

    with po_repair_writer_section(db) as fence:
        return _sync_one_listed_sales_order_under_writer_lease(
            db,
            accurate_id,
            client=client,
            owner_token=owner_token,
            writer_fence=fence,
        )


def _sync_one_listed_sales_order_under_writer_lease(
    db: Database,
    accurate_id: str,
    *,
    client: AccurateReadClient,
    owner_token: str,
    writer_fence: WriterFence,
) -> dict[str, Any]:
    writer_fence.assert_valid(db)
    payload = client.fetch_sales_order_detail(accurate_id)
    writer_fence.assert_valid(db)
    require_owns_job_lock(db, job="po", owner_token=owner_token)
    from app.services.accurate_auto_sync.date_floor import detail_scope_outcome
    from app.services.po_ingest_guards import (
        mark_branch_conflict_review,
        mark_empty_branch_review,
        resolve_branch_for_ingest,
    )

    gated = detail_scope_outcome(payload, accurate_id=str(accurate_id))
    if gated is not None:
        return gated
    po = normalize_sales_order(payload)
    before = get_by_accurate_id(db, po.accurate_id or accurate_id)
    line_diff = diff_synced_lines(before, po)

    branch_res = resolve_branch_for_ingest(db, po=po, existing=before)
    if not branch_res.get("ok"):
        # Insert baru / update tanpa cabang valid — zero write.
        if before is None:
            return {
                "outcome": branch_res.get("outcome") or "failed",
                "reason": branch_res.get("reason"),
                "code": branch_res.get("code"),
                "accurate_id": str(po.accurate_id or accurate_id),
                "writes": 0,
            }
        # Existing tanpa cabang kanonis + source invalid: jangan perluas scope palsu.
        if not before.get("branch_id"):
            mark_empty_branch_review(
                db,
                accurate_id=str(po.accurate_id or accurate_id),
                source_branch_id=branch_res.get("source_branch_id"),
                reason=str(branch_res.get("reason") or "existing_missing_branch_id"),
            )
            return {
                "outcome": "review_required",
                "reason": branch_res.get("reason") or "existing_missing_branch_id",
                "code": branch_res.get("code"),
                "accurate_id": str(po.accurate_id or accurate_id),
                "writes": 0,
            }

    branch_action = str(branch_res.get("action") or "preserve")
    branch_id = branch_res.get("branch_id")
    if branch_action == "conflict":
        mark_branch_conflict_review(
            db,
            accurate_id=str(po.accurate_id or accurate_id),
            existing_branch_id=str(branch_res.get("branch_id")),
            source_branch_id=str(branch_res.get("source_branch_id")),
        )

    def _note_empty_branch_if_needed(doc: dict[str, Any] | None) -> bool:
        """True jika existing tanpa branch_id — review, tanpa auto-fill."""
        if doc is None or doc.get("branch_id"):
            return False
        if branch_action != "set":
            # Source invalid sudah ditangani di atas; tetap tandai bila kosong.
            mark_empty_branch_review(
                db,
                accurate_id=str(po.accurate_id or accurate_id),
                source_branch_id=branch_res.get("source_branch_id"),
                reason="existing_missing_branch_id",
            )
            return True
        mark_empty_branch_review(
            db,
            accurate_id=str(po.accurate_id or accurate_id),
            source_branch_id=str(branch_id) if branch_id else branch_res.get(
                "source_branch_id"
            ),
            reason="existing_missing_branch_id",
        )
        return True

    if before is None:
        if branch_action != "set" or not branch_id:
            return {
                "outcome": "failed",
                "reason": "missing_branch_id",
                "code": "missing_branch_id",
                "accurate_id": str(po.accurate_id or accurate_id),
                "writes": 0,
            }
        result = _upsert_unaccepted(
            db,
            po,
            line_diff=line_diff,
            owner_token=owner_token,
            branch_id=str(branch_id),
            set_branch_on_insert=True,
        )
        if result.get("outcome") == "skipped":
            before2 = get_by_accurate_id(db, str(po.accurate_id))
            if before2 is not None and is_po_accepted(before2):
                if _material_line_change(line_diff):
                    return _mark_material_review(
                        db,
                        accurate_id=str(po.accurate_id),
                        before=before2,
                        line_diff=line_diff,
                        po=po,
                        owner_token=owner_token,
                    )
                needs_review = _note_empty_branch_if_needed(before2)
                meta = _apply_safe_accepted_metadata(
                    db,
                    accurate_id=str(po.accurate_id),
                    before=before2,
                    po=po,
                    owner_token=owner_token,
                )
                if needs_review:
                    return {
                        **meta,
                        "outcome": "review_required",
                        "reason": "existing_missing_branch_id",
                    }
                return meta
            return {
                "outcome": "skipped",
                "reason": result.get("reason") or "acceptance_race",
                "accurate_id": str(po.accurate_id),
            }
        return {
            "outcome": "created",
            "accurate_id": str(po.accurate_id),
            "line_diff": line_diff,
            "branch_id": str(branch_id),
        }

    needs_empty_review = _note_empty_branch_if_needed(before)

    if is_po_accepted(before):
        if _material_line_change(line_diff):
            return _mark_material_review(
                db,
                accurate_id=str(po.accurate_id),
                before=before,
                line_diff=line_diff,
                po=po,
                owner_token=owner_token,
            )
        meta_out = _apply_safe_accepted_metadata(
            db,
            accurate_id=str(po.accurate_id),
            before=before,
            po=po,
            owner_token=owner_token,
        )
        if needs_empty_review:
            return {
                **meta_out,
                "outcome": "review_required",
                "reason": "existing_missing_branch_id",
            }
        return meta_out

    # Belum accepted: update penuh dengan filter anti-race Terima PO.
    header_keys = (
        *_SAFE_ACCEPTED_HEADER_KEYS,
        "customer",
        "address",
        "address_pic_name",
        "po_no",
    )
    before_header = before.get("header") if isinstance(before.get("header"), dict) else {}
    header_changed = any(
        before_header.get(key) != getattr(po, key, None) for key in header_keys
    )
    if (
        not _material_line_change(line_diff)
        and not header_changed
        and branch_action != "conflict"
    ):
        if needs_empty_review:
            return {
                "outcome": "review_required",
                "reason": "existing_missing_branch_id",
                "accurate_id": str(po.accurate_id),
                "line_diff": line_diff,
            }
        return {
            "outcome": "unchanged",
            "accurate_id": str(po.accurate_id),
            "line_diff": line_diff,
        }

    result = _upsert_unaccepted(
        db,
        po,
        line_diff=line_diff,
        owner_token=owner_token,
        branch_id=None,
        set_branch_on_insert=False,
    )
    if result.get("outcome") == "skipped":
        before2 = get_by_accurate_id(db, str(po.accurate_id))
        if before2 is not None and is_po_accepted(before2):
            if _material_line_change(line_diff):
                return _mark_material_review(
                    db,
                    accurate_id=str(po.accurate_id),
                    before=before2,
                    line_diff=line_diff,
                    po=po,
                    owner_token=owner_token,
                )
            needs_review = _note_empty_branch_if_needed(before2)
            meta = _apply_safe_accepted_metadata(
                db,
                accurate_id=str(po.accurate_id),
                before=before2,
                po=po,
                owner_token=owner_token,
            )
            if needs_review:
                return {
                    **meta,
                    "outcome": "review_required",
                    "reason": "existing_missing_branch_id",
                }
            return meta
        return {"outcome": "skipped", "reason": "acceptance_race", "accurate_id": str(po.accurate_id)}

    out: dict[str, Any] = {
        "outcome": "review_required" if needs_empty_review else "updated",
        "accurate_id": str(po.accurate_id),
        "line_diff": line_diff,
    }
    if needs_empty_review:
        out["reason"] = "existing_missing_branch_id"
    if branch_action == "conflict":
        out["outcome"] = "review_required"
        out["branch_conflict"] = True
        out["reason"] = "branch_conflict"
    return out


def run_po_auto_sync(
    db: Database,
    *,
    client: AccurateReadClient | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Satu siklus auto-sync PO. Fail-closed jika kill switch OFF."""
    if not is_po_auto_sync_enabled():
        # ZERO WRITE: tanpa lock, status mutation, Accurate HTTP, atau PO write.
        return {
            "ok": True,
            "job": "po",
            "enabled": False,
            "accurate_requests": 0,
            "writes": 0,
            "status": disabled_status_view("po"),
        }

    mark_running(db, "po")
    started = time.monotonic()
    token: str | None = None
    counters = {
        "listed": 0,
        "fetched": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "review_required": 0,
        "excluded": 0,
        "failed": 0,
    }
    failures: list[dict[str, Any]] = []
    reader = client or AccurateReadClient.from_settings()
    accurate_requests = 0

    try:
        token = acquire_job_lock(db, job="po")
        page_size = max(1, min(int(page_size), 100))
        max_pages = max(1, min(int(max_pages), 50))
        max_rows = max(1, min(int(max_rows), 500))
        timeout_seconds = max(0.001, float(timeout_seconds))

        def _fetch_list(params: dict[str, Any]) -> list[dict[str, Any]]:
            return reader.fetch_sales_order_list(params=params)

        def _sync_one(accurate_id: str) -> dict[str, Any]:
            return sync_one_listed_sales_order(
                db, accurate_id, client=reader, owner_token=token
            )

        disc = run_discovery_cycle(
            db,
            job="po",
            owner_token=token,
            fetch_list=_fetch_list,
            sync_one=_sync_one,
            counters=counters,
            failures=failures,
            page_size=page_size,
            max_pages=max_pages,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
            started=started,
            contract_status=CONTRACT_SO,
        )
        accurate_requests = int(disc["accurate_requests"])
        checkpoint = disc["checkpoint"]
        stopped_reason = disc.get("stopped_reason")
        ok = bool(disc["ok"])
        partial = bool(disc["partial"])
        complete = bool(disc["complete"])

        status = record_job_result(
            db,
            "po",
            enabled=True,
            ok=ok,
            counters=counters,
            error=(
                None
                if ok and complete
                else (
                    stopped_reason
                    or ("detail_failures" if disc["has_failures"] else "incomplete")
                )
            ),
            error_code=stopped_reason or ("detail_failures" if disc["has_failures"] else None),
            duration_seconds=time.monotonic() - started,
            partial=partial,
            complete=complete,
            detail={
                "stopped_reason": stopped_reason,
                "failures": failures[:30],
                "accurate_requests": accurate_requests,
                "max_pages": max_pages,
                "max_rows": max_rows,
                "pages_this_cycle": disc["pages_this_cycle"],
                "phase": checkpoint.get("phase"),
                "contract_status": CONTRACT_SO,
                "sort_warnings": disc.get("sort_warnings") or [],
                "checkpoint": {
                    "next_page": checkpoint.get("next_page"),
                    "backfill_complete": checkpoint.get("backfill_complete"),
                    "last_completed_id": checkpoint.get("last_completed_id"),
                    "last_completed_page": checkpoint.get("last_completed_page"),
                    "incremental_next_page": checkpoint.get("incremental_next_page"),
                    "incremental_cycles_completed": checkpoint.get(
                        "incremental_cycles_completed"
                    ),
                    "incremental_crawl_complete": disc.get("incremental_crawl_complete"),
                    "sort_backfill": SORT_BACKFILL,
                    "sort_incremental": SORT_INCREMENTAL,
                },
                "incremental_pending": disc.get("incremental_pending"),
                "discovery_complete": complete,
                "note": (
                    None
                    if complete
                    else (
                        "Backfill id|asc belum selesai — checkpoint next_page dipertahankan."
                        if not disc["backfill_done"]
                        else (
                            "incremental_pending — crawl lastUpdate belum selesai."
                            if disc.get("incremental_pending")
                            else None
                        )
                    )
                ),
            },
        )
        return {
            "ok": ok,
            "job": "po",
            "enabled": True,
            "partial": partial,
            "complete": complete,
            "accurate_requests": accurate_requests,
            "writes": counters["created"] + counters["updated"] + counters["review_required"],
            "counters": counters,
            "stopped_reason": stopped_reason,
            "failures": failures[:30],
            "checkpoint": checkpoint,
            "status": status,
        }
    except AutoSyncLockError as exc:
        status = record_job_result(
            db,
            "po",
            enabled=True,
            ok=False,
            error=str(exc),
            error_code=exc.code,
            duration_seconds=time.monotonic() - started,
            partial=False,
            complete=False,
        )
        return {
            "ok": False,
            "job": "po",
            "enabled": True,
            "accurate_requests": accurate_requests,
            "writes": 0,
            "error": exc.code,
            "status": status,
        }
    finally:
        if token:
            release_job_lock(db, job="po", owner_token=token)
