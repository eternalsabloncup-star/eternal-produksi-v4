"""Auto-sync Delivery Order / SJ — membungkus delivery_sync, kill switch OFF."""

from __future__ import annotations

import time
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.integrations.accurate.client import AccurateReadClient
from app.services.accurate_auto_sync.locks import (
    AutoSyncLockError,
    acquire_job_lock,
    release_job_lock,
)
from app.services.accurate_auto_sync.status import (
    INCOMPLETE_STOP_REASONS,
    disabled_status_view,
    mark_running,
    record_job_result,
)
from app.services.delivery_sync import (
    DeliverySyncDisabledError,
    is_sj_sync_enabled,
    sync_delivery_orders_from_accurate,
)

# User sistem untuk audit ingest SJ (bukan aktor manusia).
_SYSTEM_USER = {
    "email_normalized": "system:accurate-auto-sync",
    "role": "super_admin",
}


def run_sj_auto_sync(
    db: Database,
    *,
    client: AccurateReadClient | None = None,
    mongo_guard: str = "test",
) -> dict[str, Any]:
    """Satu siklus auto-sync SJ. Memerlukan V4_SJ_SYNC_ENABLED=1."""
    if not is_sj_sync_enabled():
        # ZERO WRITE: tanpa lock, status mutation, Accurate HTTP.
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
    try:
        token = acquire_job_lock(db, job="sj")
        result = sync_delivery_orders_from_accurate(
            db,
            _SYSTEM_USER,
            client=client,
            mongo_guard="test",  # type: ignore[arg-type]
            scope="auto",
        )
        counters = {
            "listed": int(result.get("listed") or 0),
            "fetched": int(result.get("listed") or 0),
            "created": int(result.get("created") or 0),
            "updated": int(result.get("updated") or 0),
            "unchanged": int(result.get("unchanged") or 0),
            "skipped": int(result.get("skipped") or 0),
            "review_required": 0,
            "failed": int(result.get("failed") or 0),
        }
        stopped = result.get("stopped_reason")
        incomplete = stopped in INCOMPLETE_STOP_REASONS
        has_failures = counters["failed"] > 0
        list_failed = stopped == "list_failed"
        complete = stopped is None and not incomplete
        ok = bool(result.get("ok")) and not has_failures and not list_failed
        if has_failures or list_failed:
            ok = False

        status = record_job_result(
            db,
            "sj",
            enabled=True,
            ok=ok,
            counters=counters,
            error=None if ok and complete else str(stopped or "sj_sync_failed"),
            error_code=stopped,
            duration_seconds=time.monotonic() - started,
            partial=incomplete and not list_failed,
            complete=complete and not has_failures,
            detail={
                "auto_sync": True,
                "mongo_guard": "test",
                "note": (
                    "Auto SJ memakai guard test existing; aktivasi production "
                    "memerlukan keputusan CEO + jalur guard terpisah."
                ),
                "failures": (result.get("failures") or [])[:20],
                "discovery_complete": complete,
            },
        )
        return {
            "ok": ok,
            "job": "sj",
            "enabled": True,
            "partial": incomplete and not list_failed,
            "complete": complete and not has_failures,
            "accurate_requests": counters["listed"] + int(result.get("pages") or 0),
            "writes": counters["created"] + counters["updated"],
            "counters": counters,
            "result": {
                k: result.get(k)
                for k in (
                    "ok",
                    "stopped_reason",
                    "pages",
                    "listed",
                    "created",
                    "updated",
                    "unchanged",
                    "skipped",
                    "failed",
                )
            },
            "status": status,
        }
    except DeliverySyncDisabledError as exc:
        # Race: flag dimatikan setelah check awal — jangan tulis status sebagai enabled success.
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
        "production_guard": "test_only_until_ceo_approval",
    }
