"""Gate Design & Payment per PO — state, alasan, dan riwayat perubahan."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

GATES_COLLECTION = "po_gates"
GATE_HISTORY_COLLECTION = "po_gate_history"

GateState = Literal["closed", "open", "review"]

GATE_CLOSED: GateState = "closed"
GATE_OPEN: GateState = "open"
GATE_REVIEW: GateState = "review"

REQUIRED_GATE_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": GATES_COLLECTION,
        "keys": [("accurate_po_id", ASCENDING)],
        "name": "uniq_po_gates",
        "unique": True,
    },
    {
        "collection": GATE_HISTORY_COLLECTION,
        "keys": [("accurate_po_id", ASCENDING), ("created_at", ASCENDING)],
        "name": "idx_gate_history_po",
        "unique": False,
    },
]


def gates_collection(db: Database) -> Collection:
    return db[GATES_COLLECTION]


def gate_history_collection(db: Database) -> Collection:
    return db[GATE_HISTORY_COLLECTION]


def ensure_gate_indexes(db: Database) -> list[str]:
    """Buat indeks gate — HANYA bootstrap/maintenance/test."""
    created: list[str] = []
    for spec in REQUIRED_GATE_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified([s["name"] for s in REQUIRED_GATE_INDEX_SPECS], db=db)
    return created


def require_gate_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_GATE_INDEX_SPECS)


def inspect_gate_indexes(db: Database) -> dict[str, Any]:
    from app.services.index_preflight import inspect_index_specs

    return inspect_index_specs(db, REQUIRED_GATE_INDEX_SPECS)


def _ensure_once(db: Database, *, session: Any | None = None) -> None:
    """Alias request-path: require (bukan lazy createIndex). Skip bila dalam txn."""
    if session is None:
        require_gate_indexes(db)


_DEFAULT_GATES: dict[str, dict[str, Any]] = {
    "design": {"state": GATE_CLOSED, "reason": "Belum ada approval desain."},
    "payment": {"state": GATE_CLOSED, "reason": "Belum ada penerimaan terverifikasi."},
}


def _merge_gate_defaults(
    accurate_po_id: str, doc: dict[str, Any] | None
) -> dict[str, Any]:
    merged: dict[str, Any] = {"accurate_po_id": accurate_po_id}
    merged.update(doc or {})
    for gate, default in _DEFAULT_GATES.items():
        if not isinstance(merged.get(gate), dict):
            merged[gate] = dict(default)
    return merged


def get_gates(
    db: Database, accurate_po_id: str, *, session: Any | None = None
) -> dict[str, Any]:
    """Selalu mengembalikan kedua gate; yang belum pernah di-set tetap tertutup."""
    _ensure_once(db, session=session)
    doc = gates_collection(db).find_one(
        {"accurate_po_id": accurate_po_id}, {"_id": 0}, session=session
    )
    return _merge_gate_defaults(accurate_po_id, doc)


def get_gates_many(
    db: Database, accurate_po_ids: list[str], *, session: Any | None = None
) -> dict[str, dict[str, Any]]:
    """Batch get_gates untuk list Inbox — satu query."""
    _ensure_once(db, session=session)
    ids = [str(i) for i in accurate_po_ids if str(i or "").strip()]
    if not ids:
        return {}
    found = {
        str(d["accurate_po_id"]): d
        for d in gates_collection(db).find(
            {"accurate_po_id": {"$in": ids}}, {"_id": 0}, session=session
        )
        if d.get("accurate_po_id") is not None
    }
    return {pid: _merge_gate_defaults(pid, found.get(pid)) for pid in ids}


def set_gate(
    db: Database,
    *,
    accurate_po_id: str,
    gate: Literal["design", "payment"],
    state: GateState,
    reason: str,
    actor_email: str | None = None,
    detail: dict[str, Any] | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Ubah state satu gate + catat riwayat (append-only)."""
    _ensure_once(db, session=session)
    now = datetime.now(timezone.utc)
    payload = {
        "state": state,
        "reason": reason,
        "updated_at": now,
        "updated_by": actor_email,
        "detail": dict(detail or {}),
    }
    doc = gates_collection(db).find_one_and_update(
        {"accurate_po_id": accurate_po_id},
        {
            "$set": {gate: payload, "updated_at": now},
            "$setOnInsert": {"accurate_po_id": accurate_po_id, "created_at": now},
        },
        upsert=True,
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    gate_history_collection(db).insert_one(
        {
            "accurate_po_id": accurate_po_id,
            "gate": gate,
            "state": state,
            "reason": reason,
            "actor_email": actor_email,
            "detail": dict(detail or {}),
            "created_at": now,
        },
        session=session,
    )
    return doc


def claim_payment_bypass(
    db: Database,
    *,
    accurate_po_id: str,
    reason: str,
    actor_email: str,
    session: Any,
) -> dict[str, Any] | None:
    """CAS: buka payment gate sebagai bypass hanya jika belum open.

    Mengembalikan dokumen gate setelah klaim, atau ``None`` jika payment
    sudah ``open`` (pemenang lain / open normal / sudah bypass).
    History ditulis di sini; audit tetap tanggung jawab pemanggil dalam txn sama.
    """
    _ensure_once(db, session=session)
    now = datetime.now(timezone.utc)
    # Skeleton agar filter payment.state bisa dievaluasi.
    gates_collection(db).update_one(
        {"accurate_po_id": accurate_po_id},
        {
            "$setOnInsert": {
                "accurate_po_id": accurate_po_id,
                "created_at": now,
                "design": dict(_DEFAULT_GATES["design"]),
                "payment": dict(_DEFAULT_GATES["payment"]),
            }
        },
        upsert=True,
        session=session,
    )
    payload = {
        "state": GATE_OPEN,
        "reason": reason,
        "updated_at": now,
        "updated_by": actor_email,
        "detail": {"bypass": True},
    }
    doc = gates_collection(db).find_one_and_update(
        {
            "accurate_po_id": accurate_po_id,
            "payment.state": {"$ne": GATE_OPEN},
        },
        {"$set": {"payment": payload, "updated_at": now}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if doc is None:
        return None
    gate_history_collection(db).insert_one(
        {
            "accurate_po_id": accurate_po_id,
            "gate": "payment",
            "state": GATE_OPEN,
            "reason": reason,
            "actor_email": actor_email,
            "detail": {"bypass": True},
            "created_at": now,
        },
        session=session,
    )
    return _merge_gate_defaults(accurate_po_id, doc)


def gate_history(
    db: Database, accurate_po_id: str, *, session: Any | None = None
) -> list[dict[str, Any]]:
    _ensure_once(db, session=session)
    return list(
        gate_history_collection(db)
        .find({"accurate_po_id": accurate_po_id}, {"_id": 0}, session=session)
        .sort("created_at", ASCENDING)
    )


def both_gates_open(
    db: Database, accurate_po_id: str, *, session: Any | None = None
) -> bool:
    gates = get_gates(db, accurate_po_id, session=session)
    return (
        gates.get("design", {}).get("state") == GATE_OPEN
        and gates.get("payment", {}).get("state") == GATE_OPEN
    )
