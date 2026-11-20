"""Auto-sync Delivery Order / SJ — discovery checkpoint + delivery ingest guards."""

from __future__ import annotations

import time
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.integrations.accurate.client import AccurateReadClient
from app.services.accurate_auto_sync.checkpoint import CONTRACT_DO
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
from app.services.delivery_sync import (
    DeliverySyncDisabledError,
    is_sj_sync_enabled,
    resolve_sj_mongo_guard,
    sync_delivery_order_payload,
)

SJ_CONTRACT_STATUS = CONTRACT_DO

_SYSTEM_USER = {
    "email_normalized": "system:accurate-auto-sync",
    "role": "super_admin",
}

DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_ROWS = 100
DEFAULT_TIMEOUT_SECONDS = 50.0


def sync_one_sj(
    db: Database,
    accurate_id: str,
    *,
    client: AccurateReadClient,
    owner_token: str,
    mongo_guard: str | None = None,
) -> dict[str, Any]:
    require_owns_job_lock(db, job="sj", owner_token=owner_token)
    from app.services.po_repair_maintenance import po_repair_writer_section

    with po_repair_writer_section(db) as fence:
        fence.assert_valid(db)
        detail = client.fetch_delivery_order_detail(accurate_id)
        fence.assert_valid(db)
        require_owns_job_lock(db, job="sj", owner_token=owner_token)
        from app.services.accurate_auto_sync.date_floor import detail_scope_outcome

        gated = detail_scope_outcome(detail, accurate_id=str(accurate_id))
        if gated is not None:
            return gated
        guard = mongo_guard or resolve_sj_mongo_guard()
        return sync_delivery_order_payload(
            db,
            _SYSTEM_USER,
            detail,
            mongo_guard=guard,  # type: ignore[arg-type]
            require_registered_po=True,
        )


def run_sj_auto_sync(
    db: Database,
    *,
    client: AccurateReadClient | None = None,
    mongo_guard: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Satu siklus auto-sync SJ. Memerlukan V4_SJ_SYNC_ENABLED=1.

    Production write hanya jika V4_SJ_SYNC_PRODUCTION_ENABLED=1 + guard production.
    Default tetap jalur test / fail-closed.
    """
    if not is_sj_sync_enabled():
        return {
            "ok": True,
            "job": "sj",
            "enabled": False,
            "accurate_requests": 0,
            "writes": 0,
            "status": disabled_status_view("sj"),
        }

    mark_running(db, "sj")
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
    active_guard = mongo_guard or resolve_sj_mongo_guard()

    try:
        token = acquire_job_lock(db, job="sj")
        page_size = max(1, min(int(page_size), 100))
        max_pages = max(1, min(int(max_pages), 50))
        max_rows = max(1, min(int(max_rows), 500))
        timeout_seconds = max(0.001, float(timeout_seconds))

        def _fetch_list(params: dict[str, Any]) -> list[dict[str, Any]]:
            return reader.fetch_delivery_order_list(params=params)

        def _sync_one(accurate_id: str) -> dict[str, Any]:
            return sync_one_sj(
                db,
                accurate_id,
                client=reader,
                owner_token=token,
                mongo_guard=active_guard,
            )

        disc = run_discovery_cycle(
            db,
            job="sj",
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
            contract_status=SJ_CONTRACT_STATUS,
        )
        accurate_requests = int(disc["accurate_requests"])
        checkpoint = disc["checkpoint"]
        stopped_reason = disc.get("stopped_reason")
        ok = bool(disc["ok"])
        partial = bool(disc["partial"])
        complete = bool(disc["complete"])

        status = record_job_result(
            db,
            "sj",
            enabled=True,
            ok=ok,
            counters=counters,
            error=None if ok and complete else str(stopped_reason or "sj_sync_failed"),
            error_code=stopped_reason,
            duration_seconds=time.monotonic() - started,
            partial=partial,
            complete=complete,
            detail={
                "auto_sync": True,
                "mongo_guard": active_guard,
                "production_flag": bool(
                    get_settings().sj_sync_production_enabled
                ),
                "contract_status": SJ_CONTRACT_STATUS,
                "sort_warnings": disc.get("sort_warnings") or [],
                "note": (
                    "Auto SJ default fail-closed ke guard test. Production "
                    "hanya jika V4_SJ_SYNC_PRODUCTION_ENABLED=1 + CEO."
                ),
                "failures": failures[:20],
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
            },
        )
        return {
            "ok": ok,
            "job": "sj",
            "enabled": True,
            "partial": partial,
            "complete": complete,
            "accurate_requests": accurate_requests,
            "writes": counters["created"] + counters["updated"],
            "counters": counters,
            "stopped_reason": stopped_reason,
            "failures": failures[:30],
            "checkpoint": checkpoint,
            "mongo_guard": active_guard,
            "status": status,
        }
    except DeliverySyncDisabledError as exc:
        return {
            "ok": True,
            "job": "sj",
            "enabled": False,
            "accurate_requests": 0,
            "writes": 0,
            "status": disabled_status_view("sj", detail={"message": str(exc)}),
        }
    except AutoSyncLockError as exc:
        status = record_job_result(
            db,
            "sj",
            enabled=True,
            ok=False,
            error=str(exc),
            error_code=exc.code,
            duration_seconds=time.monotonic() - started,
            complete=False,
        )
        return {
            "ok": False,
            "job": "sj",
            "enabled": True,
            "accurate_requests": 0,
            "writes": 0,
            "error": exc.code,
            "status": status,
        }
    finally:
        if token:
            release_job_lock(db, job="sj", owner_token=token)


def sj_auto_sync_settings_note() -> dict[str, Any]:
    s = get_settings()
    return {
        "sj_sync_enabled": bool(s.sj_sync_enabled),
        "sj_sync_production_enabled": bool(s.sj_sync_production_enabled),
        "resolved_mongo_guard": resolve_sj_mongo_guard(),
        "production_guard": (
            "production_flag_off_default"
            if not s.sj_sync_production_enabled
            else "production_flag_on_requires_ceo_ops"
        ),
        "contract_status": SJ_CONTRACT_STATUS,
    }
