"""Sinkronisasi Sales Order Accurate → MongoDB V4 (idempotent)."""

from __future__ import annotations

from typing import Any, Literal

from pymongo.database import Database
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

from app.config import get_settings
from app.db_guard import (
    UnsafeMongoTargetError,
    assert_safe_mongo_production_sync_target,
    assert_safe_mongo_sync_target,
)
from app.domain.po import PoHeaderModel
from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.normalize import normalize_sales_order
from app.repositories.po_repository import (
    count_by_accurate_id,
    get_by_accurate_id,
    upsert_sales_order,
)

MongoGuardMode = Literal["test", "production"]


def _line_fingerprint(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("accurate_line_id"),
        item.get("item_code"),
        item.get("qty"),
        item.get("unit"),
        item.get("process_raw"),
        item.get("process_kind"),
        item.get("color"),
        item.get("item_name"),
    )


def diff_synced_lines(
    previous: dict[str, Any] | None,
    current: PoHeaderModel,
) -> dict[str, Any]:
    """Bandingkan line sebelum/sesudah sync (termasuk penghapusan item)."""
    prev_items = []
    if previous and isinstance(previous.get("header"), dict):
        prev_items = list(previous["header"].get("items") or [])
    prev_by_id = {
        str(i.get("accurate_line_id")): i
        for i in prev_items
        if i.get("accurate_line_id") is not None
    }
    curr_by_id = {
        str(i.accurate_line_id): i.model_dump()
        for i in current.items
        if i.accurate_line_id is not None
    }
    added = sorted(set(curr_by_id) - set(prev_by_id))
    removed = sorted(set(prev_by_id) - set(curr_by_id))
    changed = sorted(
        lid
        for lid in set(curr_by_id) & set(prev_by_id)
        if _line_fingerprint(prev_by_id[lid]) != _line_fingerprint(curr_by_id[lid])
    )
    return {
        "added_line_ids": added,
        "removed_line_ids": removed,
        "changed_line_ids": changed,
        "previous_line_count": len(prev_by_id),
        "current_line_count": len(curr_by_id),
    }


def _guard_db(db: Database, *, mongo_guard: MongoGuardMode = "test") -> None:
    import os

    settings = get_settings()
    expected_auth = (
        os.environ.get("V4_MONGO_AUTH_SOURCE")
        or os.environ.get("MONGO_AUTH_SOURCE")
        or ""
    ).strip() or None
    if mongo_guard == "production":
        assert_safe_mongo_production_sync_target(
            uri=settings.mongodb_uri,
            db_name=db.name,
            app_env=settings.app_env,
            expected_auth_source=expected_auth or "eternal_v4",
        )
        return
    assert_safe_mongo_sync_target(
        uri=settings.mongodb_uri,
        db_name=db.name,
        app_env=settings.app_env,
        expected_auth_source=expected_auth,
    )


