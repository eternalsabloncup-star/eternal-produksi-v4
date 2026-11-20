"""Exclusive maintenance gate untuk remediasi branch_id PO.

Isolasi efektif (bukan cek-sebelum-operasi saja):
- Writer memegang *writer lease* selama critical section.
- Maintenance refuse jika ada lease writer aktif (drain / fail-closed).
- Writer refuse jika maintenance sudah aktif (cek ulang setelah insert lease).

Bukan transaksi lintas koleksi — fence via dokumen di ``sync_locks``.
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.services.accurate_auto_sync.locks import (
    LOCKS_COLLECTION,
    acquire_job_lock,
    release_job_lock,
)

MAINTENANCE_LOCK_ID = "po_branch_repair_maintenance"
WRITER_KIND = "po_repair_writer"
WRITER_ID_PREFIX = "po_repair_writer:"
HELD_JOBS = ("po", "sj")
DEFAULT_TTL_SECONDS = 3600
DEFAULT_WRITER_TTL_SECONDS = 300


class PoRepairMaintenanceError(RuntimeError):
    """Writer ditolak atau maintenance tidak dapat dibuktikan eksklusif."""

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
    """Cek cepat — tidak cukup untuk exclusivity; gunakan ``po_repair_writer_section``."""
    if is_po_repair_maintenance_active(db):
        raise PoRepairMaintenanceError(
            "Operasi ditolak: remediasi branch_id PO sedang dalam maintenance isolation."
        )


def _purge_expired_writer_leases(db: Database, now: datetime) -> None:
    db[LOCKS_COLLECTION].delete_many(
        {"kind": WRITER_KIND, "expires_at": {"$lte": now}}
    )


def count_active_po_repair_writers(db: Database) -> int:
    now = _now()
    _purge_expired_writer_leases(db, now)
    return db[LOCKS_COLLECTION].count_documents(
        {"kind": WRITER_KIND, "expires_at": {"$gt": now}}
    )


def enter_po_repair_writer_section(
    db: Database, *, ttl_seconds: int = DEFAULT_WRITER_TTL_SECONDS
) -> str:
    """Ambil writer lease; gagal jika maintenance aktif (termasuk race post-insert)."""
    refuse_if_po_repair_maintenance(db)
    now = _now()
    token = secrets.token_hex(16)
    db[LOCKS_COLLECTION].insert_one(
        {
            "_id": f"{WRITER_ID_PREFIX}{token}",
            "kind": WRITER_KIND,
            "owner_token": token,
            "started_at": now,
            "expires_at": now + timedelta(seconds=max(30, int(ttl_seconds))),
        }
    )
    if is_po_repair_maintenance_active(db):
        db[LOCKS_COLLECTION].delete_one({"_id": f"{WRITER_ID_PREFIX}{token}"})
        raise PoRepairMaintenanceError(
            "Operasi ditolak: remediasi branch_id PO sedang dalam maintenance isolation."
        )
    return token


def exit_po_repair_writer_section(db: Database, owner_token: str) -> None:
    db[LOCKS_COLLECTION].delete_one({"_id": f"{WRITER_ID_PREFIX}{owner_token}"})


@contextmanager
def po_repair_writer_section(
    db: Database, *, ttl_seconds: int = DEFAULT_WRITER_TTL_SECONDS
) -> Iterator[str]:
    token = enter_po_repair_writer_section(db, ttl_seconds=ttl_seconds)
    try:
        yield token
    finally:
        exit_po_repair_writer_section(db, token)


def acquire_po_repair_maintenance(
    db: Database,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    actor: str = "po_branch_repair_apply",
) -> dict[str, Any]:
    """Lease maintenance + job lock PO/SJ; refuse jika writer in-flight."""
    coll = db[LOCKS_COLLECTION]
    now = _now()
    expires = now + timedelta(seconds=max(60, int(ttl_seconds)))
    token = secrets.token_hex(16)
    job_tokens: dict[str, str] = {}

    try:
        for job in HELD_JOBS:
            job_tokens[job] = acquire_job_lock(
                db, job=job, ttl_seconds=ttl_seconds
            )
    except Exception:
        for job, jt in job_tokens.items():
            release_job_lock(db, job=job, owner_token=jt)
        raise

    def _abort_jobs() -> None:
        for job, jt in job_tokens.items():
            release_job_lock(db, job=job, owner_token=jt)

    # Drain check: writer yang sudah lewat gate harus memegang lease.
    if count_active_po_repair_writers(db) > 0:
        _abort_jobs()
        raise PoRepairMaintenanceError(
            "writers_in_flight: tidak dapat membuktikan isolasi — STOP tanpa write"
        )

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
            _abort_jobs()
            raise PoRepairMaintenanceError(
                "Maintenance remediasi branch_id sudah aktif (lease belum expired)."
            )

    # Post-acquire fence: writer yang race-insert lease setelah kita → abort.
    if count_active_po_repair_writers(db) > 0:
        coll.delete_one({"_id": MAINTENANCE_LOCK_ID, "owner_token": token})
        _abort_jobs()
        raise PoRepairMaintenanceError(
            "writers_in_flight_after_acquire: isolasi tidak terbukti — STOP tanpa write"
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
