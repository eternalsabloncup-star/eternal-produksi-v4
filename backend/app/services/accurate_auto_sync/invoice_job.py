"""Auto-sync Faktur Penjualan Accurate (kill switch OFF default)."""

from __future__ import annotations

import time
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.fields import as_text
from app.integrations.accurate.normalize_invoice_receipt import normalize_sales_invoice
from app.repositories import invoice_repository as invoices
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

DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_ROWS = 100
DEFAULT_TIMEOUT_SECONDS = 50.0


def is_invoice_auto_sync_enabled() -> bool:
    return bool(get_settings().invoice_auto_sync_enabled)


def _list_page_params(page: int, page_size: int) -> dict[str, Any]:
    return {"sp.page": page, "sp.pageSize": page_size}


def sync_one_invoice(
    db: Database,
    accurate_id: str,
    *,
    client: AccurateReadClient,
    owner_token: str,
) -> dict[str, Any]:
    require_owns_job_lock(db, job="invoice", owner_token=owner_token)
    payload = client.fetch_sales_invoice_detail(accurate_id)
    require_owns_job_lock(db, job="invoice", owner_token=owner_token)
    normalized = normalize_sales_invoice(payload)
    before = invoices.get_invoice(db, normalized["accurate_invoice_id"])
    invoices.upsert_invoice(db, normalized)
    if before is None:
        outcome = "created"
    elif before.get("total_amount") != normalized.get("total_amount") or before.get(
        "accurate_po_ids"
    ) != normalized.get("accurate_po_ids") or before.get("status") != normalized.get(
        "status"
    ):
        outcome = "updated"
    else:
        outcome = "unchanged"
    if normalized.get("review_required"):
        outcome = "review_required"
    return {
        "outcome": outcome,
        "accurate_invoice_id": normalized["accurate_invoice_id"],
        "accurate_po_ids": normalized.get("accurate_po_ids") or [],
    }


def run_invoice_auto_sync(
    db: Database,
    *,
    client: AccurateReadClient | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not is_invoice_auto_sync_enabled():
        return {
            "ok": True,
            "job": "invoice",
            "enabled": False,
            "accurate_requests": 0,
            "writes": 0,
            "status": disabled_status_view("invoice"),
        }

    mark_running(db, "invoice")
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
        token = acquire_job_lock(db, job="invoice")
        page_size = max(1, min(int(page_size), 100))
        max_pages = max(1, min(int(max_pages), 50))
        max_rows = max(1, min(int(max_rows), 500))
        timeout_seconds = max(0.001, float(timeout_seconds))

        for page in range(1, max_pages + 1):
            if time.monotonic() - started > timeout_seconds:
                stopped_reason = "timeout"
                break
            if counters["listed"] >= max_rows:
                stopped_reason = "max_rows"
                break
            if not refresh_job_lock(db, job="invoice", owner_token=token):
                raise AutoSyncLockError("Lease invoice hilang.")

            try:
                rows = reader.fetch_sales_invoice_list(
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
                inv_id = as_text(row.get("id"))
                if not inv_id:
                    counters["failed"] += 1
                    failures.append({"stage": "list_row", "code": "missing_invoice_id"})
                    continue
                try:
                    if not refresh_job_lock(db, job="invoice", owner_token=token):
                        raise AutoSyncLockError("Lease hilang mid-run.")
                    result = sync_one_invoice(
                        db, inv_id, client=reader, owner_token=token
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
                except Exception as exc:  # noqa: BLE001
                    counters["failed"] += 1
                    failures.append(
                        {
                            "stage": "detail_or_upsert",
                            "accurate_id": inv_id,
                            "error": type(exc).__name__,
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
        ok = not list_failed and not has_failures
        if list_failed or has_failures:
            ok = False

        status = record_job_result(
            db,
            "invoice",
            enabled=True,
            ok=ok,
            counters=counters,
            error=None if ok and complete else (stopped_reason or "incomplete"),
            error_code=stopped_reason,
            duration_seconds=time.monotonic() - started,
            partial=incomplete and not list_failed,
            complete=complete and not has_failures,
            detail={
                "stopped_reason": stopped_reason,
                "failures": failures[:30],
                "accurate_requests": accurate_requests,
                "discovery_complete": complete,
            },
        )
        return {
            "ok": ok,
            "job": "invoice",
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
            "invoice",
            enabled=True,
            ok=False,
            error=str(exc),
            error_code=exc.code,
            duration_seconds=time.monotonic() - started,
            complete=False,
        )
        return {
            "ok": False,
            "job": "invoice",
            "enabled": True,
            "accurate_requests": accurate_requests,
            "writes": 0,
            "error": exc.code,
            "status": status,
        }
    finally:
        if token:
            release_job_lock(db, job="invoice", owner_token=token)
