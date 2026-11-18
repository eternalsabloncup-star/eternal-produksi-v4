"""Append-only audit log — Super Admin cannot erase history via this API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

COLLECTION = "audit_logs"

AUDIT_INDEXES: list[dict[str, Any]] = [
    {
        "keys": [("created_at", ASCENDING)],
        "name": "idx_audit_created_at",
    },
    {
        "keys": [("action", ASCENDING), ("created_at", ASCENDING)],
        "name": "idx_audit_action_created",
    },
]


_INDEXED_DATABASES: set[str] = set()


def ensure_audit_indexes(db: Database) -> list[str]:
    created: list[str] = []
    coll = db[COLLECTION]
    for spec in AUDIT_INDEXES:
        name = coll.create_index(spec["keys"], name=spec["name"])
        created.append(name)
    _INDEXED_DATABASES.add(db.name)
    return created


def _ensure_audit_indexes_once(db: Database) -> None:
    if db.name in _INDEXED_DATABASES:
        return
    ensure_audit_indexes(db)


def append_audit_event(
    db: Database,
    *,
    action: str,
    actor_email: str | None,
    target_email: str | None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert one audit row. Never stores passwords/hashes/tokens/secrets."""
    _ensure_audit_indexes_once(db)
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
    result = db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc
