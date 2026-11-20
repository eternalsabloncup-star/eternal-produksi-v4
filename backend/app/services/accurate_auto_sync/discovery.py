"""Shared Accurate list discovery: backfill id|asc + incremental lastUpdate|desc.

Guarantees (within bounded cycle budgets):
- Checkpoint advances only after a full page succeeds.
- Mid-page failure keeps the same page (no skip).
- After backfill: crawl ``lastUpdate|desc`` via ``incremental_next_page``;
  each cycle also refreshes page 1 (hot window) so new/recent changes are not
  starved by a fixed 3-page window.
- COMPLETE requires ``backfill_complete`` and zero failures.

Page-number checkpoints under mutating ``lastUpdate`` order are not a perfect
snapshot; page-1 hot refresh + full incremental crawl + same-cycle dedupe are
the tested mitigations. Id-filter cursors are not used (Accurate ignores them
on SO; unproven elsewhere).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pymongo.database import Database

from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.fields import as_text
from app.services.accurate_auto_sync.checkpoint import (
    SORT_BACKFILL,
    SORT_INCREMENTAL,
    load_job_checkpoint,
    save_job_checkpoint,
)
from app.services.accurate_auto_sync.locks import AutoSyncLockError, refresh_job_lock
from app.services.accurate_auto_sync.status import INCOMPLETE_STOP_REASONS

ListFetcher = Callable[[dict[str, Any]], list[dict[str, Any]]]
SyncOne = Callable[[str], dict[str, Any]]


def list_page_params(page: int, page_size: int, *, sort: str) -> dict[str, Any]:
    return {"sp.page": page, "sp.pageSize": page_size, "sp.sort": sort}


def _ids_monotonic_asc(rows: list[dict[str, Any]]) -> bool:
    prev: int | None = None
    for row in rows:
        try:
            cur = int(as_text(row.get("id")) or "")
        except ValueError:
            return False
        if prev is not None and cur < prev:
            return False
        prev = cur
    return True


def _last_update_desc_ok(rows: list[dict[str, Any]]) -> bool | None:
    """True/False if lastUpdate present on all rows; None if field absent."""
    stamps: list[str] = []
    for row in rows:
        lu = row.get("lastUpdate")
        if lu is None or lu == "":
            return None
        stamps.append(str(lu))
    if not stamps:
        return None
    return stamps == sorted(stamps, reverse=True)


def run_discovery_cycle(
    db: Database,
    *,
    job: str,
    owner_token: str,
    fetch_list: ListFetcher,
    sync_one: SyncOne,
    counters: dict[str, int],
    failures: list[dict[str, Any]],
    page_size: int,
    max_pages: int,
    max_rows: int,
    timeout_seconds: float,
    started: float,
    contract_status: str,
) -> dict[str, Any]:
    """Run one bounded discovery cycle. Mutates counters/failures."""
    checkpoint = load_job_checkpoint(db, job, contract_status=contract_status)
    pages_this_cycle = 0
    accurate_requests = 0
    stopped_reason: str | None = None
    synced_ids: set[str] = set()
    sort_warnings: list[str] = []

    def _budget() -> str | None:
        if time.monotonic() - started > timeout_seconds:
            return "timeout"
        if counters["listed"] >= max_rows:
            return "max_rows"
        if pages_this_cycle >= max_pages:
            return "max_pages"
        return None

    def _fetch(page: int, *, sort: str) -> list[dict[str, Any]] | None:
        nonlocal accurate_requests, stopped_reason
        if not refresh_job_lock(db, job=job, owner_token=owner_token):
            raise AutoSyncLockError(f"Lease {job} hilang — abort tanpa commit baru.")
        try:
            rows = fetch_list(list_page_params(page, page_size, sort=sort))
            accurate_requests += 1
            return list(rows or [])
        except (AccurateAuthError, TimeoutError, ConnectionError, RuntimeError) as exc:
            stopped_reason = "list_failed"
            counters["failed"] += 1
            failures.append(
                {
                    "stage": "list",
                    "page": page,
                    "sort": sort,
                    "error": type(exc).__name__,
                    "code": getattr(exc, "code", "list_failed"),
                }
            )
            return None

    def _sync_rows(rows: list[dict[str, Any]], *, page: int, sort: str) -> dict[str, Any]:
        nonlocal accurate_requests
        page_failed = False
        last_id: int | None = None
        for row in rows:
            budget = _budget()
            if budget and budget != "max_pages":
                return {
                    "page_failed": True,
                    "stopped": budget,
                    "last_id": last_id,
                }
            counters["listed"] += 1
            so_id = as_text(row.get("id"))
            if not so_id:
                counters["failed"] += 1
                failures.append(
                    {"stage": "list_row", "page": page, "sort": sort, "code": "missing_id"}
                )
                page_failed = True
                break
            try:
                last_id = int(so_id)
            except ValueError:
                last_id = None
            if so_id in synced_ids:
                continue
            try:
                if not refresh_job_lock(db, job=job, owner_token=owner_token):
                    raise AutoSyncLockError("Lease hilang mid-run.")
                result = sync_one(so_id)
                accurate_requests += 1
                counters["fetched"] += 1
                key = str(result.get("outcome") or "failed")
                if key in counters:
                    counters[key] += 1
                    synced_ids.add(so_id)
                else:
                    counters["failed"] += 1
                    page_failed = True
                    failures.append(
                        {
                            "stage": "detail_or_upsert",
                            "accurate_id": so_id,
                            "page": page,
                            "code": "unknown_outcome",
                        }
                    )
                    break
            except AutoSyncLockError:
                raise
            except Exception as exc:  # noqa: BLE001
                counters["failed"] += 1
                page_failed = True
                failures.append(
                    {
                        "stage": "detail_or_upsert",
                        "accurate_id": so_id,
                        "page": page,
                        "sort": sort,
                        "error": type(exc).__name__,
                        "code": getattr(exc, "code", "sync_failed"),
                    }
                )
                break
        return {"page_failed": page_failed, "stopped": None, "last_id": last_id}

    # --- Phase A: backfill ---
    if not checkpoint.get("backfill_complete"):
        while True:
            budget = _budget()
            if budget:
                stopped_reason = budget
                break
            page = int(checkpoint["next_page"])
            rows = _fetch(page, sort=SORT_BACKFILL)
            if rows is None:
                break
            pages_this_cycle += 1
            if rows and not _ids_monotonic_asc(rows):
                sort_warnings.append("backfill_id_asc_not_monotonic")
                stopped_reason = "sort_unstable"
                counters["failed"] += 1
                failures.append(
                    {
                        "stage": "list_sort_guard",
                        "page": page,
                        "sort": SORT_BACKFILL,
                        "code": "sort_unstable",
                    }
                )
                checkpoint = save_job_checkpoint(
                    db, job, checkpoint, owner_token=owner_token, contract_status=contract_status
                )
                break
            if not rows:
                checkpoint["backfill_complete"] = True
                checkpoint["phase"] = "incremental"
                checkpoint = save_job_checkpoint(
                    db, job, checkpoint, owner_token=owner_token, contract_status=contract_status
                )
                break

            outcome = _sync_rows(rows, page=page, sort=SORT_BACKFILL)
            if outcome.get("stopped"):
                stopped_reason = str(outcome["stopped"])
                break
            if outcome.get("page_failed"):
                stopped_reason = stopped_reason or "page_failed"
                checkpoint = save_job_checkpoint(
                    db, job, checkpoint, owner_token=owner_token, contract_status=contract_status
                )
                break

            checkpoint["last_completed_page"] = page
            if outcome.get("last_id") is not None:
                checkpoint["last_completed_id"] = outcome["last_id"]
            if len(rows) < page_size:
                checkpoint["backfill_complete"] = True
                checkpoint["phase"] = "incremental"
                checkpoint["next_page"] = page
            else:
                checkpoint["next_page"] = page + 1
            checkpoint = save_job_checkpoint(
                db, job, checkpoint, owner_token=owner_token, contract_status=contract_status
            )
            if checkpoint.get("backfill_complete"):
                break
            if pages_this_cycle >= max_pages:
                stopped_reason = "max_pages"
                break

    # --- Phase B: incremental (hot page 1 + crawl) ---
    if (
        checkpoint.get("backfill_complete")
        and stopped_reason not in {"list_failed", "page_failed", "sort_unstable"}
        and counters["failed"] == 0
    ):
        checkpoint["phase"] = "incremental"

        def _inc_page(page: int) -> bool:
            """Process one incremental page. Returns False if caller should stop."""
            nonlocal stopped_reason, pages_this_cycle, checkpoint
            budget = _budget()
            if budget and budget != "max_pages":
                stopped_reason = budget
                return False
            if pages_this_cycle >= max_pages:
                return False
            rows = _fetch(page, sort=SORT_INCREMENTAL)
            if rows is None:
                return False
            pages_this_cycle += 1
            order = _last_update_desc_ok(rows)
            if order is False:
                sort_warnings.append("incremental_lastUpdate_not_desc")
                # Soft warning — continue; do not invent alternate cursor.
            if not rows:
                checkpoint["incremental_next_page"] = 1
                checkpoint["incremental_cycles_completed"] = (
                    int(checkpoint.get("incremental_cycles_completed") or 0) + 1
                )
                return False
            outcome = _sync_rows(rows, page=page, sort=SORT_INCREMENTAL)
            if outcome.get("stopped"):
                stopped_reason = str(outcome["stopped"])
                return False
            if outcome.get("page_failed"):
                stopped_reason = stopped_reason or "page_failed"
                return False
            if len(rows) < page_size:
                checkpoint["incremental_next_page"] = 1
                checkpoint["incremental_cycles_completed"] = (
                    int(checkpoint.get("incremental_cycles_completed") or 0) + 1
                )
                return False
            return True

        # Hot window: always refresh newest updates first.
        if not _inc_page(1):
            checkpoint = save_job_checkpoint(
                db, job, checkpoint, owner_token=owner_token, contract_status=contract_status
            )
        else:
            # Crawl deeper from checkpoint (skip 1 if already done this cycle).
            crawl_from = int(checkpoint.get("incremental_next_page") or 1)
            if crawl_from <= 1:
                crawl_from = 2
            page = crawl_from
            while pages_this_cycle < max_pages and stopped_reason is None:
                budget = _budget()
                if budget and budget != "max_pages":
                    stopped_reason = budget
                    break
                cont = _inc_page(page)
                if not cont:
                    break
                # Advance crawl cursor only after success.
                checkpoint["incremental_next_page"] = page + 1
                page += 1
            checkpoint = save_job_checkpoint(
                db, job, checkpoint, owner_token=owner_token, contract_status=contract_status
            )

    has_failures = counters["failed"] > 0 or bool(failures)
    list_failed = stopped_reason == "list_failed"
    backfill_done = bool(checkpoint.get("backfill_complete"))
    incomplete = not backfill_done
    if backfill_done and stopped_reason in INCOMPLETE_STOP_REASONS:
        incomplete = False
    if stopped_reason == "page_failed":
        incomplete = not backfill_done
    if stopped_reason == "sort_unstable":
        incomplete = True
    complete = backfill_done and not has_failures and not list_failed
    if stopped_reason == "sort_unstable":
        complete = False
    ok = (not list_failed) and (not has_failures) and stopped_reason != "sort_unstable"
    partial = (not complete) and (not list_failed) and (
        incomplete
        or stopped_reason in {"page_failed", "timeout", "max_pages", "max_rows", "sort_unstable"}
    )
    if complete:
        partial = False

    return {
        "ok": ok,
        "partial": partial,
        "complete": complete,
        "stopped_reason": stopped_reason,
        "accurate_requests": accurate_requests,
        "pages_this_cycle": pages_this_cycle,
        "checkpoint": checkpoint,
        "sort_warnings": sort_warnings,
        "has_failures": has_failures,
        "list_failed": list_failed,
        "backfill_done": backfill_done,
    }
