"""M9.3E — append-only invoice administrative review storage.

Separate from Accurate invoice snapshots so Invoice Auto-Sync ``$set`` cannot
overwrite administrative evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

EVENTS_COLLECTION = "invoice_admin_review_events"
STATE_COLLECTION = "invoice_admin_review_state"

REQUIRED_ADMIN_REVIEW_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": EVENTS_COLLECTION,
        "keys": [("idempotency_key", ASCENDING)],
        "name": "uniq_invoice_admin_review_idempotency",
        "unique": True,
    },
    {
        "collection": EVENTS_COLLECTION,
        "keys": [("accurate_invoice_id", ASCENDING), ("created_at", ASCENDING)],
        "name": "idx_invoice_admin_review_events_invoice_created",
        "unique": False,
    },
    {
        "collection": STATE_COLLECTION,
        "keys": [("accurate_invoice_id", ASCENDING)],
        "name": "uniq_invoice_admin_review_state_invoice",
        "unique": True,
    },
]


def events_collection(db: Database) -> Collection:
    return db[EVENTS_COLLECTION]


def state_collection(db: Database) -> Collection:
    return db[STATE_COLLECTION]


def ensure_admin_review_indexes(db: Database) -> list[str]:
    """Bootstrap/test only — never on production request path without deploy auth."""
    created: list[str] = []
    for spec in REQUIRED_ADMIN_REVIEW_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_ADMIN_REVIEW_INDEX_SPECS], db=db
    )
    return created


def require_admin_review_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_ADMIN_REVIEW_INDEX_SPECS)


def get_state(db: Database, accurate_invoice_id: str) -> dict[str, Any] | None:
    require_admin_review_indexes(db)
    return state_collection(db).find_one(
        {"accurate_invoice_id": str(accurate_invoice_id)}, {"_id": 0}
    )


def find_event_by_idempotency(
    db: Database, idempotency_key: str
) -> dict[str, Any] | None:
    require_admin_review_indexes(db)
    return events_collection(db).find_one(
        {"idempotency_key": str(idempotency_key)}, {"_id": 0}
    )


def list_events(
    db: Database,
    accurate_invoice_id: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    require_admin_review_indexes(db)
    return list(
        events_collection(db)
        .find({"accurate_invoice_id": str(accurate_invoice_id)}, {"_id": 0})
        .sort("created_at", ASCENDING)
        .limit(max(1, min(int(limit), 500)))
    )


def insert_event(
    db: Database,
    event: dict[str, Any],
    *,
    session: Any | None = None,
) -> dict[str, Any]:
    """Append-only insert. Unique idempotency_key prevents duplicates."""
    if session is None:
        require_admin_review_indexes(db)
    doc = dict(event)
    doc.setdefault("created_at", datetime.now(timezone.utc))
    events_collection(db).insert_one(doc, session=session)
    return {k: v for k, v in doc.items() if k != "_id"}


def cas_upsert_state(
    db: Database,
    *,
    accurate_invoice_id: str,
    expected_version: int,
    new_state: dict[str, Any],
    session: Any | None = None,
) -> dict[str, Any] | None:
    """Optimistic concurrency on ``version``.

    ``expected_version=0`` means no prior state (insert). Returns updated state
    or ``None`` on conflict.
    """
    if session is None:
        require_admin_review_indexes(db)
    inv_id = str(accurate_invoice_id)
    now = datetime.now(timezone.utc)
    payload = {
        **new_state,
        "accurate_invoice_id": inv_id,
        "version": int(expected_version) + 1,
        "updated_at": now,
    }
    if expected_version == 0:
        try:
            state_collection(db).insert_one(
                {**payload, "created_at": now}, session=session
            )
        except DuplicateKeyError:
            return None
        return state_collection(db).find_one(
            {"accurate_invoice_id": inv_id}, {"_id": 0}, session=session
        )

    updated = state_collection(db).find_one_and_update(
        {"accurate_invoice_id": inv_id, "version": int(expected_version)},
        {"$set": payload},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if updated is None:
        return None
    updated.pop("_id", None)
    return updated
