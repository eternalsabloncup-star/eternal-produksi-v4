"""Penyimpanan bukti/dokumen via GridFS + metadata soft-delete.

Hapus di UI tidak pernah physical delete: file asli, versi, aktor, dan
alasan tetap tersimpan untuk audit berizin.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import gridfs
from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database

EVIDENCE_COLLECTION = "evidence_files"
GRIDFS_BUCKET = "evidence"

_INDEXED_DATABASES: set[str] = set()


def evidence_collection(db: Database) -> Collection:
    return db[EVIDENCE_COLLECTION]


def ensure_evidence_indexes(db: Database) -> list[str]:
    coll = evidence_collection(db)
    created = [
        coll.create_index([("evidence_id", ASCENDING)], name="uniq_evidence_id", unique=True),
        coll.create_index(
            [("scope", ASCENDING), ("scope_id", ASCENDING), ("version", DESCENDING)],
            name="idx_evidence_scope_version",
        ),
    ]
    _INDEXED_DATABASES.add(db.name)
    return created


def _ensure_once(db: Database) -> None:
    if db.name not in _INDEXED_DATABASES:
        ensure_evidence_indexes(db)


def _bucket(db: Database) -> gridfs.GridFS:
    return gridfs.GridFS(db, collection=GRIDFS_BUCKET)


def next_version(db: Database, *, scope: str, scope_id: str) -> int:
    _ensure_once(db)
    latest = evidence_collection(db).find_one(
        {"scope": scope, "scope_id": scope_id},
        sort=[("version", DESCENDING)],
        projection={"version": 1},
    )
    return int(latest["version"]) + 1 if latest else 1


def store_evidence(
    db: Database,
    *,
    scope: str,
    scope_id: str,
    filename: str,
    content_type: str,
    content: bytes,
    uploaded_by: str,
    kind: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not content:
        raise ValueError("File bukti kosong.")
    _ensure_once(db)

    version = next_version(db, scope=scope, scope_id=scope_id)
    evidence_id = f"evid:{scope}:{scope_id}:v{version}"
    sha256 = hashlib.sha256(content).hexdigest()
    file_id = _bucket(db).put(
        content,
        filename=filename,
        contentType=content_type,
        evidence_id=evidence_id,
    )
    doc = {
        "evidence_id": evidence_id,
        "scope": scope,
        "scope_id": scope_id,
        "kind": kind,
        "version": version,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(content),
        "sha256": sha256,
        "gridfs_id": file_id,
        "uploaded_by": uploaded_by,
        "uploaded_at": datetime.now(timezone.utc),
        "deleted_at": None,
        "deleted_by": None,
        "delete_reason": None,
        "context": dict(context or {}),
    }
    evidence_collection(db).insert_one(dict(doc))
    doc.pop("_id", None)
    doc["gridfs_id"] = str(file_id)
    return doc


def list_evidence(
    db: Database,
    *,
    scope: str,
    scope_id: str,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    _ensure_once(db)
    query: dict[str, Any] = {"scope": scope, "scope_id": scope_id}
    if not include_deleted:
        query["deleted_at"] = None
    rows = list(
        evidence_collection(db)
        .find(query, {"_id": 0})
        .sort("version", ASCENDING)
    )
    for row in rows:
        row["gridfs_id"] = str(row.get("gridfs_id"))
    return rows


def get_evidence(db: Database, evidence_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    doc = evidence_collection(db).find_one({"evidence_id": evidence_id}, {"_id": 0})
    if doc:
        doc["gridfs_id"] = str(doc.get("gridfs_id"))
    return doc


def soft_delete_evidence(
    db: Database,
    *,
    evidence_id: str,
    actor_email: str,
    reason: str,
) -> dict[str, Any]:
    _ensure_once(db)
    if not reason.strip():
        raise ValueError("Alasan penghapusan wajib diisi.")
    doc = evidence_collection(db).find_one_and_update(
        {"evidence_id": evidence_id, "deleted_at": None},
        {
            "$set": {
                "deleted_at": datetime.now(timezone.utc),
                "deleted_by": actor_email,
                "delete_reason": reason.strip(),
            }
        },
        projection={"_id": 0},
        return_document=True,
    )
    if doc is None:
        raise LookupError(f"Bukti tidak ditemukan atau sudah dihapus: {evidence_id}")
    doc["gridfs_id"] = str(doc.get("gridfs_id"))
    return doc
