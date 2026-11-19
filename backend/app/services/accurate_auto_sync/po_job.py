"""Auto-sync Sales Order Accurate → Inbox (kill switch OFF default)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.fields import as_text
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
    refresh_job_lock,
    release_job_lock,
    require_owns_job_lock,
)
from app.services.accurate_auto_sync.status import (
    INCOMPLETE_STOP_REASONS,
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


def _list_page_params(page: int, page_size: int) -> dict[str, Any]:
    return {"sp.page": page, "sp.pageSize": page_size}


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
) -> dict[str, Any]:
    """Insert/update penuh hanya jika PO belum accepted (atomic filter)."""
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
    try:
        result = po_collection(db).find_one_and_update(
            _not_accepted_filter(accurate_id),
            {
                "$set": set_fields,
                "$setOnInsert": {
                    "created_at": now,
                    "accurate_id": accurate_id,
                    "operational": {"inbox_status": "PO Baru"},
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # Race: dokumen accepted sudah ada (unique accurate_id).
        return {"outcome": "skipped", "reason": "acceptance_race", "accurate_id": accurate_id}

    if result is None:
        return {"outcome": "skipped", "reason": "acceptance_race", "accurate_id": accurate_id}

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
    payload = client.fetch_sales_order_detail(accurate_id)
    require_owns_job_lock(db, job="po", owner_token=owner_token)
    po = normalize_sales_order(payload)
    before = get_by_accurate_id(db, po.accurate_id or accurate_id)
    line_diff = diff_synced_lines(before, po)

    if before is None:
        result = _upsert_unaccepted(db, po, line_diff=line_diff, owner_token=owner_token)
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
                return _apply_safe_accepted_metadata(
                    db,
                    accurate_id=str(po.accurate_id),
                    before=before2,
                    po=po,
                    owner_token=owner_token,
                )
            return {
                "outcome": "skipped",
                "reason": result.get("reason") or "acceptance_race",
                "accurate_id": str(po.accurate_id),
            }
        return {
            "outcome": "created",
            "accurate_id": str(po.accurate_id),
            "line_diff": line_diff,
        }

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
        return _apply_safe_accepted_metadata(
            db,
            accurate_id=str(po.accurate_id),
            before=before,
            po=po,
            owner_token=owner_token,
        )

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
    if not _material_line_change(line_diff) and not header_changed:
        return {
            "outcome": "unchanged",
            "accurate_id": str(po.accurate_id),
            "line_diff": line_diff,
        }

    result = _upsert_unaccepted(db, po, line_diff=line_diff, owner_token=owner_token)
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
            return _apply_safe_accepted_metadata(
                db,
                accurate_id=str(po.accurate_id),
                before=before2,
                po=po,
                owner_token=owner_token,
            )
        return {"outcome": "skipped", "reason": "acceptance_race", "accurate_id": str(po.accurate_id)}

    return {
        "outcome": "updated",
        "accurate_id": str(po.accurate_id),
        "line_diff": line_diff,
    }


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
        "failed": 0,
    }
    failures: list[dict[str, Any]] = []
    stopped_reason: str | None = None
    reader = client or AccurateReadClient.from_settings()
    accurate_requests = 0

    try:
        token = acquire_job_lock(db, job="po")
        page_size = max(1, min(int(page_size), 100))
        max_pages = max(1, min(int(max_pages), 50))
        max_rows = max(1, min(int(max_rows), 500))
        # Izinkan timeout kecil di test; produksi memakai DEFAULT_TIMEOUT_SECONDS.
        timeout_seconds = max(0.001, float(timeout_seconds))

        for page in range(1, max_pages + 1):
            if time.monotonic() - started > timeout_seconds:
                stopped_reason = "timeout"
                break
            if counters["listed"] >= max_rows:
                stopped_reason = "max_rows"
                break
            if not refresh_job_lock(db, job="po", owner_token=token):
                raise AutoSyncLockError("Lease PO auto-sync hilang — abort tanpa commit baru.")

            try:
                rows = reader.fetch_sales_order_list(
                    params=_list_page_params(page, page_size)
                )
                accurate_requests += 1
            except (AccurateAuthError, TimeoutError, ConnectionError, RuntimeError) as exc:
                stopped_reason = "list_failed"
                counters["failed"] += 1
                failures.append(
                    {
                        "stage": "list",
                        "page": page,
                        "error": type(exc).__name__,
                        "code": getattr(exc, "code", "list_failed"),
                    }
                )
                break

            if not rows:
                break

            for row in rows:
                if time.monotonic() - started > timeout_seconds:
                    stopped_reason = "timeout"
                    break
                if counters["listed"] >= max_rows:
                    stopped_reason = "max_rows"
                    break
                counters["listed"] += 1
                so_id = as_text(row.get("id"))
                if not so_id:
                    counters["failed"] += 1
                    failures.append({"stage": "list_row", "code": "missing_so_id"})
                    continue
                try:
                    if not refresh_job_lock(db, job="po", owner_token=token):
                        raise AutoSyncLockError("Lease hilang mid-run.")
                    result = sync_one_listed_sales_order(
                        db, so_id, client=reader, owner_token=token
                    )
                    accurate_requests += 1
                    counters["fetched"] += 1
                    key = str(result.get("outcome") or "failed")
                    if key in counters:
                        counters[key] += 1
                    else:
                        counters["failed"] += 1
                except AutoSyncLockError:
                    raise
                except Exception as exc:  # noqa: BLE001 — isolasi partial
                    counters["failed"] += 1
                    failures.append(
                        {
                            "stage": "detail_or_upsert",
                            "accurate_id": so_id,
                            "error": type(exc).__name__,
                            "code": getattr(exc, "code", "sync_failed"),
                        }
                    )

            if stopped_reason:
                break
            if len(rows) < page_size:
                break
        else:
            stopped_reason = "max_pages"

        incomplete = stopped_reason in INCOMPLETE_STOP_REASONS
        has_failures = counters["failed"] > 0 or bool(failures)
        list_failed = stopped_reason == "list_failed"
        complete = stopped_reason is None and not incomplete
        # Sukses penuh hanya jika discovery selesai dan tanpa failure.
        ok = (not list_failed) and (not has_failures) and (complete or incomplete)
        # Partial incomplete tanpa failure tetap ok=True tapi partial=True → bukan HEALTHY.
        if list_failed or has_failures:
            ok = False

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
                    or ("detail_failures" if has_failures else "incomplete")
                )
            ),
            error_code=stopped_reason or ("detail_failures" if has_failures else None),
            duration_seconds=time.monotonic() - started,
            partial=incomplete and not list_failed,
            complete=complete and not has_failures,
            detail={
                "stopped_reason": stopped_reason,
                "failures": failures[:30],
                "accurate_requests": accurate_requests,
                "max_pages": max_pages,
                "max_rows": max_rows,
                "discovery_complete": complete,
                "note": (
                    "Partial list tidak menjamin tidak ada PO tambahan di Accurate."
                    if incomplete
                    else None
                ),
            },
        )
        return {
            "ok": ok,
            "job": "po",
            "enabled": True,
            "partial": incomplete and not list_failed,
            "complete": complete and not has_failures,
            "accurate_requests": accurate_requests,
            "writes": counters["created"] + counters["updated"] + counters["review_required"],
            "counters": counters,
            "stopped_reason": stopped_reason,
            "failures": failures[:30],
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
