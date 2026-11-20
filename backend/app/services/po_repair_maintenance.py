"""Exclusive maintenance gate untuk remediasi branch_id PO.

Ditegakkan di writer relevan (accept / derive task / allocation / auto-sync
PO+SJ). Bukan transaksi lintas koleksi — isolasi via lease Mongo eksklusif.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.services.accurate_auto_sync.locks import (
    LOCKS_COLLECTION,
    acquire_job_lock,
    release_job_lock,
)

MAINTENANCE_LOCK_ID = "po_branch_repair_maintenance"
HELD_JOBS = ("po", "sj")  # writer Accurate yang dapat membentuk SJ / mengubah PO
DEFAULT_TTL_SECONDS = 3600


class PoRepairMaintenanceError(RuntimeError):
    """Writer ditolak karena remediasi branch_id sedang aktif."""

    code = "PO_BRANCH_REPAIR_MAINTENANCE"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_po_repair_maintenance_active(db: Database) -> bool:
    doc = db[LOCKS_COLLECTION].find_one({"_id": MAINTENANCE_LOCK_ID})
    if doc is None:
        return False
    expires = doc.get("expires_at")
    if isinstance(expires, datetime) and _as_utc(expires) <= _now():
        return False
    return True


def refuse_if_po_repair_maintenance(db: Database) -> None:
    if is_po_repair_maintenance_active(db):
        raise PoRepairMaintenanceError(
            "Operasi ditolak: remediasi branch_id PO sedang dalam maintenance isolation."
        )


def acquire_po_repair_maintenance(
    db: Database,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    actor: str = "po_branch_repair_apply",
) -> dict[str, Any]:
    """Ambil lease maintenance + tahan job lock PO/SJ agar auto-sync tidak jalan."""
    coll = db[LOCKS_COLLECTION]
    now = _now()
    expires = now + timedelta(seconds=max(60, int(ttl_seconds)))
    token = secrets.token_hex(16)
    job_tokens: dict[str, str] = {}

    # Tahan job lock dulu — gagal bila sync sedang aktif.
    try:
        for job in HELD_JOBS:
            job_tokens[job] = acquire_job_lock(
                db, job=job, ttl_seconds=ttl_seconds
            )
    except Exception:
        for job, jt in job_tokens.items():
            release_job_lock(db, job=job, owner_token=jt)
        raise

    try:
        coll.insert_one(
            {
                "_id": MAINTENANCE_LOCK_ID,
                "kind": "po_branch_repair",
                "owner_token": token,
                "actor": actor,
                "started_at": now,
                "expires_at": expires,
                "held_jobs": list(HELD_JOBS),
            }
        )
    except DuplicateKeyError:
        stolen = coll.find_one_and_update(
            {"_id": MAINTENANCE_LOCK_ID, "expires_at": {"$lte": now}},
            {
                "$set": {
                    "kind": "po_branch_repair",
                    "owner_token": token,
                    "actor": actor,
                    "started_at": now,
                    "expires_at": expires,
                    "held_jobs": list(HELD_JOBS),
                }
            },
        )
        if stolen is None:
            for job, jt in job_tokens.items():
                release_job_lock(db, job=job, owner_token=jt)
            raise PoRepairMaintenanceError(
                "Maintenance remediasi branch_id sudah aktif (lease belum expired)."
            )

    return {
        "owner_token": token,
        "job_tokens": job_tokens,
        "expires_at": expires.isoformat(),
    }


def release_po_repair_maintenance(
    db: Database,
    *,
    owner_token: str,
    job_tokens: dict[str, str] | None = None,
) -> bool:
    result = db[LOCKS_COLLECTION].delete_one(
        {"_id": MAINTENANCE_LOCK_ID, "owner_token": owner_token}
    )
    for job, jt in (job_tokens or {}).items():
        release_job_lock(db, job=job, owner_token=jt)
    return result.deleted_count == 1


def refresh_po_repair_maintenance(
    db: Database,
    *,
    owner_token: str,
    job_tokens: dict[str, str] | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    from app.services.accurate_auto_sync.locks import refresh_job_lock

    now = _now()
    expires = now + timedelta(seconds=max(60, int(ttl_seconds)))
    result = db[LOCKS_COLLECTION].update_one(
        {"_id": MAINTENANCE_LOCK_ID, "owner_token": owner_token},
        {"$set": {"expires_at": expires}},
    )
    if result.matched_count != 1:
        return False
    for job, jt in (job_tokens or {}).items():
        if not refresh_job_lock(db, job=job, owner_token=jt, ttl_seconds=ttl_seconds):
            return False
    return True
