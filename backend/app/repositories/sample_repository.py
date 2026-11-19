"""Sampel cetak per alokasi operator — berversi, tidak dipakai bersama."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

SAMPLES_COLLECTION = "operator_samples"

STATUS_DRAFT = "draft"
STATUS_PENDING = "menunggu_approval"
STATUS_APPROVED = "disetujui"
STATUS_REJECTED = "ditolak"

REQUIRED_SAMPLE_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": SAMPLES_COLLECTION,
        "keys": [("allocation_id", ASCENDING), ("version", DESCENDING)],
        "name": "uniq_sample_allocation_version",
        "unique": True,
    },
    {
        "collection": SAMPLES_COLLECTION,
        "keys": [("accurate_line_id", ASCENDING), ("status", ASCENDING)],
        "name": "idx_sample_line_status",
        "unique": False,
    },
]


def samples_collection(db: Database) -> Collection:
    return db[SAMPLES_COLLECTION]


def ensure_sample_indexes(db: Database) -> list[str]:
    """Buat indeks sample — HANYA bootstrap/maintenance/test."""
    created: list[str] = []
    for spec in REQUIRED_SAMPLE_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_SAMPLE_INDEX_SPECS], db=db
    )
    return created


def require_sample_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_SAMPLE_INDEX_SPECS)


def inspect_sample_indexes(db: Database) -> dict[str, Any]:
    from app.services.index_preflight import inspect_index_specs

    return inspect_index_specs(db, REQUIRED_SAMPLE_INDEX_SPECS)


def _ensure_once(db: Database) -> None:
    """Alias request-path: require (bukan lazy createIndex)."""
    require_sample_indexes(db)


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
    status: str = STATUS_DRAFT,
) -> dict[str, Any]:
    """Append-only versi sampel. Default = draft (belum diajukan approval).

    Legacy callers yang membutuhkan pending langsung dapat mengirim
    status=STATUS_PENDING (kompatibilitas tanpa migrasi).
    """
    _ensure_once(db)
    if status not in {
        STATUS_DRAFT,
        STATUS_PENDING,
        STATUS_APPROVED,
        STATUS_REJECTED,
    }:
        raise ValueError(f"Status sampel tidak valid: {status}")
    latest = latest_sample(db, allocation_id)
    version = int(latest["version"]) + 1 if latest else 1
    now = datetime.now(timezone.utc)
    doc = {
        "sample_id": f"sample:{allocation_id}:v{version}",
        "allocation_id": allocation_id,
        "accurate_po_id": accurate_po_id,
        "accurate_line_id": accurate_line_id,
        "version": version,
        "evidence_id": evidence_id,
        "filename": filename,
        "submitted_by": submitted_by,
        "uploaded_at": now,
        # submitted_at diisi saat Konfirmasi (draft → pending); legacy pending langsung.
        "submitted_at": now if status != STATUS_DRAFT else None,
        "status": status,
        "decided_by": None,
        "decided_at": None,
        "reject_reason": None,
    }
    samples_collection(db).insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


def latest_draft(db: Database, allocation_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return samples_collection(db).find_one(
        {"allocation_id": allocation_id, "status": STATUS_DRAFT},
        {"_id": 0},
        sort=[("version", DESCENDING)],
    )


def pending_sample(db: Database, allocation_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return samples_collection(db).find_one(
        {"allocation_id": allocation_id, "status": STATUS_PENDING},
        {"_id": 0},
        sort=[("version", DESCENDING)],
    )


def confirm_draft_sample(
    db: Database,
    *,
    allocation_id: str,
    version: int,
    actor_email: str,
) -> dict[str, Any] | None:
    """CAS draft → menunggu_approval. None = versi bukan draft aktif (bentrok/idempoten)."""
    _ensure_once(db)
    return samples_collection(db).find_one_and_update(
        {
            "allocation_id": allocation_id,
            "version": int(version),
            "status": STATUS_DRAFT,
        },
        {
            "$set": {
                "status": STATUS_PENDING,
                "submitted_at": datetime.now(timezone.utc),
                "submitted_by": actor_email,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


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
    """Sampel approved aktif — draft/pending/rejected tidak membuka gate cetak."""
    _ensure_once(db)
    return samples_collection(db).find_one(
        {"allocation_id": allocation_id, "status": STATUS_APPROVED},
        {"_id": 0},
        sort=[("version", DESCENDING)],
    )
