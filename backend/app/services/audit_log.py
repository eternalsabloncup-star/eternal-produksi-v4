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

    mark_index_specs_verified(db.name, [s["name"] for s in REQUIRED_AUDIT_INDEX_SPECS])
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
