"""Adapter baca-saja ke kontrak workflow V4 (koleksi PR#6) tanpa mengimpor modulnya.

Jika koleksi belum ada / kosong → sinyal ``available=False`` agar milestone Pending
jujur, bukan dikarang selesai.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure, PyMongoError

# Nama koleksi mengikuti kontrak PR#6 (Checkpoint A–B) — jangan diganti lokal.
COLL_GATES = "po_gates"
COLL_TASKS = "operational_tasks"
COLL_DESIGN_SUBMISSIONS = "design_submissions"
COLL_DESIGN_APPROVALS = "design_approvals"
COLL_SCHEDULES = "production_schedules"
COLL_DELIVERY_NOTES = "delivery_notes"
COLL_DELIVERY_EVIDENCE = "delivery_evidence"
COLL_PAYMENTS = "payment_receipts"


def _safe_find(
    db: Database,
    collection: str,
    query: dict[str, Any],
    *,
    projection: dict[str, Any] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    try:
        if collection not in db.list_collection_names():
            return []
        cur = db[collection].find(query, projection or {"_id": 0}).limit(limit)
        return list(cur)
    except (PyMongoError, OperationFailure):
        return []


def _safe_find_one(
    db: Database,
    collection: str,
    query: dict[str, Any],
    *,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    rows = _safe_find(db, collection, query, projection=projection, limit=1)
    return rows[0] if rows else None


def collection_available(db: Database, name: str) -> bool:
    try:
        return name in db.list_collection_names()
    except (PyMongoError, OperationFailure):
        return False


def load_workflow_snapshot(db: Database, accurate_po_id: str) -> dict[str, Any]:
    """Snapshot bukti event untuk mapping milestone — tanpa side effect."""
    po_id = str(accurate_po_id)
    gates_doc = _safe_find_one(db, COLL_GATES, {"accurate_po_id": po_id})
    tasks = _safe_find(db, COLL_TASKS, {"ref.accurate_po_id": po_id})
    design_subs = _safe_find(db, COLL_DESIGN_SUBMISSIONS, {"accurate_po_id": po_id})
    design_appr = _safe_find(db, COLL_DESIGN_APPROVALS, {"accurate_po_id": po_id})
    schedules = _safe_find(db, COLL_SCHEDULES, {"accurate_po_id": po_id})
    # Soft-delete filter: soft_deleted != True
    delivery_notes = _safe_find(
        db,
        COLL_DELIVERY_NOTES,
        {"accurate_po_id": po_id, "soft_deleted": {"$ne": True}},
    )
    evidence = _safe_find(
        db,
        COLL_DELIVERY_EVIDENCE,
        {"accurate_po_id": po_id, "soft_deleted": {"$ne": True}},
    )
    payments = _safe_find(db, COLL_PAYMENTS, {"accurate_po_id": po_id})

    by_type: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        tt = str(t.get("task_type") or "")
        by_type.setdefault(tt, []).append(t)

    return {
        "accurate_po_id": po_id,
        "availability": {
            "gates": collection_available(db, COLL_GATES),
            "tasks": collection_available(db, COLL_TASKS),
            "design": collection_available(db, COLL_DESIGN_SUBMISSIONS),
            "schedules": collection_available(db, COLL_SCHEDULES),
            "delivery_notes": collection_available(db, COLL_DELIVERY_NOTES),
            "delivery_evidence": collection_available(db, COLL_DELIVERY_EVIDENCE),
            "payments": collection_available(db, COLL_PAYMENTS),
        },
        "gates": gates_doc,
        "tasks_by_type": by_type,
        "tasks": tasks,
        "design_submissions": design_subs,
        "design_approvals": design_appr,
        "schedules": schedules,
        "delivery_notes": delivery_notes,
        "delivery_evidence": evidence,
        "payments": payments,
    }


def task_aggregate(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not tasks:
        return {
            "present": False,
            "status": None,
            "qty_target": 0.0,
            "qty_confirmed": 0.0,
            "updated_at": None,
        }
    statuses = [str(t.get("status") or "") for t in tasks]
    qty_target = sum(float(t.get("qty_target") or 0) for t in tasks)
    qty_confirmed = sum(float(t.get("qty_confirmed") or 0) for t in tasks)
    done = all(s == "selesai" for s in statuses)
    partial = any(s in ("sebagian", "selesai") for s in statuses)
    if done:
        status = "selesai"
    elif partial:
        status = "sebagian"
    else:
        status = "belum_dikerjakan"
    timestamps: list[datetime] = []
    for t in tasks:
        for key in ("updated_at", "completed_at", "created_at"):
            val = t.get(key)
            if isinstance(val, datetime):
                timestamps.append(val)
    updated = max(timestamps) if timestamps else None
    return {
        "present": True,
        "status": status,
        "qty_target": qty_target,
        "qty_confirmed": qty_confirmed,
        "updated_at": updated,
        "count": len(tasks),
    }


def gate_state(gates_doc: dict[str, Any] | None, gate: str) -> str | None:
    if not gates_doc:
        return None
    node = gates_doc.get(gate)
    if not isinstance(node, dict):
        return None
    return node.get("state")


def as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None
