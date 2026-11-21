"""Tracking link tokens — opaque CSPRNG, hash-only storage, revoke/regenerate/expiry.

Index policy (fail-closed):
- Request path → ``require_tracking_indexes`` (listIndexes only; never createIndex).
- Maintenance/bootstrap/test → ``ensure_tracking_indexes`` (explicit createIndex).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

COLLECTION = "tracking_links"
EVENTS_COLLECTION = "tracking_link_events"

LinkStatus = Literal["active", "revoked", "expired"]

# ~132 bit entropy (22 chars × log2(32)). Bukan kode pendek tebak-tebakan.
_TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_TOKEN_LENGTH = 22

DocumentScope = Literal[
    "operational",  # PO summary, SJ, bukti pengiriman (bila diizinkan)
    "financial",  # faktur + pembayaran — butuh auth khusus
]

DEFAULT_PUBLIC_SCOPES: tuple[DocumentScope, ...] = ("operational",)

# Cache verifikasi spek — kunci = target Mongo (host:port) + db + client id.
# Jangan pakai db.name saja: dua target berbeda bisa berbagi nama database.
_VERIFIED_SPECS: dict[str, frozenset[str]] = {}

REQUIRED_TRACKING_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": COLLECTION,
        "keys": [("token_hash", ASCENDING)],
        "name": "uniq_tracking_token_hash",
        "unique": True,
    },
    {
        "collection": COLLECTION,
        "keys": [("accurate_po_id", ASCENDING), ("status", ASCENDING)],
        "name": "idx_tracking_po_status",
        "unique": False,
    },
    {
        "collection": COLLECTION,
        "keys": [("customer_slug", ASCENDING), ("po_slug", ASCENDING)],
        "name": "idx_tracking_slugs",
        "unique": False,
    },
    {
        "collection": EVENTS_COLLECTION,
        "keys": [("token_hash", ASCENDING), ("created_at", ASCENDING)],
        "name": "idx_tracking_events_token",
        "unique": False,
    },
]

# Bukan required request-path / bukan bootstrap auto-create.
# Manual maintenance (setelah audit duplikat aktif) untuk hard concurrency:
#   db.tracking_links.createIndex(
#     { accurate_po_id: 1 },
#     { unique: true, name: "uniq_tracking_one_active_per_po",
#       partialFilterExpression: { status: "active" } })
MANUAL_UNIQ_ACTIVE_PER_PO_INDEX = {
    "collection": COLLECTION,
    "keys": [("accurate_po_id", ASCENDING)],
    "name": "uniq_tracking_one_active_per_po",
    "unique": True,
    "partialFilterExpression": {"status": "active"},
}


class TrackingActiveLinkExists(RuntimeError):
    """PO sudah punya link aktif — generate ditolak (bukan regenerate)."""

    code = "TRACKING_ACTIVE_EXISTS"

    def __init__(self, message: str = "Link aktif sudah ada untuk PO ini.") -> None:
        super().__init__(message)
        self.message = message


class MissingRequiredTrackingIndexError(RuntimeError):
    """Indeks tracking wajib hilang/salah spek — request ditolak (fail-closed)."""

    code = "MISSING_REQUIRED_INDEX"

    def __init__(
        self,
        message: str,
        *,
        missing: list[str] | None = None,
        conflict: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing = list(missing or [])
        self.conflict = list(conflict or [])


def links_collection(db: Database) -> Collection:
    return db[COLLECTION]


def events_collection(db: Database) -> Collection:
    return db[EVENTS_COLLECTION]


def tracking_index_cache_key(db: Database) -> str:
    """Identitas isolasi: nodes Mongo + nama DB + id client PyMongo.

    Dua database bernama sama pada host/port atau client berbeda tidak berbagi cache.
    """
    client = db.client
    nodes: list[str] = []
    address = getattr(client, "address", None)
    if address and len(address) >= 2:
        nodes.append(f"{address[0]}:{address[1]}")
    raw_nodes = getattr(client, "nodes", None) or set()
    for item in sorted(raw_nodes):
        if isinstance(item, tuple) and len(item) >= 2:
            nodes.append(f"{item[0]}:{item[1]}")
        else:
            nodes.append(str(item))
    # Unik, urutan stabil.
    uniq: list[str] = []
    seen: set[str] = set()
    for n in nodes:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    if not uniq:
        uniq.append("unknown-host")
    return f"{','.join(uniq)}#{db.name}#cid:{id(client)}"


def clear_tracking_index_verification_cache(
    db: Database | None = None,
    *,
    cache_key: str | None = None,
) -> None:
    """Hapus cache. Tanpa argumen → clear semua. Utamakan objek ``db``."""
    if db is None and cache_key is None:
        _VERIFIED_SPECS.clear()
        return
    key = cache_key or tracking_index_cache_key(db)  # type: ignore[arg-type]
    _VERIFIED_SPECS.pop(key, None)


def mark_tracking_index_specs_verified(db: Database, names: list[str]) -> None:
    key = tracking_index_cache_key(db)
    prev = _VERIFIED_SPECS.get(key, frozenset())
    _VERIFIED_SPECS[key] = prev | frozenset(names)


def _normalize_key_list(keys: Any) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if isinstance(keys, list):
        for item in keys:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                out.append((str(item[0]), int(item[1])))
            elif isinstance(item, dict):
                for k, v in item.items():
                    out.append((str(k), int(v)))
    elif isinstance(keys, dict):
        for k, v in keys.items():
            out.append((str(k), int(v)))
    return out


def _index_matches_spec(existing: dict[str, Any], spec: dict[str, Any]) -> bool:
    if existing.get("name") != spec["name"]:
        return False
    if bool(existing.get("unique")) != bool(spec.get("unique")):
        return False
    if _normalize_key_list(existing.get("key")) != [
        (k, int(v)) for k, v in spec["keys"]
    ]:
        return False
    return True


def inspect_tracking_index_specs(db: Database) -> dict[str, Any]:
    """Bandingkan indeks aktual vs spek — tanpa createIndex."""
    present: list[str] = []
    missing: list[str] = []
    conflict: list[dict[str, Any]] = []
    by_coll: dict[str, list[dict[str, Any]]] = {}

    for spec in REQUIRED_TRACKING_INDEX_SPECS:
        coll_name = str(spec["collection"])
        if coll_name not in by_coll:
            by_coll[coll_name] = list(db[coll_name].list_indexes())
        indexes = by_coll[coll_name]
        match = next((ix for ix in indexes if ix.get("name") == spec["name"]), None)
        if match is None:
            missing.append(spec["name"])
            continue
        if _index_matches_spec(match, spec):
            present.append(spec["name"])
        else:
            conflict.append(
                {
                    "name": spec["name"],
                    "collection": coll_name,
                    "expected_unique": bool(spec.get("unique")),
                    "actual_unique": bool(match.get("unique")),
                    "expected_keys": [(k, int(v)) for k, v in spec["keys"]],
                    "actual_keys": _normalize_key_list(match.get("key")),
                }
            )

    return {
        "ok": not missing and not conflict,
        "writes": False,
        "db_name": db.name,
        "required": [s["name"] for s in REQUIRED_TRACKING_INDEX_SPECS],
        "present": present,
        "missing": missing,
        "conflict": conflict,
    }


def require_tracking_indexes(db: Database) -> None:
    """Fail-closed pada request path. Tidak pernah createIndex."""
    names = frozenset(str(s["name"]) for s in REQUIRED_TRACKING_INDEX_SPECS)
    cached = _VERIFIED_SPECS.get(tracking_index_cache_key(db), frozenset())
    if names <= cached:
        return

    report = inspect_tracking_index_specs(db)
    if report["missing"] or report["conflict"]:
        raise MissingRequiredTrackingIndexError(
            "Indeks tracking wajib belum tersedia atau spek tidak cocok. "
            "Jalankan maintenance ensure_tracking_indexes setelah persetujuan Owner "
            f"(missing={report['missing']}, conflict="
            f"{[c.get('name') for c in report['conflict']]}).",
            missing=report["missing"],
            conflict=report["conflict"],
        )
    mark_tracking_index_specs_verified(db, list(names))


def ensure_tracking_indexes(db: Database) -> list[str]:
    """Buat indeks tracking — HANYA maintenance/bootstrap/test eksplisit."""
    created: list[str] = []
    for spec in REQUIRED_TRACKING_INDEX_SPECS:
        coll = db[spec["collection"]]
        created.append(
            coll.create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    mark_tracking_index_specs_verified(
        db, [s["name"] for s in REQUIRED_TRACKING_INDEX_SPECS]
    )
    return created


def _ensure_once(db: Database) -> None:
    """Alias request-path: verify-only (bukan lazy createIndex)."""
    require_tracking_indexes(db)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_raw_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _public_link(doc: dict[str, Any]) -> dict[str, Any]:
    # Jangan pernah expose hash/ciphertext/raw token ke klien list.
    out = {
        k: v
        for k, v in doc.items()
        if k not in ("_id", "token_hash", "token_sealed")
    }
    if "_id" in doc:
        out["id"] = str(doc["_id"])
    out["url_recoverable"] = bool(doc.get("token_sealed"))
    return out


def find_active_doc_for_po(db: Database, accurate_po_id: str) -> dict[str, Any] | None:
    """Dokumen Mongo aktif (termasuk token_sealed) — internal saja."""
    _ensure_once(db)
    return links_collection(db).find_one(
        {"accurate_po_id": str(accurate_po_id), "status": "active"},
        sort=[("created_at", -1)],
    )


def find_active_for_po(db: Database, accurate_po_id: str) -> dict[str, Any] | None:
    """Link aktif terbaru untuk PO (tanpa raw token / ciphertext)."""
    doc = find_active_doc_for_po(db, accurate_po_id)
    return _public_link(doc) if doc else None


def count_active_for_po(db: Database, accurate_po_id: str) -> int:
    _ensure_once(db)
    return int(
        links_collection(db).count_documents(
            {"accurate_po_id": str(accurate_po_id), "status": "active"}
        )
    )


def create_link(
    db: Database,
    *,
    accurate_po_id: str,
    po_no: str,
    customer_name: str,
    customer_slug: str,
    po_slug: str,
    actor_email: str | None,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
    ttl_seconds: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Buat link baru. Token mentah hanya dikembalikan sekali.

    Menolak jika sudah ada link aktif (check-then-insert). Hard race protection
    membutuhkan indeks manual ``uniq_tracking_one_active_per_po`` (lihat konstanta).
    """
    from pymongo.errors import DuplicateKeyError

    _ensure_once(db)
    if find_active_for_po(db, str(accurate_po_id)):
        raise TrackingActiveLinkExists()
    from app.services.tracking_token_seal import seal_raw_token

    raw = generate_raw_token()
    sealed = seal_raw_token(raw)
    now = utcnow()
    exp = expires_at
    if exp is None and ttl_seconds is not None and ttl_seconds > 0:
        exp = now + timedelta(seconds=ttl_seconds)
    doc: dict[str, Any] = {
        "token_hash": hash_token(raw),
        # Ciphertext Fernet — bukan plaintext. Di-strip dari _public_link.
        "token_sealed": sealed,
        "accurate_po_id": str(accurate_po_id),
        "po_no": po_no,
        "customer_name": customer_name,
        "customer_slug": customer_slug,
        "po_slug": po_slug,
        "status": "active",
        "scopes": list(scopes or DEFAULT_PUBLIC_SCOPES),
        "created_at": now,
        "created_by": actor_email,
        "revoked_at": None,
        "revoked_by": None,
        "expires_at": exp,
        "regenerated_from": None,
        "last_accessed_at": None,
        "access_count": 0,
    }
    try:
        result = links_collection(db).insert_one(doc)
    except DuplicateKeyError as exc:
        # Unique partial index (manual) menangkap race dua generate bersamaan.
        raise TrackingActiveLinkExists() from exc
    doc["_id"] = result.inserted_id
    _append_event(
        db,
        token_hash=doc["token_hash"],
        action="generate",
        actor_email=actor_email,
        detail={"accurate_po_id": accurate_po_id},
    )
    return raw, _public_link(doc)


