"""Sampel cetak per alokasi operator — berversi, tidak dipakai bersama."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

SAMPLES_COLLECTION = "operator_samples"
SAMPLE_META_COLLECTION = "operator_sample_meta"

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
    # Satu pending approval aktif per alokasi — provision via bootstrap/test saja.
    {
        "collection": SAMPLES_COLLECTION,
        "keys": [("allocation_id", ASCENDING)],
        "name": "uniq_sample_allocation_pending",
        "unique": True,
        "partialFilterExpression": {"status": STATUS_PENDING},
    },
    {
        "collection": SAMPLE_META_COLLECTION,
        "keys": [("allocation_id", ASCENDING)],
        "name": "uniq_sample_meta_allocation",
        "unique": True,
    },
]


def samples_collection(db: Database) -> Collection:
    return db[SAMPLES_COLLECTION]


def ensure_sample_indexes(db: Database) -> list[str]:
    """Buat indeks sample — HANYA bootstrap/maintenance/test."""
    created: list[str] = []
    for spec in REQUIRED_SAMPLE_INDEX_SPECS:
        kwargs: dict[str, Any] = {
            "name": spec["name"],
            "unique": bool(spec.get("unique")),
        }
        if "partialFilterExpression" in spec:
            kwargs["partialFilterExpression"] = spec["partialFilterExpression"]
        created.append(db[spec["collection"]].create_index(spec["keys"], **kwargs))
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


def _allocate_sample_version(db: Database, allocation_id: str) -> int:
    """Counter atomik per alokasi — mencegah bentrok versi pada upload konkuren."""
    if db[SAMPLE_META_COLLECTION].find_one({"allocation_id": allocation_id}) is None:
        latest = samples_collection(db).find_one(
            {"allocation_id": allocation_id},
            {"version": 1},
            sort=[("version", DESCENDING)],
        )
        seed = int(latest["version"]) if latest else 0
        try:
            db[SAMPLE_META_COLLECTION].insert_one(
                {
                    "allocation_id": allocation_id,
                    "next_version": seed,
                    "pending": False,
                }
            )
        except DuplicateKeyError:
            pass
    meta = db[SAMPLE_META_COLLECTION].find_one_and_update(
        {"allocation_id": allocation_id},
        {"$inc": {"next_version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if meta is None:
        raise RuntimeError(f"Gagal mengalokasikan versi sampel: {allocation_id}")
    return int(meta["next_version"])


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
    version = _allocate_sample_version(db, allocation_id)
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
    try:
        samples_collection(db).insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise RuntimeError(
            f"Versi sampel bentrok untuk alokasi {allocation_id} v{version}."
        ) from exc
    if status == STATUS_PENDING:
        db[SAMPLE_META_COLLECTION].update_one(
            {"allocation_id": allocation_id},
            {"$set": {"pending": True, "pending_version": version}},
        )
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
    """CAS draft → menunggu_approval. Hanya versi terbaru; satu pending per alokasi.

    None = stale draft, sudah pending, atau bentrok konkuren.
    """
    _ensure_once(db)
    version = int(version)
    # Tolak konfirmasi draft usang bila ada unggahan lebih baru.
    latest = latest_sample(db, allocation_id)
    if latest is None or int(latest["version"]) != version:
        return None
    if pending_sample(db, allocation_id) is not None:
        return None
    # Meta CAS: kunci pending sebelum mutasi status (fail-closed bila race).
    meta_ok = db[SAMPLE_META_COLLECTION].find_one_and_update(
        {
            "allocation_id": allocation_id,
            "next_version": version,
            "pending": {"$ne": True},
        },
        {"$set": {"pending": True, "pending_version": version}},
        projection={"_id": 1},
    )
    if meta_ok is None:
        # Bootstrap meta untuk data lama tanpa counter.
        existing_meta = db[SAMPLE_META_COLLECTION].find_one(
            {"allocation_id": allocation_id}
        )
        if existing_meta is None:
            db[SAMPLE_META_COLLECTION].update_one(
                {"allocation_id": allocation_id},
                {
                    "$setOnInsert": {
                        "allocation_id": allocation_id,
                        "next_version": version,
                        "pending": False,
                    }
                },
                upsert=True,
            )
            meta_ok = db[SAMPLE_META_COLLECTION].find_one_and_update(
                {
                    "allocation_id": allocation_id,
                    "next_version": version,
                    "pending": {"$ne": True},
                },
                {"$set": {"pending": True, "pending_version": version}},
                projection={"_id": 1},
            )
        if meta_ok is None:
            return None
    try:
        submitted = samples_collection(db).find_one_and_update(
            {
                "allocation_id": allocation_id,
                "version": version,
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
    except DuplicateKeyError:
        # Unique pending index menolak pending kedua.
        db[SAMPLE_META_COLLECTION].update_one(
            {"allocation_id": allocation_id, "pending_version": version},
            {"$set": {"pending": False}, "$unset": {"pending_version": ""}},
        )
        return None
    if submitted is None:
        db[SAMPLE_META_COLLECTION].update_one(
            {"allocation_id": allocation_id, "pending_version": version},
            {"$set": {"pending": False}, "$unset": {"pending_version": ""}},
        )
        return None
    return submitted


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
    db[SAMPLE_META_COLLECTION].update_one(
        {"allocation_id": allocation_id},
        {"$set": {"pending": False}, "$unset": {"pending_version": ""}},
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
