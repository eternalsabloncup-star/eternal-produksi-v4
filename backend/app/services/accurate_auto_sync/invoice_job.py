"""Auto-sync Faktur Penjualan Accurate (kill switch OFF default)."""

from __future__ import annotations

import time
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.normalize_invoice_receipt import (
    invoice_material_equal,
    normalize_sales_invoice,
)
from app.repositories import invoice_repository as invoices
from app.services.accurate_auto_sync.checkpoint import CONTRACT_SI
from app.services.accurate_auto_sync.discovery import run_discovery_cycle
from app.services.accurate_auto_sync.locks import (
    AutoSyncLockError,
    acquire_job_lock,
    release_job_lock,
    require_owns_job_lock,
)
from app.services.accurate_auto_sync.po_checkpoint import SORT_BACKFILL, SORT_INCREMENTAL
from app.services.accurate_auto_sync.status import (
    disabled_status_view,
    mark_running,
    record_job_result,
)

DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_ROWS = 100
DEFAULT_TIMEOUT_SECONDS = 50.0

INVOICE_CONTRACT_STATUS = CONTRACT_SI


def is_invoice_auto_sync_enabled() -> bool:
    return bool(get_settings().invoice_auto_sync_enabled)


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
    from app.services.accurate_auto_sync.date_floor import detail_scope_outcome

    gated = detail_scope_outcome(payload, accurate_id=str(accurate_id))
    if gated is not None:
        return gated
    normalized = normalize_sales_invoice(payload)
    before = invoices.get_invoice(db, normalized["accurate_invoice_id"])
    if invoice_material_equal(before, normalized):
        return {
            "outcome": "unchanged",
            "accurate_invoice_id": normalized["accurate_invoice_id"],
            "accurate_po_ids": normalized.get("accurate_po_ids") or [],
            "writes": 0,
        }
    invoices.upsert_invoice(db, normalized)
    if before is None:
        outcome = "created"
    elif normalized.get("review_required"):
        outcome = "review_required"
    else:
        outcome = "updated"
    return {
        "outcome": outcome,
        "accurate_invoice_id": normalized["accurate_invoice_id"],
        "accurate_po_ids": normalized.get("accurate_po_ids") or [],
        "writes": 1,
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
        "excluded": 0,
        "failed": 0,
    }
    failures: list[dict[str, Any]] = []
    reader = client or AccurateReadClient.from_settings()
    accurate_requests = 0

    try:
        token = acquire_job_lock(db, job="invoice")
        page_size = max(1, min(int(page_size), 100))
        max_pages = max(1, min(int(max_pages), 50))
        max_rows = max(1, min(int(max_rows), 500))
        timeout_seconds = max(0.001, float(timeout_seconds))

        def _fetch_list(params: dict[str, Any]) -> list[dict[str, Any]]:
            return reader.fetch_sales_invoice_list(params=params)

        def _sync_one(accurate_id: str) -> dict[str, Any]:
            return sync_one_invoice(db, accurate_id, client=reader, owner_token=token)

        disc = run_discovery_cycle(
            db,
            job="invoice",
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
            contract_status=INVOICE_CONTRACT_STATUS,
        )
        accurate_requests = int(disc["accurate_requests"])
        checkpoint = disc["checkpoint"]
        stopped_reason = disc.get("stopped_reason")
        ok = bool(disc["ok"])
        partial = bool(disc["partial"])
        complete = bool(disc["complete"])

        status = record_job_result(
            db,
            "invoice",
            enabled=True,
            ok=ok,
            counters=counters,
            error=None if ok and complete else (stopped_reason or "incomplete"),
            error_code=stopped_reason,
            duration_seconds=time.monotonic() - started,
            partial=partial,
            complete=complete,
            detail={
                "stopped_reason": stopped_reason,
                "failures": failures[:30],
                "accurate_requests": accurate_requests,
                "contract_status": INVOICE_CONTRACT_STATUS,
                "sort_warnings": disc.get("sort_warnings") or [],
                "checkpoint": {
                    "next_page": checkpoint.get("next_page"),
                    "backfill_complete": checkpoint.get("backfill_complete"),
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
                        "incremental_pending"
                        if disc.get("incremental_pending")
                        else "backfill_incomplete"
                    )
                ),
            },
        )
        return {
            "ok": ok,
            "job": "invoice",
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
