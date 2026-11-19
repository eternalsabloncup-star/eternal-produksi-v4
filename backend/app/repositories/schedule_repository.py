"""Alokasi penjadwalan per item line Cetak + riwayat revisi append-only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

ALLOCATIONS_COLLECTION = "production_allocations"
ALLOCATION_HISTORY_COLLECTION = "production_allocation_history"
SCHEDULE_COMMANDS_COLLECTION = "schedule_commands"

STATUS_ACTIVE = "active"
STATUS_RELEASED = "released"

_INDEXED_DATABASES: set[str] = set()


def allocations_collection(db: Database) -> Collection:
    return db[ALLOCATIONS_COLLECTION]


def allocation_history_collection(db: Database) -> Collection:
    return db[ALLOCATION_HISTORY_COLLECTION]


def schedule_commands_collection(db: Database) -> Collection:
    return db[SCHEDULE_COMMANDS_COLLECTION]


def ensure_schedule_indexes(db: Database) -> list[str]:
    created = [
        allocations_collection(db).create_index(
            [("allocation_id", ASCENDING)], name="uniq_allocation_id", unique=True
        ),
        allocations_collection(db).create_index(
            [("accurate_line_id", ASCENDING), ("status", ASCENDING)],
            name="idx_allocation_line_status",
        ),
        allocation_history_collection(db).create_index(
            [("allocation_id", ASCENDING), ("created_at", ASCENDING)],
            name="idx_allocation_history",
        ),
        schedule_commands_collection(db).create_index(
            [("idempotency_key", ASCENDING)],
            name="uniq_schedule_command",
            unique=True,
        ),
    ]
    _INDEXED_DATABASES.add(db.name)
    return created


def _ensure_once(db: Database) -> None:
    if db.name not in _INDEXED_DATABASES:
        ensure_schedule_indexes(db)


def list_allocations(
    db: Database,
    *,
    accurate_line_id: str,
    include_released: bool = False,
) -> list[dict[str, Any]]:
    _ensure_once(db)
    query: dict[str, Any] = {"accurate_line_id": accurate_line_id}
    if not include_released:
        query["status"] = STATUS_ACTIVE
    return list(
        allocations_collection(db).find(query, {"_id": 0}).sort("created_at", ASCENDING)
    )


def get_allocation(db: Database, allocation_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return allocations_collection(db).find_one({"allocation_id": allocation_id}, {"_id": 0})


def active_allocated_qty(db: Database, accurate_line_id: str) -> float:
    rows = allocations_collection(db).aggregate(
        [
            {"$match": {"accurate_line_id": accurate_line_id, "status": STATUS_ACTIVE}},
            {"$group": {"_id": None, "total": {"$sum": "$qty"}}},
        ]
    )
    for row in rows:
        return float(row.get("total") or 0)
    return 0.0


def next_sequence(db: Database, accurate_line_id: str) -> int:
    _ensure_once(db)
    return (
        allocations_collection(db).count_documents(
            {"accurate_line_id": accurate_line_id}
        )
        + 1
    )


def insert_allocation(
    db: Database,
    *,
    allocation_id: str,
    accurate_po_id: str,
    po_no: str,
    accurate_line_id: str,
    item_code: str,
    operator_email: str,
    qty: float,
    schedule_date: str,
    created_by: str,
) -> dict[str, Any]:
    _ensure_once(db)
    now = datetime.now(timezone.utc)
    doc = {
        "allocation_id": allocation_id,
        "accurate_po_id": accurate_po_id,
        "po_no": po_no,
        "accurate_line_id": accurate_line_id,
        "item_code": item_code,
        "operator_email": operator_email,
        "qty": float(qty),
        "status": STATUS_ACTIVE,
        "schedule_date": schedule_date,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }
    try:
        allocations_collection(db).insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise ValueError(f"allocation_id duplikat: {allocation_id}") from exc
    append_history(
        db,
        allocation_id=allocation_id,
        action="create",
        actor_email=created_by,
        qty_before=None,
        qty_after=float(qty),
        reason="Alokasi jadwal awal",
    )
    doc.pop("_id", None)
    return doc


def update_allocation_qty(
    db: Database,
    *,
    allocation_id: str,
    qty: float,
    actor_email: str,
    reason: str,
) -> dict[str, Any]:
    _ensure_once(db)
    current = get_allocation(db, allocation_id)
    if current is None:
        raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}")
    now = datetime.now(timezone.utc)
    updated = allocations_collection(db).find_one_and_update(
        {"allocation_id": allocation_id},
        {
            "$set": {
                "qty": float(qty),
                "status": STATUS_RELEASED if qty == 0 else STATUS_ACTIVE,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    append_history(
        db,
        allocation_id=allocation_id,
        action="update_qty",
        actor_email=actor_email,
        qty_before=float(current["qty"]),
        qty_after=float(qty),
        reason=reason,
    )
    return updated


def append_history(
    db: Database,
    *,
    allocation_id: str,
    action: str,
    actor_email: str,
    qty_before: float | None,
    qty_after: float | None,
    reason: str,
) -> dict[str, Any]:
    _ensure_once(db)
    doc = {
        "allocation_id": allocation_id,
        "action": action,
        "actor_email": actor_email,
        "qty_before": qty_before,
        "qty_after": qty_after,
        "reason": reason,
        "created_at": datetime.now(timezone.utc),
    }
    allocation_history_collection(db).insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


def allocation_history(db: Database, allocation_id: str) -> list[dict[str, Any]]:
    _ensure_once(db)
    return list(
        allocation_history_collection(db)
        .find({"allocation_id": allocation_id}, {"_id": 0})
        .sort("created_at", ASCENDING)
    )


def remember_command(
    db: Database, *, idempotency_key: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    """Simpan hasil perintah penjadwalan; None jika kunci sudah pernah dipakai."""
    _ensure_once(db)
    try:
        schedule_commands_collection(db).insert_one(
            {
                "idempotency_key": idempotency_key,
                "result": result,
                "created_at": datetime.now(timezone.utc),
            }
        )
    except DuplicateKeyError:
        return None
    return result


def recall_command(db: Database, idempotency_key: str) -> dict[str, Any] | None:
    _ensure_once(db)
    doc = schedule_commands_collection(db).find_one(
        {"idempotency_key": idempotency_key}, {"_id": 0}
    )
    return (doc or {}).get("result")
