"""Exclusive maintenance + transactional writer fencing untuk remediasi branch_id.

Invariant:
  Tidak ada jendela di mana maintenance punya hak repair sementara writer
  (lama/baru) masih bisa commit mutasi bertentangan.

Desain (bukan TTL/pre-check saja):
  1. Writer memegang lease + epoch.
  2. Mutasi wajib lewat ``mutate_under_writer_fence``: renew lease + cek
     maintenance **dalam transaksi Mongo yang sama** dengan write.
  3. Maintenance: blokir unexpired writers → ambil lock → bump epoch → hapus
     lease. Writer yang sudah lewat assert di luar txn gagal di commit boundary.
  4. Expired lease ≠ bukti proses mati; epoch bump mematikan stale resume.
"""

from __future__ import annotations

import contextvars
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, TypeVar

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.services.accurate_auto_sync.locks import (
    LOCKS_COLLECTION,
    acquire_job_lock,
    release_job_lock,
)

MAINTENANCE_LOCK_ID = "po_branch_repair_maintenance"
FENCE_ID = "po_branch_repair_fence"
WRITER_KIND = "po_repair_writer"
WRITER_ID_PREFIX = "po_repair_writer:"
HELD_JOBS = ("po", "sj")
DEFAULT_TTL_SECONDS = 3600
DEFAULT_WRITER_TTL_SECONDS = 300

T = TypeVar("T")


class PoRepairMaintenanceError(RuntimeError):
    """Writer ditolak atau maintenance tidak dapat dibuktikan eksklusif."""

    code = "PO_BRANCH_REPAIR_MAINTENANCE"


class WriterFenceLostError(PoRepairMaintenanceError):
    """Lease/epoch hilang — writer wajib fail-closed (tidak boleh commit)."""

    code = "PO_REPAIR_WRITER_FENCE_LOST"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ensure_fence_doc(db: Database) -> dict[str, Any]:
    coll = db[LOCKS_COLLECTION]
    doc = coll.find_one({"_id": FENCE_ID})
    if doc is not None:
        return doc
    try:
        coll.insert_one({"_id": FENCE_ID, "kind": "po_repair_fence", "epoch": 0})
    except DuplicateKeyError:
        pass
    doc = coll.find_one({"_id": FENCE_ID})
    assert doc is not None
    return doc


def current_fence_epoch(db: Database, *, session: Any | None = None) -> int:
    if session is None:
        return int(_ensure_fence_doc(db).get("epoch") or 0)
    doc = db[LOCKS_COLLECTION].find_one({"_id": FENCE_ID}, session=session)
    if doc is None:
        return 0
    return int(doc.get("epoch") or 0)


def is_po_repair_maintenance_active(
    db: Database, *, session: Any | None = None
) -> bool:
    doc = db[LOCKS_COLLECTION].find_one(
        {"_id": MAINTENANCE_LOCK_ID}, session=session
    )
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


def count_unexpired_po_repair_writers(db: Database) -> int:
    now = _now()
    return db[LOCKS_COLLECTION].count_documents(
        {"kind": WRITER_KIND, "expires_at": {"$gt": now}}
    )


count_active_po_repair_writers = count_unexpired_po_repair_writers


@dataclass(frozen=True)
class WriterFence:
    token: str
    epoch: int
    ttl_seconds: int

    def assert_valid(self, db: Database, *, session: Any | None = None) -> None:
        """Renew lease. Bukan jaminan commit — gunakan mutate_under_writer_fence."""
        renew_po_repair_writer_lease(db, self, session=session)


_active_writer_fence: contextvars.ContextVar[WriterFence | None] = contextvars.ContextVar(
    "po_repair_active_writer_fence", default=None
)
_active_writer_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "po_repair_active_writer_depth", default=0
)


def active_writer_fence() -> WriterFence | None:
    """Fence aktif di context (jika ada) — untuk renew di dalam txn writer lain."""
    return _active_writer_fence.get()



def enter_po_repair_writer_section(
    db: Database, *, ttl_seconds: int = DEFAULT_WRITER_TTL_SECONDS
) -> WriterFence:
    existing = _active_writer_fence.get()
    if existing is not None:
        still = db[LOCKS_COLLECTION].find_one(
            {"_id": f"{WRITER_ID_PREFIX}{existing.token}"}
        )
        if still is None:
            _active_writer_fence.set(None)
            _active_writer_depth.set(0)
        else:
            _active_writer_depth.set(_active_writer_depth.get() + 1)
            return existing

    refuse_if_po_repair_maintenance(db)
    epoch = current_fence_epoch(db)
    now = _now()
    ttl = max(30, int(ttl_seconds))
    token = secrets.token_hex(16)
    db[LOCKS_COLLECTION].insert_one(
        {
            "_id": f"{WRITER_ID_PREFIX}{token}",
            "kind": WRITER_KIND,
            "owner_token": token,
            "epoch": epoch,
            "started_at": now,
            "expires_at": now + timedelta(seconds=ttl),
        }
    )
    if is_po_repair_maintenance_active(db) or current_fence_epoch(db) != epoch:
        db[LOCKS_COLLECTION].delete_one({"_id": f"{WRITER_ID_PREFIX}{token}"})
        raise PoRepairMaintenanceError(
            "Operasi ditolak: remediasi branch_id PO sedang dalam maintenance isolation."
        )
    fence = WriterFence(token=token, epoch=epoch, ttl_seconds=ttl)
    _active_writer_fence.set(fence)
    _active_writer_depth.set(1)
    return fence