def sync_sales_order_payload(
    db: Database,
    payload: dict[str, Any],
    *,
    mongo_guard: MongoGuardMode = "test",
    historical_floor_permit: Any | None = None,
) -> dict[str, Any]:
    """Normalisasi + upsert. Aman dijalankan berulang tanpa duplikasi header.

    Guard sama dengan auto-sync: date floor transDate + cabang kanonis Master.
    Cabang existing/manual tidak ditimpa; konflik ditandai review.

    ``historical_floor_permit`` — izin sempit dari
    ``historical_po_recovery.issue_floor_permit`` saja. Bukan bypass global.
    """
    from app.services.po_ingest_guards import (
        date_floor_reject_or_none,
        mark_branch_conflict_review,
        mark_empty_branch_review,
        resolve_branch_for_ingest,
    )

    _guard_db(db, mongo_guard=mongo_guard)
    accurate_id = str(payload.get("id") or payload.get("accurate_id") or "").strip()
    floor_hit = date_floor_reject_or_none(payload, accurate_id=accurate_id or "unknown")
    if floor_hit is not None:
        permit_ok = False
        if historical_floor_permit is not None:
            permits = getattr(historical_floor_permit, "permits", None)
            if callable(permits):
                permit_ok = bool(permits(accurate_id, payload))
        if not permit_ok:
            return {
                "po": None,
                "document": None,
                "accurate_id": accurate_id or None,
                "po_no": None,
                "line_count": 0,
                "was_insert": False,
                "duplicate_prevented": True,
                "count_after": count_by_accurate_id(db, accurate_id) if accurate_id else 0,
                "line_diff": {
                    "added_line_ids": [],
                    "removed_line_ids": [],
                    "changed_line_ids": [],
                    "previous_line_count": 0,
                    "current_line_count": 0,
                },
                "operational_preserved": True,
                "branch_apply": "rejected",
                "outcome": floor_hit.get("outcome"),
                "reason": floor_hit.get("reason"),
                "writes": 0,
            }
        # Exception allowlist historis: lanjut pipeline validasi cabang/identity.

    po: PoHeaderModel = normalize_sales_order(payload)
    try:
        before_doc = get_by_accurate_id(db, po.accurate_id or "")
        before = 1 if before_doc else 0
        line_diff = diff_synced_lines(before_doc, po)
        branch_res = resolve_branch_for_ingest(db, po=po, existing=before_doc)
        if not branch_res.get("ok"):
            if before_doc is None or not before_doc.get("branch_id"):
                if before_doc is not None and not before_doc.get("branch_id"):
                    mark_empty_branch_review(
                        db,
                        accurate_id=str(po.accurate_id or ""),
                        source_branch_id=branch_res.get("source_branch_id"),
                        reason=str(
                            branch_res.get("reason") or "existing_missing_branch_id"
                        ),
                    )
                return {
                    "po": po,
                    "document": before_doc,
                    "accurate_id": po.accurate_id,
                    "po_no": po.po_no,
                    "line_count": len(po.items),
                    "was_insert": False,
                    "duplicate_prevented": True,
                    "count_after": count_by_accurate_id(db, po.accurate_id or ""),
                    "line_diff": line_diff,
                    "operational_preserved": True,
                    "branch_apply": "review_required"
                    if before_doc is not None
                    else "rejected",
                    "outcome": "review_required"
                    if before_doc is not None
                    else branch_res.get("outcome"),
                    "reason": branch_res.get("reason"),
                    "writes": 0,
                }
        branch_action = str(branch_res.get("action") or "preserve")
        branch_id = branch_res.get("branch_id")
        # branch_id hanya lewat $setOnInsert untuk PO baru — tanpa auto-heal existing.
        stored = upsert_sales_order(
            db,
            po,
            line_diff=line_diff,
            branch_id_on_insert=(
                str(branch_id)
                if before == 0 and branch_action == "set" and branch_id
                else None
            ),
        )
        if before == 0 and branch_action == "set" and branch_id and stored.get("branch_id"):
            branch_apply = "set_from_accurate"
        elif before_doc is not None and not stored.get("branch_id"):
            mark_empty_branch_review(
                db,
                accurate_id=str(po.accurate_id or ""),
                source_branch_id=str(branch_id)
                if branch_id
                else branch_res.get("source_branch_id"),
                reason="existing_missing_branch_id",
            )
            stored = get_by_accurate_id(db, po.accurate_id or "") or stored
            branch_apply = "review_required"
        elif branch_action == "conflict":
            mark_branch_conflict_review(
                db,
                accurate_id=str(po.accurate_id or ""),
                existing_branch_id=str(branch_res.get("branch_id")),
                source_branch_id=str(branch_res.get("source_branch_id")),
            )
            stored = get_by_accurate_id(db, po.accurate_id or "") or stored
            branch_apply = "conflict_preserved"
        else:
            branch_apply = "preserve_existing"
        after = count_by_accurate_id(db, po.accurate_id or "")
    except UnsafeMongoTargetError:
        raise
    except ServerSelectionTimeoutError as exc:
        raise ConnectionError(
            "MongoDB tidak terjangkau (ServerSelectionTimeout)."
        ) from exc
    except PyMongoError as exc:
        raise ConnectionError(f"MongoDB error: {exc.__class__.__name__}") from exc

    return {
        "po": po,
        "document": stored,
        "accurate_id": po.accurate_id,
        "po_no": po.po_no,
        "line_count": len(po.items),
        "was_insert": before == 0,
        "duplicate_prevented": after == 1,
        "count_after": after,
        "line_diff": line_diff,
        "operational_preserved": isinstance((stored or {}).get("operational"), dict),
        "branch_apply": branch_apply,
        "outcome": (
            "created"
            if before == 0
            else ("review_required" if branch_apply == "review_required" else "updated")
        ),
        "writes": 1 if before == 0 or branch_apply != "review_required" else 1,
        "reason": (
            "existing_missing_branch_id"
            if branch_apply == "review_required"
            else None
        ),
    }


