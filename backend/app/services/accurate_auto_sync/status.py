"""Status/monitoring job auto-sync — append status dokumen, tanpa secret."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pymongo import ASCENDING
from pymongo.database import Database

STATUS_COLLECTION = "accurate_auto_sync_status"
JobName = Literal["po", "invoice", "receipt", "sj"]

REQUIRED_AUTO_SYNC_STATUS_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": STATUS_COLLECTION,
        "keys": [("job", ASCENDING)],
        "name": "uniq_auto_sync_job",
        "unique": True,
    },
]

HealthState = Literal[
    "DISABLED", "HEALTHY", "STALE", "FAILED", "RUNNING", "BLOCKED", "PARTIAL"
]

# Discovery belum selesai — jangan catat sebagai last_success penuh.
INCOMPLETE_STOP_REASONS = frozenset({"timeout", "max_pages", "max_rows"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_auto_sync_status_indexes(db: Database) -> list[str]:
    """Hanya bootstrap/test — jangan dipanggil request/worker production path."""
    created: list[str] = []
    for spec in REQUIRED_AUTO_SYNC_STATUS_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    return created


def require_auto_sync_status_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_AUTO_SYNC_STATUS_INDEX_SPECS)


def inspect_auto_sync_status_indexes(db: Database) -> dict[str, Any]:
    from app.services.index_preflight import inspect_index_specs

    return inspect_index_specs(db, REQUIRED_AUTO_SYNC_STATUS_INDEX_SPECS)


def _empty_counters() -> dict[str, int]:
    return {
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


def disabled_status_view(
    job: JobName, *, blocked: bool = False, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Status in-memory untuk kill switch OFF — tanpa write Mongo."""
    return {
        "job": job,
        "enabled": False,
        "health": "DISABLED",
        "running": False,
        "counters": _empty_counters(),
        "consecutive_failures": 0,
        "blocked": bool(blocked),
        "detail": dict(detail or {"message": "Kill switch OFF — zero Accurate / zero write."}),
        "persisted": False,
    }


def mark_running(db: Database, job: JobName) -> dict[str, Any]:
    """Hanya untuk job yang ENABLED. Jangan panggil saat kill switch OFF."""
    now = _now()
    doc = {
        "job": job,
        "enabled": True,
        "health": "RUNNING",
        "last_attempt_at": now,
        "running": True,
        "updated_at": now,
    }
    db[STATUS_COLLECTION].update_one(
        {"job": job},
        {
            "$set": doc,
            "$setOnInsert": {"created_at": now, "consecutive_failures": 0},
        },
        upsert=True,
    )
    return get_job_status(db, job) or doc


def record_job_result(
    db: Database,
    job: JobName,
    *,
    enabled: bool,
    ok: bool,
    counters: dict[str, int] | None = None,
    error: str | None = None,
    error_code: str | None = None,
    duration_seconds: float = 0.0,
    blocked: bool = False,
    partial: bool = False,
    complete: bool = True,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist hasil job ENABLED.

    last_success_at hanya untuk siklus lengkap sukses (ok + complete + not partial).
    Detail failure / incomplete discovery → bukan HEALTHY.
    """
    now = _now()
    existing = db[STATUS_COLLECTION].find_one({"job": job}) or {}
    consecutive = int(existing.get("consecutive_failures") or 0)
    counters_merged = {**_empty_counters(), **(counters or {})}
    failed_count = int(counters_merged.get("failed") or 0)

    full_success = bool(ok and complete and not partial and failed_count == 0 and enabled)
    if full_success:
        consecutive = 0
    elif enabled:
        consecutive += 1

    if not enabled:
        health: HealthState = "DISABLED"
    elif blocked:
        health = "BLOCKED"
    elif failed_count > 0 or (not ok and not partial):
        health = "FAILED"
    elif partial or not complete:
        health = "PARTIAL"
    elif full_success:
        health = "HEALTHY"
    else:
        health = "FAILED"

    payload = {
        "job": job,
        "enabled": bool(enabled),
        "health": health,
        "running": False,
        "last_attempt_at": existing.get("last_attempt_at") or now,
        "last_finished_at": now,
        "last_success_at": now if full_success else existing.get("last_success_at"),
        "last_error": error,
        "last_error_code": error_code,
        "duration_seconds": round(float(duration_seconds), 3),
        "counters": counters_merged,
        "consecutive_failures": consecutive,
        "blocked": bool(blocked),
        "partial": bool(partial),
        "complete": bool(complete and not partial),
        "detail": dict(detail or {}),
        "updated_at": now,
        "persisted": True,
    }
    db[STATUS_COLLECTION].update_one(
        {"job": job},
        {"$set": payload, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return get_job_status(db, job) or payload


def get_job_status(db: Database, job: JobName) -> dict[str, Any] | None:
    doc = db[STATUS_COLLECTION].find_one({"job": job}, {"_id": 0})
    return doc


def list_job_statuses(
    db: Database,
    *,
    enabled_flags: dict[str, bool],
    interval_seconds: int,
    stale_after_seconds: int | None = None,
) -> dict[str, Any]:
    """Gabungkan status tersimpan + flag runtime (tanpa secret)."""
    stale_after = stale_after_seconds or max(180, int(interval_seconds) * 3)
    now = _now()
    jobs: list[dict[str, Any]] = []
    for job in ("po", "invoice", "receipt", "sj"):
        enabled = bool(enabled_flags.get(job, False))
        if not enabled:
            jobs.append(disabled_status_view(job))  # type: ignore[arg-type]
            continue
        stored = get_job_status(db, job) or {
            "job": job,
            "enabled": True,
            "health": "FAILED",
            "counters": _empty_counters(),
            "consecutive_failures": 0,
            "persisted": False,
        }
        health = str(stored.get("health") or "FAILED")
        if health == "HEALTHY" and stored.get("last_success_at"):
            last = stored["last_success_at"]
            if isinstance(last, datetime):
                age = (now - last).total_seconds()
                if age > stale_after:
                    health = "STALE"
        stored = {**stored, "enabled": True, "health": health}
        jobs.append(stored)
    return {
        "interval_seconds": int(interval_seconds),
        "jobs": jobs,
        "manual_sync_button": False,
    }