def exit_po_repair_writer_section(
    db: Database, fence: WriterFence | str
) -> None:
    token = fence.token if isinstance(fence, WriterFence) else fence
    depth = _active_writer_depth.get()
    active = _active_writer_fence.get()
    if active is not None and active.token == token and depth > 1:
        _active_writer_depth.set(depth - 1)
        return
    if active is not None and active.token == token:
        _active_writer_fence.set(None)
        _active_writer_depth.set(0)
    db[LOCKS_COLLECTION].delete_one({"_id": f"{WRITER_ID_PREFIX}{token}"})


def renew_po_repair_writer_lease(
    db: Database,
    fence: WriterFence,
    *,
    session: Any | None = None,
) -> None:
    if is_po_repair_maintenance_active(db, session=session):
        raise WriterFenceLostError("writer_fence_lost:maintenance_active")
    if current_fence_epoch(db, session=session) != fence.epoch:
        raise WriterFenceLostError("writer_fence_lost:epoch_mismatch")

    now = _now()
    new_exp = now + timedelta(seconds=max(30, int(fence.ttl_seconds)))
    result = db[LOCKS_COLLECTION].update_one(
        {
            "_id": f"{WRITER_ID_PREFIX}{fence.token}",
            "owner_token": fence.token,
            "epoch": fence.epoch,
            "expires_at": {"$gt": now},
        },
        {"$set": {"expires_at": new_exp, "renewed_at": now}},
        session=session,
    )
    if result.matched_count != 1:
        raise WriterFenceLostError("writer_fence_lost:lease_expired_or_replaced")


def mutate_under_writer_fence(
    db: Database,
    fence: WriterFence,
    operation: Callable[[Any], T],
    *,
    attempts: int = 8,
) -> T:
    """Commit boundary: renew fence + mutasi dalam satu transaksi Mongo.

    ``operation(session)`` melakukan write dengan session yang sama.
    Maintenance acquire / epoch bump di tengah → renew gagal atau commit conflict.
    """
    from app.repositories import task_repository as tasks

    def _op(session: Any) -> T:
        renew_po_repair_writer_lease(db, fence, session=session)
        if is_po_repair_maintenance_active(db, session=session):
            raise WriterFenceLostError("writer_fence_lost:maintenance_active")
        return operation(session)

    return tasks.run_in_transaction(db, _op, attempts=attempts)


def require_maintenance_owner(
    db: Database,
    *,
    owner_token: str,
    session: Any | None = None,
) -> None:
    """Repair write: buktikan lease maintenance masih milik kita."""
    now = _now()
    result = db[LOCKS_COLLECTION].update_one(
        {
            "_id": MAINTENANCE_LOCK_ID,
            "owner_token": owner_token,
            "expires_at": {"$gt": now},
        },
        {"$set": {"last_repair_touch_at": now}},
        session=session,
    )
    if result.matched_count != 1:
        raise PoRepairMaintenanceError(
            "maintenance_lease_lost: STOP — tidak dapat membuktikan eksklusivitas"
        )


@contextmanager
def po_repair_writer_section(
    db: Database, *, ttl_seconds: int = DEFAULT_WRITER_TTL_SECONDS
) -> Iterator[WriterFence]:
    fence = enter_po_repair_writer_section(db, ttl_seconds=ttl_seconds)
    try:
        yield fence
    finally:
        exit_po_repair_writer_section(db, fence)


def acquire_po_repair_maintenance(
    db: Database,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    actor: str = "po_branch_repair_apply",
) -> dict[str, Any]:
    coll = db[LOCKS_COLLECTION]
    now = _now()
    expires = now + timedelta(seconds=max(60, int(ttl_seconds)))
    token = secrets.token_hex(16)
    job_tokens: dict[str, str] = {}
    _ensure_fence_doc(db)

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

    # Writer dengan lease masih valid memblokir — jangan andalkan TTL sebagai drain.
    if count_unexpired_po_repair_writers(db) > 0:
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

    fence_upd = coll.find_one_and_update(
        {"_id": FENCE_ID},
        {"$inc": {"epoch": 1}, "$set": {"bumped_at": now, "bumped_by": actor}},
        return_document=ReturnDocument.AFTER,
    )
    if fence_upd is None:
        coll.delete_one({"_id": MAINTENANCE_LOCK_ID, "owner_token": token})
        _abort_jobs()
        raise PoRepairMaintenanceError("fence_epoch_bump_failed")

    new_epoch = int(fence_upd.get("epoch") or 0)
    # Setelah epoch bump: orphan/expired + lease invalid boleh dibersihkan.
    # Writer hidup yang masih di luar txn gagal di mutate_under_writer_fence.
    coll.delete_many({"kind": WRITER_KIND})

    if count_unexpired_po_repair_writers(db) > 0:
        coll.delete_one({"_id": MAINTENANCE_LOCK_ID, "owner_token": token})
        _abort_jobs()
        raise PoRepairMaintenanceError(
            "writers_in_flight_after_acquire: isolasi tidak terbukti — STOP tanpa write"
        )

    return {
        "owner_token": token,
        "job_tokens": job_tokens,
        "expires_at": expires.isoformat(),
        "fence_epoch": new_epoch,
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
        {
            "_id": MAINTENANCE_LOCK_ID,
            "owner_token": owner_token,
            "expires_at": {"$gt": now},
        },
        {"$set": {"expires_at": expires}},
    )
    if result.matched_count != 1:
        return False
    for job, jt in (job_tokens or {}).items():
        if not refresh_job_lock(db, job=job, owner_token=jt, ttl_seconds=ttl_seconds):
            return False
    return True