def sync_one_sales_order_from_accurate(
    db: Database,
    accurate_id: str | int,
    *,
    client: AccurateReadClient | None = None,
    mongo_guard: MongoGuardMode = "test",
) -> dict[str, Any]:
    """Manual sync satu SO: Accurate read → normalisasi → upsert Mongo V4.

    Tidak ada scheduler. Tidak menulis ke Accurate. Fail-closed pada target Mongo.
    ``mongo_guard="production"`` memakai jalur guard ops terpisah.
    """
    _guard_db(db, mongo_guard=mongo_guard)
    reader = client or AccurateReadClient.from_settings()
    try:
        payload = reader.fetch_sales_order_detail(accurate_id)
    except AccurateAuthError:
        raise
    except TimeoutError:
        raise
    except ConnectionError:
        raise

    result = sync_sales_order_payload(db, payload, mongo_guard=mongo_guard)
    result["fetch_ok"] = True
    outcome = result.get("outcome")
    writes = int(result.get("writes") or 0)
    # Rejected/excluded/failed: jangan akses result["po"].items (bisa None).
    if writes == 0 and outcome in {"excluded", "failed"}:
        return {
            "accurate_id": result.get("accurate_id"),
            "po_no": result.get("po_no"),
            "po_count": result.get("count_after", 0),
            "line_count": 0,
            "was_insert": False,
            "was_updated": False,
            "inserted": False,
            "updated": False,
            "duplicate_prevented": True,
            "idempotent": True,
            "count_after": result.get("count_after", 0),
            "line_diff": result.get("line_diff"),
            "operational_preserved": True,
            "fetch_ok": True,
            "process_kinds": [],
            "outcome": outcome,
            "reason": result.get("reason"),
            "writes": 0,
            "ok": False,
        }

    po = result.get("po")
    process_kinds = (
        [i.process_kind for i in po.items] if po is not None else []
    )
    # Jangan sertakan payload mentah / pelanggan di return untuk caller log-safe.
    safe = {
        "accurate_id": result["accurate_id"],
        "po_no": result["po_no"],
        "po_count": result["count_after"],
        "line_count": result["line_count"],
        "was_insert": result["was_insert"],
        "was_updated": not result["was_insert"],
        "inserted": result["was_insert"],
        "updated": not result["was_insert"],
        "duplicate_prevented": result["duplicate_prevented"],
        "idempotent": bool(result["duplicate_prevented"] and result["count_after"] == 1),
        "count_after": result["count_after"],
        "line_diff": result["line_diff"],
        "operational_preserved": result["operational_preserved"],
        "fetch_ok": True,
        "process_kinds": process_kinds,
        "outcome": outcome or ("created" if result["was_insert"] else "updated"),
        "writes": writes if writes else 1,
        "ok": True,
        "branch_apply": result.get("branch_apply"),
    }
    return safe


def read_synced_sales_order(
    db: Database,
    accurate_id: str | int,
) -> dict[str, Any] | None:
    try:
        return get_by_accurate_id(db, accurate_id)
    except ServerSelectionTimeoutError as exc:
        raise ConnectionError(
            "MongoDB tidak terjangkau (ServerSelectionTimeout)."
        ) from exc
