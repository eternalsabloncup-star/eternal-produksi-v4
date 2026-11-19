"""Design per PO: submission berversi, link approval bertoken, approval customer.

Aturan terkunci: satu PO = satu file desain walaupun punya beberapa item Cetak.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

SUBMISSIONS_COLLECTION = "design_submissions"
LINKS_COLLECTION = "design_approval_links"
APPROVALS_COLLECTION = "design_approvals"

APPROVAL_LINK_TTL_HOURS = 72

REQUIRED_DESIGN_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": SUBMISSIONS_COLLECTION,
        "keys": [("accurate_po_id", ASCENDING), ("version", DESCENDING)],
        "name": "uniq_design_po_version",
        "unique": True,
    },
    {
        "collection": LINKS_COLLECTION,
        "keys": [("token_hash", ASCENDING)],
        "name": "uniq_design_link_token",
        "unique": True,
    },
    {
        "collection": LINKS_COLLECTION,
        "keys": [("accurate_po_id", ASCENDING)],
        "name": "idx_design_link_po",
        "unique": False,
    },
    {
        "collection": APPROVALS_COLLECTION,
        "keys": [("accurate_po_id", ASCENDING), ("design_version", ASCENDING)],
        "name": "uniq_design_approval_version",
        "unique": True,
    },
]


def submissions_collection(db: Database) -> Collection:
    return db[SUBMISSIONS_COLLECTION]


def links_collection(db: Database) -> Collection:
    return db[LINKS_COLLECTION]


def approvals_collection(db: Database) -> Collection:
    return db[APPROVALS_COLLECTION]


def ensure_design_indexes(db: Database) -> list[str]:
    """Buat indeks design — HANYA bootstrap/maintenance/test."""
    created: list[str] = []
    for spec in REQUIRED_DESIGN_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_DESIGN_INDEX_SPECS], db=db
    )
    return created


def require_design_indexes(db: Database) -> None:
    """Fail-closed request path — tidak createIndex."""
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_DESIGN_INDEX_SPECS)


def inspect_design_indexes(db: Database) -> dict[str, Any]:
    from app.services.index_preflight import inspect_index_specs

    return inspect_index_specs(db, REQUIRED_DESIGN_INDEX_SPECS)


def _ensure_once(db: Database) -> None:
    """Alias request-path: require (bukan lazy createIndex)."""
    require_design_indexes(db)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_submission(
    db: Database,
    *,
    accurate_po_id: str,
    po_no: str,
    evidence_id: str,
    filename: str,
    submitted_by: str,
) -> dict[str, Any]:
    """Draft desain baru; versi lama tetap tersimpan sebagai riwayat."""
    _ensure_once(db)
    latest = latest_submission(db, accurate_po_id)
    version = int(latest["version"]) + 1 if latest else 1
    now = datetime.now(timezone.utc)
    doc = {
        "accurate_po_id": accurate_po_id,
        "po_no": po_no,
        "version": version,
        "evidence_id": evidence_id,
        "filename": filename,
        "submitted_by": submitted_by,
        "submitted_at": now,
        "confirmed_at": None,
        "confirmed_by": None,
    }
    submissions_collection(db).insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


def latest_submission(db: Database, accurate_po_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return submissions_collection(db).find_one(
        {"accurate_po_id": accurate_po_id},
        {"_id": 0},
        sort=[("version", DESCENDING)],
    )


def list_submissions(db: Database, accurate_po_id: str) -> list[dict[str, Any]]:
    _ensure_once(db)
    return list(
        submissions_collection(db)
        .find({"accurate_po_id": accurate_po_id}, {"_id": 0})
        .sort("version", ASCENDING)
    )


def confirm_submission(
    db: Database,
    *,
    accurate_po_id: str,
    version: int,
    confirmed_by: str,
) -> dict[str, Any]:
    """Konfirmasi Designer. Idempoten: konfirmasi ulang tidak mengubah riwayat."""
    _ensure_once(db)
    existing = submissions_collection(db).find_one(
        {"accurate_po_id": accurate_po_id, "version": version}, {"_id": 0}
    )
    if existing is None:
        raise LookupError(f"Submission desain tidak ditemukan: {accurate_po_id} v{version}")
    if existing.get("confirmed_at") is not None:
        return existing
    return submissions_collection(db).find_one_and_update(
        {"accurate_po_id": accurate_po_id, "version": version, "confirmed_at": None},
        {
            "$set": {
                "confirmed_at": datetime.now(timezone.utc),
                "confirmed_by": confirmed_by,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


def create_approval_link(
    db: Database,
    *,
    accurate_po_id: str,
    design_version: int,
    created_by: str,
    ttl_hours: int = APPROVAL_LINK_TTL_HOURS,
) -> tuple[str, dict[str, Any]]:
    """Token acak; hanya hash yang disimpan. Scope terbatas pada satu PO."""
    _ensure_once(db)
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    doc = {
        "token_hash": hash_token(raw_token),
        "accurate_po_id": accurate_po_id,
        "design_version": design_version,
        "created_by": created_by,
        "created_at": now,
        "expires_at": now + timedelta(hours=ttl_hours),
        "used_at": None,
        "revoked_at": None,
    }
    links_collection(db).insert_one(dict(doc))
    doc.pop("_id", None)
    return raw_token, doc


def find_valid_link(db: Database, raw_token: str) -> dict[str, Any] | None:
    _ensure_once(db)
    doc = links_collection(db).find_one({"token_hash": hash_token(raw_token)}, {"_id": 0})
    if not doc:
        return None
    if doc.get("revoked_at") is not None:
        return None
    expires_at = doc.get("expires_at")
    if expires_at is not None and expires_at.replace(tzinfo=timezone.utc) < datetime.now(
        timezone.utc
    ):
        return None
    return doc


def record_approval(
    db: Database,
    *,
    accurate_po_id: str,
    design_version: int,
    signer_name: str,
    signature_evidence_id: str,
    token_hash: str,
) -> dict[str, Any]:
    """Satu approval per versi desain; klik berulang tidak menggandakan."""
    _ensure_once(db)
    now = datetime.now(timezone.utc)
    doc = {
        "accurate_po_id": accurate_po_id,
        "design_version": design_version,
        "signer_name": signer_name.strip(),
        "signature_evidence_id": signature_evidence_id,
        "token_hash": token_hash,
        "approved_at": now,
    }
    try:
        approvals_collection(db).insert_one(dict(doc))
    except DuplicateKeyError:
        existing = approvals_collection(db).find_one(
            {"accurate_po_id": accurate_po_id, "design_version": design_version},
            {"_id": 0},
        )
        return {**(existing or {}), "duplicate": True}

    links_collection(db).update_one(
        {"token_hash": token_hash, "used_at": None}, {"$set": {"used_at": now}}
    )
    doc.pop("_id", None)
    return {**doc, "duplicate": False}


def get_approval(
    db: Database, *, accurate_po_id: str, design_version: int
) -> dict[str, Any] | None:
    _ensure_once(db)
    return approvals_collection(db).find_one(
        {"accurate_po_id": accurate_po_id, "design_version": design_version}, {"_id": 0}
    )
