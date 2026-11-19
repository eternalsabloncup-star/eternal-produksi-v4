"""Auto-sync Sales Order Accurate → Inbox (kill switch OFF default)."""

from __future__ import annotations

import time
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.fields import as_text
from app.integrations.accurate.normalize import normalize_sales_order
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services.accurate_auto_sync.locks import (
    AutoSyncLockError,
    acquire_job_lock,
    refresh_job_lock,
    release_job_lock,
)
from app.services.accurate_auto_sync.status import mark_running, record_job_result
from app.services.po_acceptance import is_po_accepted
from app.services.po_sync import diff_synced_lines

DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_ROWS = 100
DEFAULT_TIMEOUT_SECONDS = 50.0

# Field header non-material yang aman diperbarui pada PO accepted.
_NON_MATERIAL_HEADER_KEYS = (
    "ship_zone",
    "buffer_label",
    "seller",
    "priority_label",
    "payment_terms",
    "expedition",
    "notes",
    "eta_ship",
    "po_date",
    "address",
    "address_pic_name",
    "print_user_name",
    "customer",
    "raw_sync_meta",
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


def _material_line_change(line_diff: dict[str, Any]) -> bool:
    return bool(
        line_diff.get("added_line_ids")
        or line_diff.get("removed_line_ids")
        or line_diff.get("changed_line_ids")
    )


def _apply_non_material_header_update(
    db: Database, before: dict[str, Any], po: Any
) -> dict[str, Any]:
    """Perbarui field header non-material saja; items/line_ids tetap."""
    from datetime import datetime, timezone

    header = dict(before.get("header") or {})
    fresh = po.model_dump()
    for key in _NON_MATERIAL_HEADER_KEYS:
        if key in fresh:
            header[key] = fresh[key]
    now = datetime.now(timezone.utc)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": str(po.accurate_id)},
        {
            "$set": {
                "header": header,
                "updated_at": now,
                "source_synced_at": now,
                "last_line_diff": {
                    "added_line_ids": [],
                    "removed_line_ids": [],
                    "changed_line_ids": [],
                    "previous_line_count": len(before.get("line_ids") or []),
                    "current_line_count": len(before.get("line_ids") or []),
                    "material_skipped": True,
                },
                "operational.sync_review_required": True,
                "operational.sync_review_reason": (
                    "Perubahan line/qty Accurate pada PO diterima — "
                    "memerlukan review; items tidak ditimpa otomatis."
                ),
            }
        },
    )
    return {"outcome": "review_required", "accurate_id": str(po.accurate_id)}


def sync_one_listed_sales_order(
    db: Database,
    accurate_id: str,
    *,
    client: AccurateReadClient,
) -> dict[str, Any]:
    """Fetch detail → normalize → upsert dengan guard acceptance."""
    payload = client.fetch_sales_order_detail(accurate_id)
    po = normalize_sales_order(payload)
    before = get_by_accurate_id(db, po.accurate_id or accurate_id)
    line_diff = diff_synced_lines(before, po)

    if before is None:
        stored = upsert_sales_order(db, po, line_diff=line_diff)
        ops = stored.get("operational") if isinstance(stored.get("operational"), dict) else {}
        if not ops.get("inbox_status"):
            db["accurate_sales_orders"].update_one(
                {"accurate_id": str(po.accurate_id)},
                {"$set": {"operational.inbox_status": "PO Baru"}},
            )
        return {
            "outcome": "created",
            "accurate_id": str(po.accurate_id),
            "line_diff": line_diff,
        }

    if is_po_accepted(before) and _material_line_change(line_diff):
        return _apply_non_material_header_update(db, before, po)

    header_keys = (
        "ship_zone",
        "buffer_label",
        "seller",
        "priority_label",
        "payment_terms",
        "expedition",
        "notes",
        "eta_ship",
        "customer",
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

    upsert_sales_order(db, po, line_diff=line_diff)
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
    enabled = is_po_auto_sync_enabled()
    mark_running(db, "po", enabled=enabled)
    if not enabled:
        status = record_job_result(
            db,
            "po",
            enabled=False,
            ok=True,
            counters={},
            duration_seconds=0.0,
            detail={"message": "Kill switch OFF — zero Accurate / zero write."},
        )
        return {
            "ok": True,
            "job": "po",
            "enabled": False,
            "accurate_requests": 0,
            "writes": 0,
            "status": status,
        }

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
        timeout_seconds = max(5.0, float(timeout_seconds))

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
                    result = sync_one_listed_sales_order(db, so_id, client=reader)
                    accurate_requests += 1
                    counters["fetched"] += 1
                    key = str(result.get("outcome") or "failed")
                    if key in counters:
                        counters[key] += 1
                    else:
                        counters["failed"] += 1
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

        ok = stopped_reason not in {"list_failed"}
        status = record_job_result(
            db,
            "po",
            enabled=True,
            ok=ok,
            counters=counters,
            error=None if ok else "list_or_partial_failure",
            error_code=stopped_reason,
            duration_seconds=time.monotonic() - started,
            detail={
                "stopped_reason": stopped_reason,
                "failures": failures[:30],
                "accurate_requests": accurate_requests,
                "max_pages": max_pages,
                "max_rows": max_rows,
            },
        )
        return {
            "ok": ok,
            "job": "po",
            "enabled": True,
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
