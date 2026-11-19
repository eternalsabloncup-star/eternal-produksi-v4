"""Tracking link tokens — opaque CSPRNG, hash-only storage, revoke/regenerate/expiry."""

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
    "financial",  # faktur + penerimaan — butuh auth khusus
]

DEFAULT_PUBLIC_SCOPES: tuple[DocumentScope, ...] = ("operational",)

_INDEXED: set[str] = set()


def links_collection(db: Database) -> Collection:
    return db[COLLECTION]


def events_collection(db: Database) -> Collection:
    return db[EVENTS_COLLECTION]


def ensure_tracking_indexes(db: Database) -> list[str]:
    created = [
        links_collection(db).create_index(
            [("token_hash", ASCENDING)], name="uniq_tracking_token_hash", unique=True
        ),
        links_collection(db).create_index(
            [("accurate_po_id", ASCENDING), ("status", ASCENDING)],
            name="idx_tracking_po_status",
        ),
        links_collection(db).create_index(
            [("customer_slug", ASCENDING), ("po_slug", ASCENDING)],
            name="idx_tracking_slugs",
        ),
        events_collection(db).create_index(
            [("token_hash", ASCENDING), ("created_at", ASCENDING)],
            name="idx_tracking_events_token",
        ),
    ]
    _INDEXED.add(db.name)
    return created


def _ensure_once(db: Database) -> None:
    if db.name not in _INDEXED:
        ensure_tracking_indexes(db)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_raw_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LENGTH))


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _public_link(doc: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in doc.items() if k not in ("_id", "token_hash")}
    if "_id" in doc:
        out["id"] = str(doc["_id"])
    return out


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
    """Buat link baru. Token mentah hanya dikembalikan sekali."""
    _ensure_once(db)
    raw = generate_raw_token()
    now = utcnow()
    exp = expires_at
    if exp is None and ttl_seconds is not None and ttl_seconds > 0:
        exp = now + timedelta(seconds=ttl_seconds)
    doc: dict[str, Any] = {
        "token_hash": hash_token(raw),
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
    result = links_collection(db).insert_one(doc)
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
