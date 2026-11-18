"""Task engine operasional — tugas idempoten + ledger konfirmasi append-only.

Invarian:
- Satu unit tugas = satu ``task_key`` (tidak ganda saat refresh/sync/retry).
- Konfirmasi parsial berulang diizinkan; setiap konfirmasi tersimpan.
- Qty terkonfirmasi tidak pernah melampaui target dan tidak pernah dobel.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.domain.operational import TaskRef, build_task_key, task_ref_to_document

TASKS_COLLECTION = "operational_tasks"
CONFIRMATIONS_COLLECTION = "task_confirmations"

TASK_STATUS_OPEN = "belum_dikerjakan"
TASK_STATUS_PARTIAL = "sebagian"
TASK_STATUS_DONE = "selesai"


class TaskQuantityError(ValueError):
    """Konfirmasi melampaui Qty target atau kuota yang tersedia."""


def tasks_collection(db: Database) -> Collection:
    return db[TASKS_COLLECTION]


def confirmations_collection(db: Database) -> Collection:
    return db[CONFIRMATIONS_COLLECTION]


# Index hanya dibuat sekali per database dalam proses ini; jalur tulis panas
# tidak boleh mengirim createIndex pada setiap konfirmasi.
_INDEXED_DATABASES: set[str] = set()


def _ensure_task_indexes_once(db: Database) -> None:
    if db.name in _INDEXED_DATABASES:
        return
    ensure_task_indexes(db)


def ensure_task_indexes(db: Database) -> list[str]:
    created: list[str] = []
    tasks = tasks_collection(db)
    created.append(
        tasks.create_index([("task_key", ASCENDING)], name="uniq_task_key", unique=True)
    )
    created.append(
        tasks.create_index(
            [("assigned_role", ASCENDING), ("status", ASCENDING)],
            name="idx_task_role_status",
        )
    )
    created.append(
        tasks.create_index(
            [("ref.accurate_po_id", ASCENDING), ("task_type", ASCENDING)],
            name="idx_task_po_type",
        )
    )

    conf = confirmations_collection(db)
    created.append(
        conf.create_index(
            [("idempotency_key", ASCENDING)],
            name="uniq_confirmation_idempotency",
            unique=True,
        )
    )
    created.append(
        conf.create_index(
            [("task_key", ASCENDING), ("created_at", DESCENDING)],
            name="idx_confirmation_task_created",
        )
    )
    _INDEXED_DATABASES.add(db.name)
    return created


def _status_for(qty_target: float | int | None, qty_confirmed: float | int) -> str:
    if qty_target is None:
        return TASK_STATUS_DONE if qty_confirmed > 0 else TASK_STATUS_OPEN
    if qty_confirmed <= 0:
        return TASK_STATUS_OPEN
    if qty_confirmed >= qty_target:
        return TASK_STATUS_DONE
    return TASK_STATUS_PARTIAL


def upsert_task(
    db: Database,
    *,
    task_type: str,
    scope: str,
    assigned_role: str,
    ref: TaskRef,
    qty_target: float | int | None = None,
    label: str | None = None,
    coordination: bool = False,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Buat tugas bila belum ada. Pemanggilan ulang tidak menggandakan tugas."""
    _ensure_task_indexes_once(db)
    task_key = build_task_key(task_type, scope, ref)  # type: ignore[arg-type]
    now = datetime.now(timezone.utc)

    on_insert: dict[str, Any] = {
        "task_key": task_key,
        "task_type": task_type,
        "scope": scope,
        "ref": task_ref_to_document(ref),
        "qty_confirmed": 0,
        "status": TASK_STATUS_OPEN,
        "created_at": now,
    }
    always_set: dict[str, Any] = {
        "assigned_role": assigned_role,
        "coordination": bool(coordination),
        "updated_at": now,
    }
    if label is not None:
        always_set["label"] = label
    if qty_target is not None:
        always_set["qty_target"] = qty_target
    if context is not None:
        always_set["context"] = context

    doc = tasks_collection(db).find_one_and_update(
        {"task_key": task_key},
        {"$setOnInsert": on_insert, "$set": always_set},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


def get_task(db: Database, task_key: str) -> dict[str, Any] | None:
    return tasks_collection(db).find_one({"task_key": task_key}, {"_id": 0})


def list_tasks(
    db: Database,
    *,
    assigned_role: str | None = None,
    task_type: str | None = None,
    done: bool | None = None,
    accurate_po_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if assigned_role:
        query["assigned_role"] = assigned_role
    if task_type:
        query["task_type"] = task_type
    if accurate_po_id:
        query["ref.accurate_po_id"] = accurate_po_id
    if done is True:
        query["status"] = TASK_STATUS_DONE
    elif done is False:
        query["status"] = {"$ne": TASK_STATUS_DONE}
    cursor = tasks_collection(db).find(query, {"_id": 0}).sort("created_at", ASCENDING)
    return list(cursor.limit(max(1, min(limit, 1000))))


def list_confirmations(db: Database, task_key: str, limit: int = 200) -> list[dict[str, Any]]:
    return list(
        confirmations_collection(db)
        .find({"task_key": task_key}, {"_id": 0})
        .sort("created_at", ASCENDING)
        .limit(max(1, min(limit, 1000)))
    )


def confirm_task_qty(
    db: Database,
    *,
    task_key: str,
    qty: float | int,
    actor_email: str,
    actor_role: str,
    idempotency_key: str,
    note: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Konfirmasi Qty parsial secara atomik.

    - ``idempotency_key`` sama → tidak menambah Qty (retry/double-click aman).
    - Qty melebihi target → ditolak, tidak ada penambahan parsial.
    """
    if qty <= 0:
        raise TaskQuantityError("Qty konfirmasi harus lebih besar dari nol.")
    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("idempotency_key wajib diisi.")

    _ensure_task_indexes_once(db)
    task = get_task(db, task_key)
    if task is None:
        raise LookupError(f"Tugas tidak ditemukan: {task_key}")

    now = datetime.now(timezone.utc)
    ledger_doc = {
        "idempotency_key": idempotency_key.strip(),
        "task_key": task_key,
        "task_type": task.get("task_type"),
        "ref": task.get("ref") or {},
        "qty": qty,
        "actor_email": actor_email,
        "actor_role": actor_role,
        "note": note,
        "extra": dict(extra or {}),
        "created_at": now,
    }

    # Ledger sebagai penjaga idempotency: insert gagal → konfirmasi sudah tercatat.
    try:
        confirmations_collection(db).insert_one(dict(ledger_doc))
    except DuplicateKeyError:
        existing = confirmations_collection(db).find_one(
            {"idempotency_key": idempotency_key.strip()}, {"_id": 0}
        )
        return {
            "task": get_task(db, task_key),
            "confirmation": existing,
            "duplicate": True,
        }

    qty_target = task.get("qty_target")
    update_filter: dict[str, Any] = {"task_key": task_key}
    if qty_target is not None:
        # Penjagaan atomik: dua aktor bersamaan tidak bisa melewati target.
        update_filter["$expr"] = {
            "$lte": [{"$add": ["$qty_confirmed", qty]}, "$qty_target"]
        }

    updated = tasks_collection(db).find_one_and_update(
        update_filter,
        {"$inc": {"qty_confirmed": qty}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        # Rollback entri ledger agar Qty dan riwayat tetap konsisten.
        confirmations_collection(db).delete_one(
            {"idempotency_key": idempotency_key.strip()}
        )
        raise TaskQuantityError(
            "Qty konfirmasi melebihi Qty target tugas yang tersedia."
        )

    status = _status_for(updated.get("qty_target"), updated.get("qty_confirmed", 0))
    final = tasks_collection(db).find_one_and_update(
        {"task_key": task_key},
        {"$set": {"status": status, "updated_at": now}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return {"task": final, "confirmation": ledger_doc, "duplicate": False}


def total_confirmed_qty(db: Database, task_key: str) -> float:
    rows = confirmations_collection(db).aggregate(
        [
            {"$match": {"task_key": task_key}},
            {"$group": {"_id": None, "total": {"$sum": "$qty"}}},
        ]
    )
    for row in rows:
        return float(row.get("total") or 0)
    return 0.0
