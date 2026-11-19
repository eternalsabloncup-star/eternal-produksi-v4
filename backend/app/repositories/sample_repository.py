"""Sampel cetak per alokasi operator — berversi, tidak dipakai bersama."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

SAMPLES_COLLECTION = "operator_samples"

STATUS_PENDING = "menunggu_approval"
STATUS_APPROVED = "disetujui"
STATUS_REJECTED = "ditolak"

_INDEXED_DATABASES: set[str] = set()


def samples_collection(db: Database) -> Collection:
    return db[SAMPLES_COLLECTION]


def ensure_sample_indexes(db: Database) -> list[str]:
    created = [
        samples_collection(db).create_index(
            [("allocation_id", ASCENDING), ("version", DESCENDING)],
            name="uniq_sample_allocation_version",
            unique=True,
        ),
        samples_collection(db).create_index(
            [("accurate_line_id", ASCENDING), ("status", ASCENDING)],
            name="idx_sample_line_status",
        ),
    ]
    _INDEXED_DATABASES.add(db.name)
    return created


def _ensure_once(db: Database) -> None:
    if db.name not in _INDEXED_DATABASES:
        ensure_sample_indexes(db)


def latest_sample(db: Database, allocation_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return samples_collection(db).find_one(
        {"allocation_id": allocation_id}, {"_id": 0}, sort=[("version", DESCENDING)]
    )


def list_samples(db: Database, allocation_id: str) -> list[dict[str, Any]]:
    _ensure_once(db)
    return list(
        samples_collection(db)
        .find({"allocation_id": allocation_id}, {"_id": 0})
        .sort("version", ASCENDING)
    )


def create_sample(
    db: Database,
    *,
    allocation_id: str,
    accurate_po_id: str,
    accurate_line_id: str,
    evidence_id: str,
    filename: str,
    submitted_by: str,
) -> dict[str, Any]:
    _ensure_once(db)
    latest = latest_sample(db, allocation_id)
    version = int(latest["version"]) + 1 if latest else 1
    doc = {
        "sample_id": f"sample:{allocation_id}:v{version}",
        "allocation_id": allocation_id,
        "accurate_po_id": accurate_po_id,
        "accurate_line_id": accurate_line_id,
        "version": version,
        "evidence_id": evidence_id,
        "filename": filename,
        "submitted_by": submitted_by,
        "submitted_at": datetime.now(timezone.utc),
        "status": STATUS_PENDING,
        "decided_by": None,
        "decided_at": None,
        "reject_reason": None,
    }
    samples_collection(db).insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


def decide_sample(
    db: Database,
    *,
    allocation_id: str,
    version: int,
    approved: bool,
    actor_email: str,
    reason: str | None = None,
) -> dict[str, Any]:
    _ensure_once(db)
    doc = samples_collection(db).find_one_and_update(
        {
            "allocation_id": allocation_id,
            "version": version,
            "status": STATUS_PENDING,
        },
        {
            "$set": {
                "status": STATUS_APPROVED if approved else STATUS_REJECTED,
                "decided_by": actor_email,
                "decided_at": datetime.now(timezone.utc),
                "reject_reason": None if approved else (reason or "").strip(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise LookupError(
            f"Sampel menunggu approval tidak ditemukan: {allocation_id} v{version}"
        )
    return doc


def approved_sample(db: Database, allocation_id: str) -> dict[str, Any] | None:
    """Sampel approved untuk alokasi ini saja — bukan alokasi operator lain."""
    _ensure_once(db)
    return samples_collection(db).find_one(
        {"allocation_id": allocation_id, "status": STATUS_APPROVED},
        {"_id": 0},
        sort=[("version", DESCENDING)],
    )
