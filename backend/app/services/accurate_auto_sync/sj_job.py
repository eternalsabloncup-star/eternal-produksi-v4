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
from app.services.accurate_auto_sync.status import mark_running, record_job_result
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
    enabled = is_sj_sync_enabled()
    mark_running(db, "sj", enabled=enabled)
    if not enabled:
        status = record_job_result(
            db,
            "sj",
            enabled=False,
            ok=True,
            duration_seconds=0.0,
            detail={"message": "Kill switch OFF — zero Accurate / zero write."},
        )
        return {
            "ok": True,
            "job": "sj",
            "enabled": False,
            "accurate_requests": 0,
            "writes": 0,
            "status": status,
        }

    started = time.monotonic()
    token = acquire_job_lock(db, job="sj")
    try:
        # Production guard delivery_sync tetap memaksa mongo_guard=test.
        # Auto-sync production memerlukan keputusan CEO + jalur guard terpisah.
        result = sync_delivery_orders_from_accurate(
            db,
            _SYSTEM_USER,
            client=client,
            mongo_guard="test",  # type: ignore[arg-type]
            scope="auto",
        )
        # delivery_sync memakai lock sendiri — kita pakai lease job terpisah
        # untuk koordinasi monitoring; pastikan tidak bentrok scope.
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
        ok = bool(result.get("ok"))
        status = record_job_result(
            db,
            "sj",
            enabled=True,
            ok=ok,
            counters=counters,
            error=None if ok else str(result.get("stopped_reason") or "sj_sync_failed"),
            error_code=result.get("stopped_reason"),
            duration_seconds=time.monotonic() - started,
            detail={
                "auto_sync": True,
                "mongo_guard": "test",
                "note": (
                    "Auto SJ memakai guard test existing; aktivasi production "
                    "memerlukan keputusan CEO + jalur guard terpisah."
                ),
                "failures": (result.get("failures") or [])[:20],
            },
        )
        return {
            "ok": ok,
            "job": "sj",
            "enabled": True,
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
        status = record_job_result(
            db,
            "sj",
            enabled=False,
            ok=True,
            duration_seconds=time.monotonic() - started,
            detail={"message": str(exc)},
        )
        return {
            "ok": True,
            "job": "sj",
            "enabled": False,
            "accurate_requests": 0,
            "writes": 0,
            "status": status,
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
        release_job_lock(db, job="sj", owner_token=token)


def sj_auto_sync_settings_note() -> dict[str, Any]:
    s = get_settings()
    return {
        "sj_sync_enabled": bool(s.sj_sync_enabled),
        "production_guard": "test_only_until_ceo_approval",
    }
