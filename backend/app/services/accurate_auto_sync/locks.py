"""Lease lock multi-process untuk job auto-sync Accurate."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

LOCKS_COLLECTION = "sync_locks"
DEFAULT_TTL_SECONDS = 600


class AutoSyncLockError(RuntimeError):
    code = "SYNC_IN_PROGRESS"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def acquire_job_lock(
    db: Database,
    *,
    job: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Ambil lease; kembalikan owner_token. Gagal → AutoSyncLockError."""
    lock_id = f"accurate_auto_sync:{job}"
    now = _now()
    expires = now + timedelta(seconds=max(30, int(ttl_seconds)))
    token = secrets.token_hex(16)
    coll = db[LOCKS_COLLECTION]
    try:
        coll.insert_one(
            {
                "_id": lock_id,
                "job": job,
                "owner_token": token,
                "started_at": now,
                "expires_at": expires,
            }
        )
        return token
    except DuplicateKeyError:
        pass

    stolen = coll.find_one_and_update(
        {"_id": lock_id, "expires_at": {"$lte": now}},
        {
            "$set": {
                "job": job,
                "owner_token": token,
                "started_at": now,
                "expires_at": expires,
            }
        },
    )
    if stolen is None:
        raise AutoSyncLockError(
            f"Job auto-sync '{job}' sedang berjalan (lease aktif)."
        )
    return token


def release_job_lock(db: Database, *, job: str, owner_token: str) -> bool:
    """Lepas lease hanya jika owner_token cocok. True bila berhasil."""
    lock_id = f"accurate_auto_sync:{job}"
    result = db[LOCKS_COLLECTION].delete_one(
        {"_id": lock_id, "owner_token": owner_token}
    )
    return result.deleted_count == 1


def refresh_job_lock(
    db: Database, *, job: str, owner_token: str, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> bool:
    """Perpanjang lease jika masih milik token ini."""
    lock_id = f"accurate_auto_sync:{job}"
    now = _now()
    expires = now + timedelta(seconds=max(30, int(ttl_seconds)))
    result = db[LOCKS_COLLECTION].update_one(
        {"_id": lock_id, "owner_token": owner_token},
        {"$set": {"expires_at": expires}},
    )
    return result.matched_count == 1


def lock_snapshot(db: Database, job: str) -> dict[str, Any] | None:
    doc = db[LOCKS_COLLECTION].find_one(
        {"_id": f"accurate_auto_sync:{job}"}, {"_id": 0, "owner_token": 0}
    )
    return doc
