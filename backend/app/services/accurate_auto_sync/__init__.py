"""Accurate auto-sync coordinator — one-shot jobs, kill switch default OFF.

Scheduler eksternal (systemd timer / cron) memanggil worker CLI. FastAPI
tidak menjalankan polling 60s. Semua job fail-closed bila flag OFF.
"""

from __future__ import annotations

from app.services.accurate_auto_sync.runner import JOB_NAMES, run_auto_sync_job
from app.services.accurate_auto_sync.status import list_job_statuses

__all__ = ["JOB_NAMES", "run_auto_sync_job", "list_job_statuses"]
