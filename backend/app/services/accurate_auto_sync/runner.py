"""Dispatcher one-shot job auto-sync Accurate."""

from __future__ import annotations

from typing import Any, Literal

from pymongo.database import Database

from app.config import get_settings
from app.services.accurate_auto_sync import po_job, receipt_job, sj_job
from app.services.accurate_auto_sync.status import list_job_statuses

JobName = Literal["po", "receipt", "sj"]
JOB_NAMES: tuple[JobName, ...] = ("po", "receipt", "sj")


def run_auto_sync_job(db: Database, job: str) -> dict[str, Any]:
    name = str(job or "").strip().lower()
    if name == "po":
        return po_job.run_po_auto_sync(db)
    if name == "receipt":
        return receipt_job.run_receipt_auto_sync(db)
    if name == "sj":
        return sj_job.run_sj_auto_sync(db)
    if name == "all":
        results = {j: run_auto_sync_job(db, j) for j in JOB_NAMES}
        return {"ok": all(bool(r.get("ok")) for r in results.values()), "jobs": results}
    raise ValueError(f"Job tidak dikenal: {job!r}. Gunakan po|receipt|sj|all.")


def monitoring_snapshot(db: Database) -> dict[str, Any]:
    settings = get_settings()
    return list_job_statuses(
        db,
        enabled_flags={
            "po": bool(settings.po_auto_sync_enabled),
            "receipt": bool(settings.receipt_auto_sync_enabled),
            "sj": bool(settings.sj_sync_enabled),
        },
        interval_seconds=int(settings.accurate_auto_sync_interval_seconds),
    )