def find_by_hash(db: Database, token_hash: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return links_collection(db).find_one({"token_hash": token_hash})


def resolve_active_link(db: Database, raw_token: str) -> dict[str, Any] | None:
    """Valid aktif saja. Expired diganti status saat dibaca (lazy)."""
    _ensure_once(db)
    th = hash_token(raw_token.strip())
    doc = links_collection(db).find_one({"token_hash": th})
    if not doc:
        return None
    if doc.get("status") == "revoked" or doc.get("revoked_at") is not None:
        return None
    exp = doc.get("expires_at")
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= utcnow():
            links_collection(db).update_one(
                {"_id": doc["_id"], "status": "active"},
                {"$set": {"status": "expired"}},
            )
            return None
    if doc.get("status") != "active":
        return None
    return doc


def touch_access(db: Database, token_hash: str) -> None:
    links_collection(db).update_one(
        {"token_hash": token_hash},
        {"$set": {"last_accessed_at": utcnow()}, "$inc": {"access_count": 1}},
    )


def list_links_for_po(db: Database, accurate_po_id: str) -> list[dict[str, Any]]:
    _ensure_once(db)
    rows = list(
        links_collection(db)
        .find({"accurate_po_id": str(accurate_po_id)})
        .sort("created_at", -1)
    )
    # Lazy expire
    now = utcnow()
    out: list[dict[str, Any]] = []
    for row in rows:
        exp = row.get("expires_at")
        if (
            row.get("status") == "active"
            and exp is not None
            and (exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)) <= now
        ):
            links_collection(db).update_one(
                {"_id": row["_id"]}, {"$set": {"status": "expired"}}
            )
            row["status"] = "expired"
        out.append(_public_link(row))
    return out


