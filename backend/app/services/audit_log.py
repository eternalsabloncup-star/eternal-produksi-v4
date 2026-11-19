"""Append-only audit log — Super Admin cannot erase history via this API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

COLLECTION = "audit_logs"

# Spek indeks audit — createIndex hanya via ensure_audit_indexes (bootstrap/test).
REQUIRED_AUDIT_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": COLLECTION,
        "keys": [("created_at", ASCENDING)],
        "name": "idx_audit_created_at",
        "unique": False,
    },
    {
        "collection": COLLECTION,
        "keys": [("action", ASCENDING), ("created_at", ASCENDING)],
        "name": "idx_audit_action_created",
        "unique": False,
    },
]

# Alias lama untuk kompatibilitas import.
AUDIT_INDEXES: list[dict[str, Any]] = [
    {"keys": s["keys"], "name": s["name"]} for s in REQUIRED_AUDIT_INDEX_SPECS
]


def ensure_audit_indexes(db: Database) -> list[str]:
    """Buat indeks audit. Hanya bootstrap eksplisit / test — bukan request path."""
    created: list[str] = []
    coll = db[COLLECTION]
    for spec in REQUIRED_AUDIT_INDEX_SPECS:
        name = coll.create_index(
            spec["keys"], name=spec["name"], unique=bool(spec.get("unique"))
        )
        created.append(name)
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_AUDIT_INDEX_SPECS], db=db
    )
    return created


def require_audit_indexes(db: Database) -> None:
    """Fail-closed: tidak createIndex."""
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_AUDIT_INDEX_SPECS)


def append_audit_event(
    db: Database,
    *,
    action: str,
    actor_email: str | None,
    target_email: str | None,
    detail: dict[str, Any] | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Insert one audit row. Never stores passwords/hashes/tokens/secrets.

    ``session``: ikut transaksi Mongo. createIndex dilarang di request path —
    indeks harus sudah ada (bootstrap) atau diverifikasi fail-closed.
    """
    if session is None:
        require_audit_indexes(db)
    safe_detail = dict(detail or {})
    for banned in ("password", "password_hash", "token", "secret", "csrf"):
        safe_detail.pop(banned, None)
    doc = {
        "action": action,
        "actor_email": actor_email,
        "target_email": target_email,
        "detail": safe_detail,
        "created_at": datetime.now(timezone.utc),
    }
    result = db[COLLECTION].insert_one(doc, session=session)
    doc["_id"] = result.inserted_id
    return doc


def list_audit_events(
    db: Database,
    *,
    action: str | None = None,
    actor_email: str | None = None,
    module: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated audit read — never returns secrets."""
    require_audit_indexes(db)
    query: dict[str, Any] = {}
    if action:
        query["action"] = action
    if actor_email:
        query["actor_email"] = actor_email.strip().lower()
    if module:
        query["action"] = {"$regex": f"^{module}\\."}
    created: dict[str, Any] = {}
    if created_from is not None:
        created["$gte"] = created_from
    if created_to is not None:
        created["$lte"] = created_to
    if created:
        query["created_at"] = created
    lim = max(1, min(int(limit), 200))
    off = max(0, int(offset))
    coll = db[COLLECTION]
    total = coll.count_documents(query)
    cursor = (
        coll.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(off)
        .limit(lim)
    )
    items = []
    for doc in cursor:
        detail = dict(doc.get("detail") or {})
        for banned in ("password", "password_hash", "token", "secret", "csrf"):
            detail.pop(banned, None)
        items.append(
            {
                "action": doc.get("action"),
                "actor_email": doc.get("actor_email"),
                "target_email": doc.get("target_email"),
                "detail": detail,
                "created_at": doc.get("created_at"),
            }
        )
    return {"items": items, "total": total, "limit": lim, "offset": off}
