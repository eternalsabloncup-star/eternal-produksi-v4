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
) -> dict[str, Any]:
    """Normalisasi + upsert. Aman dijalankan berulang tanpa duplikasi header.

    Cabang kanonis: isi ``branch_id`` dari Accurate ``branchId`` bila dokumen
    belum punya identitas. Re-ingest / edit manual tidak ditimpa diam-diam.
    Mapping Master tidak diperlukan untuk otorisasi.
    """
    from app.repositories.po_repository import set_branch_id_if_empty
    from app.services.branch_identity import (
        extract_accurate_branch_id,
        normalize_branch_identity,
    )
    from app.services.object_auth import object_branch_code

    _guard_db(db, mongo_guard=mongo_guard)
    po: PoHeaderModel = normalize_sales_order(payload)
    try:
        before_doc = get_by_accurate_id(db, po.accurate_id or "")
        before = 1 if before_doc else 0
        line_diff = diff_synced_lines(before_doc, po)
        stored = upsert_sales_order(db, po, line_diff=line_diff)
        accurate_branch_id = extract_accurate_branch_id(stored)
        if accurate_branch_id is None and isinstance(po.raw_sync_meta, dict):
            accurate_branch_id = normalize_branch_identity(
                po.raw_sync_meta.get("accurate_branch_id")
            )
        branch_apply = "preserve_existing"
        if object_branch_code(before_doc):
            branch_apply = "preserve_existing"
        elif accurate_branch_id:
            filled = set_branch_id_if_empty(
                db, po.accurate_id or "", accurate_branch_id
            )
            if filled is not None:
                stored = filled
                branch_apply = "set_from_accurate"
            else:
                branch_apply = "leave_unset"
        else:
            branch_apply = "leave_unset"
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
        "operational_preserved": isinstance(stored.get("operational"), dict),
        "branch_apply": branch_apply,
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
        "process_kinds": [i.process_kind for i in result["po"].items],
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