def revoke_link(
    db: Database,
    *,
    link_id: str | None = None,
    token_hash: str | None = None,
    actor_email: str | None = None,
    reason: str | None = None,
) -> dict[str, Any] | None:
    from bson import ObjectId

    _ensure_once(db)
    query: dict[str, Any]
    if link_id:
        query = {"_id": ObjectId(link_id)}
    elif token_hash:
        query = {"token_hash": token_hash}
    else:
        raise ValueError("link_id atau token_hash wajib")
    now = utcnow()
    doc = links_collection(db).find_one_and_update(
        {**query, "status": "active"},
        {
            "$set": {
                "status": "revoked",
                "revoked_at": now,
                "revoked_by": actor_email,
                "revoke_reason": reason,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc:
        _append_event(
            db,
            token_hash=doc["token_hash"],
            action="revoke",
            actor_email=actor_email,
            detail={"reason": reason},
        )
        return _public_link(doc)
    return None


def revoke_active_for_po(
    db: Database,
    *,
    accurate_po_id: str,
    actor_email: str | None,
    reason: str = "regenerate",
) -> int:
    """Revoke semua link aktif untuk satu PO. Return jumlah yang di-revoke."""
    _ensure_once(db)
    active = list(
        links_collection(db).find(
            {"accurate_po_id": str(accurate_po_id), "status": "active"},
            {"token_hash": 1},
        )
    )
    count = 0
    for row in active:
        if revoke_link(
            db,
            token_hash=row["token_hash"],
            actor_email=actor_email,
            reason=reason,
        ):
            count += 1
    return count


def _append_event(
    db: Database,
    *,
    token_hash: str,
    action: str,
    actor_email: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    safe = dict(detail or {})
    for banned in ("token", "raw_token", "password", "secret"):
        safe.pop(banned, None)
    events_collection(db).insert_one(
        {
            "token_hash": token_hash,
            "action": action,
            "actor_email": actor_email,
            "detail": safe,
            "created_at": utcnow(),
        }
    )
