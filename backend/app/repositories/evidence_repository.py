"""Penyimpanan bukti/dokumen via GridFS + metadata soft-delete.

Hapus di UI tidak pernah physical delete: file asli, versi, aktor, dan
alasan tetap tersimpan untuk audit berizin.

createIndex hanya via ``ensure_evidence_indexes`` (bootstrap eksplisit).
Request path memakai require/verify fail-closed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import gridfs
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

EVIDENCE_COLLECTION = "evidence_files"
EVIDENCE_META_COLLECTION = "evidence_version_meta"
GRIDFS_BUCKET = "evidence"

REQUIRED_EVIDENCE_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": EVIDENCE_COLLECTION,
        "keys": [("evidence_id", ASCENDING)],
        "name": "uniq_evidence_id",
        "unique": True,
    },
    {
        "collection": EVIDENCE_COLLECTION,
        "keys": [
            ("scope", ASCENDING),
            ("scope_id", ASCENDING),
            ("version", DESCENDING),
        ],
        "name": "idx_evidence_scope_version",
        "unique": False,
    },
    {
        "collection": EVIDENCE_META_COLLECTION,
        "keys": [("scope", ASCENDING), ("scope_id", ASCENDING)],
        "name": "uniq_evidence_version_meta",
        "unique": True,
    },
]


def evidence_collection(db: Database) -> Collection:
    return db[EVIDENCE_COLLECTION]


def ensure_evidence_indexes(db: Database) -> list[str]:
    """Buat indeks evidence. Hanya bootstrap eksplisit / test — bukan request path."""
    created: list[str] = []
    for spec in REQUIRED_EVIDENCE_INDEX_SPECS:
        coll = db[spec["collection"]]
        kwargs: dict[str, Any] = {
            "name": spec["name"],
            "unique": bool(spec.get("unique")),
        }
        created.append(coll.create_index(spec["keys"], **kwargs))
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_EVIDENCE_INDEX_SPECS], db=db
    )
    return created


def require_evidence_indexes(db: Database, *, session: Any | None = None) -> None:
    """Fail-closed: verifikasi spek indeks tanpa createIndex."""
    if session is not None:
        return
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_EVIDENCE_INDEX_SPECS)


def _require_once(db: Database, *, session: Any | None = None) -> None:
    """Alias request-path: fail-closed require (bukan lazy createIndex)."""
    require_evidence_indexes(db, session=session)


def _bucket(db: Database) -> gridfs.GridFS:
    return gridfs.GridFS(db, collection=GRIDFS_BUCKET)


def next_version(db: Database, *, scope: str, scope_id: str) -> int:
    """Counter atomik per scope — aman untuk unggahan konkuren."""
    _require_once(db)
    meta_coll = db[EVIDENCE_META_COLLECTION]
    if meta_coll.find_one({"scope": scope, "scope_id": scope_id}) is None:
        latest = evidence_collection(db).find_one(
            {"scope": scope, "scope_id": scope_id},
            sort=[("version", DESCENDING)],
            projection={"version": 1},
        )
        seed = int(latest["version"]) if latest else 0
        try:
            meta_coll.insert_one(
                {"scope": scope, "scope_id": scope_id, "next_version": seed}
            )
        except DuplicateKeyError:
            pass
    meta = meta_coll.find_one_and_update(
        {"scope": scope, "scope_id": scope_id},
        {"$inc": {"next_version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if meta is None:
        raise RuntimeError(f"Gagal mengalokasikan versi evidence: {scope}/{scope_id}")
    return int(meta["next_version"])


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
    _require_once(db)

    last_error: Exception | None = None
    for _ in range(5):
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
        try:
            evidence_collection(db).insert_one(dict(doc))
        except DuplicateKeyError as exc:
            last_error = exc
            try:
                _bucket(db).delete(file_id)
            except Exception:  # noqa: BLE001
                pass
            continue
        doc.pop("_id", None)
        doc["gridfs_id"] = str(file_id)
        return doc
    raise RuntimeError(
        f"Gagal menyimpan evidence setelah retry: {scope}/{scope_id}"
    ) from last_error


def list_evidence(
    db: Database,
    *,
    scope: str,
    scope_id: str,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    _require_once(db)
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
    _require_once(db)
    doc = evidence_collection(db).find_one({"evidence_id": evidence_id}, {"_id": 0})
    if doc:
        doc["gridfs_id"] = str(doc.get("gridfs_id"))
    return doc


def open_evidence_stream(db: Database, evidence_id: str) -> tuple[dict[str, Any], Any]:
    """Buka stream GridFS untuk bukti aktif — tanpa soft-delete."""
    from bson import ObjectId

    _require_once(db)
    doc = evidence_collection(db).find_one({"evidence_id": evidence_id}, {"_id": 0})
    if doc is None:
        raise LookupError(f"Bukti tidak ditemukan: {evidence_id}")
    if doc.get("deleted_at") is not None:
        raise LookupError(f"Bukti sudah dihapus: {evidence_id}")
    grid_id = doc.get("gridfs_id")
    if grid_id is None:
        raise LookupError(f"Bukti tanpa file GridFS: {evidence_id}")
    stream = _bucket(db).get(ObjectId(str(grid_id)))
    doc["gridfs_id"] = str(grid_id)
    return doc, stream


def attach_evidence_to_delivery(
    db: Database,
    *,
    evidence_ids: list[str],
    accurate_sj_id: str,
    delivery_event_id: str,
    attempt: int,
    session: Any | None = None,
) -> None:
    """Tautkan metadata evidence ke event pengiriman (ikut transaksi Mongo)."""
    _require_once(db, session=session)
    for evidence_id in evidence_ids:
        updated = evidence_collection(db).find_one_and_update(
            {"evidence_id": evidence_id, "deleted_at": None},
            {
                "$set": {
                    "context.delivery_commit": "committed",
                    "context.accurate_sj_id": str(accurate_sj_id),
                    "context.delivery_event_id": delivery_event_id,
                    "context.delivery_attempt": int(attempt),
                }
            },
            projection={"_id": 1},
            session=session,
        )
        if updated is None:
            raise LookupError(
                f"Bukti {evidence_id} tidak ditemukan untuk tautan pengiriman."
            )


def soft_delete_evidence(
    db: Database,
    *,
    evidence_id: str,
    actor_email: str,
    reason: str,
    session: Any | None = None,
) -> dict[str, Any]:
    _require_once(db, session=session)
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
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if doc is None:
        raise LookupError(f"Bukti tidak ditemukan atau sudah dihapus: {evidence_id}")
    doc["gridfs_id"] = str(doc.get("gridfs_id"))
    return doc


def list_orphan_gridfs_candidates(
    db: Database, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Read-only maintenance: kandidat orphan GridFS — tanpa hapus otomatis.

    Mencakup file GridFS yang metadata evidence-nya soft-deleted (staging
    recovery) atau tidak punya baris evidence_files sama sekali.
    """
    _require_once(db)
    files = db[f"{GRIDFS_BUCKET}.files"]
    out: list[dict[str, Any]] = []
    cursor = files.find(
        {}, {"_id": 1, "filename": 1, "evidence_id": 1, "length": 1}
    ).limit(max(limit * 5, limit))
    for row in cursor:
        evidence_id = row.get("evidence_id")
        meta = None
        if evidence_id:
            meta = evidence_collection(db).find_one(
                {"evidence_id": evidence_id},
                {"_id": 0, "deleted_at": 1},
            )
        if meta is None:
            reason = "gridfs_without_evidence_metadata"
        elif meta.get("deleted_at") is not None:
            reason = "evidence_soft_deleted_gridfs_retained"
        else:
            continue
        out.append(
            {
                "gridfs_id": str(row["_id"]),
                "filename": row.get("filename"),
                "evidence_id": evidence_id,
                "length": row.get("length"),
                "reason": reason,
                "writes": False,
            }
        )
        if len(out) >= limit:
            break
    return out
