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
LINE_GUARDS_COLLECTION = "schedule_line_guards"

STATUS_ACTIVE = "active"
STATUS_RELEASED = "released"

REQUIRED_SCHEDULE_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": ALLOCATIONS_COLLECTION,
        "keys": [("allocation_id", ASCENDING)],
        "name": "uniq_allocation_id",
        "unique": True,
    },
    {
        "collection": ALLOCATIONS_COLLECTION,
        "keys": [("accurate_line_id", ASCENDING), ("status", ASCENDING)],
        "name": "idx_allocation_line_status",
        "unique": False,
    },
    {
        "collection": ALLOCATION_HISTORY_COLLECTION,
        "keys": [("allocation_id", ASCENDING), ("created_at", ASCENDING)],
        "name": "idx_allocation_history",
        "unique": False,
    },
    {
        "collection": SCHEDULE_COMMANDS_COLLECTION,
        "keys": [("idempotency_key", ASCENDING)],
        "name": "uniq_schedule_command",
        "unique": True,
    },
    {
        "collection": LINE_GUARDS_COLLECTION,
        "keys": [("accurate_line_id", ASCENDING)],
        "name": "uniq_schedule_line_guard",
        "unique": True,
    },
]


def allocations_collection(db: Database) -> Collection:
    return db[ALLOCATIONS_COLLECTION]


def allocation_history_collection(db: Database) -> Collection:
    return db[ALLOCATION_HISTORY_COLLECTION]


def schedule_commands_collection(db: Database) -> Collection:
    return db[SCHEDULE_COMMANDS_COLLECTION]


def ensure_schedule_indexes(db: Database) -> list[str]:
    """Buat indeks schedule — HANYA bootstrap/maintenance/test."""
    created: list[str] = []
    for spec in REQUIRED_SCHEDULE_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_SCHEDULE_INDEX_SPECS], db=db
    )
    return created


def require_schedule_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_SCHEDULE_INDEX_SPECS)


def inspect_schedule_indexes(db: Database) -> dict[str, Any]:
    from app.services.index_preflight import inspect_index_specs

    return inspect_index_specs(db, REQUIRED_SCHEDULE_INDEX_SPECS)


def _ensure_once(db: Database) -> None:
    """Alias request-path: require (bukan lazy createIndex)."""
    require_schedule_indexes(db)


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


def get_allocation(
    db: Database, allocation_id: str, *, session: Any | None = None
) -> dict[str, Any] | None:
    if session is None:
        _ensure_once(db)
    return allocations_collection(db).find_one(
        {"allocation_id": allocation_id}, {"_id": 0}, session=session
    )


def active_allocated_qty(
    db: Database, accurate_line_id: str, *, session: Any | None = None
) -> float:
    rows = allocations_collection(db).aggregate(
        [
            {"$match": {"accurate_line_id": accurate_line_id, "status": STATUS_ACTIVE}},
            {"$group": {"_id": None, "total": {"$sum": "$qty"}}},
        ],
        session=session,
    )
    for row in rows:
        return float(row.get("total") or 0)
    return 0.0


def touch_line_guard(
    db: Database, accurate_line_id: str, *, session: Any | None = None
) -> None:
    """Serialize concurrent capacity changes for one PO item line inside a txn."""
    if session is None:
        _ensure_once(db)
    db[LINE_GUARDS_COLLECTION].find_one_and_update(
        {"accurate_line_id": accurate_line_id},
        {
            "$inc": {"seq": 1},
            "$set": {"updated_at": datetime.now(timezone.utc)},
            "$setOnInsert": {"accurate_line_id": accurate_line_id},
        },
        upsert=True,
        session=session,
    )


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
    print_color: str | None = None,
    screen_kind: str | None = None,
    branch_code: str | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    if session is None:
        _ensure_once(db)
    now = datetime.now(timezone.utc)
    doc: dict[str, Any] = {
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
    # Opsional — record lama tanpa field tetap valid; jangan isi default palsu.
    if print_color is not None:
        doc["print_color"] = print_color
    if screen_kind is not None:
        doc["screen_kind"] = screen_kind
    if branch_code is not None and str(branch_code).strip():
        doc["branch_code"] = str(branch_code).strip().upper()
    try:
        allocations_collection(db).insert_one(dict(doc), session=session)
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
        session=session,
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
    expected_qty: float | None = None,
    session: Any | None = None,
) -> dict[str, Any] | None:
    """Update Qty alokasi. None = gagal CAS (expected_qty tidak cocok)."""
    if session is None:
        _ensure_once(db)
    current = get_allocation(db, allocation_id, session=session)
    if current is None:
        raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}")
    now = datetime.now(timezone.utc)
    filt: dict[str, Any] = {"allocation_id": allocation_id}
    if expected_qty is not None:
        filt["qty"] = float(expected_qty)
    updated = allocations_collection(db).find_one_and_update(
        filt,
        {
            "$set": {
                "qty": float(qty),
                "status": STATUS_RELEASED if qty == 0 else STATUS_ACTIVE,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if updated is None:
        return None
    append_history(
        db,
        allocation_id=allocation_id,
        action="update_qty",
        actor_email=actor_email,
        qty_before=float(current["qty"]),
        qty_after=float(qty),
        reason=reason,
        session=session,
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
    session: Any | None = None,
) -> dict[str, Any]:
    if session is None:
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
    allocation_history_collection(db).insert_one(dict(doc), session=session)
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
