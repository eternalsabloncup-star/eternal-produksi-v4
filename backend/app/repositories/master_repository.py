"""Master/Pengaturan — jenis pekerjaan, pemetaan role, permission aksi.

Default Owner di-seed idempoten, tetapi konfigurasi tetap milik Master:
Owner dapat memindahkan jenis pekerjaan ke role lain tanpa ubah kode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

from app.domain.operational import (
    DEFAULT_TASK_TYPES,
    OPERATIONAL_ROLES,
    TASK_ACTIONS,
    TaskTypeSpec,
)

TASK_TYPES_COLLECTION = "master_task_types"
ROLE_ACTION_COLLECTION = "master_role_actions"

MASTER_INDEXES: dict[str, list[dict[str, Any]]] = {
    TASK_TYPES_COLLECTION: [
        {
            "keys": [("task_type", ASCENDING)],
            "name": "uniq_master_task_type",
            "unique": True,
        }
    ],
    ROLE_ACTION_COLLECTION: [
        {
            "keys": [("role", ASCENDING), ("task_type", ASCENDING)],
            "name": "uniq_master_role_task",
            "unique": True,
        }
    ],
}


def task_types_collection(db: Database) -> Collection:
    return db[TASK_TYPES_COLLECTION]


def role_actions_collection(db: Database) -> Collection:
    return db[ROLE_ACTION_COLLECTION]


_INDEXED_DATABASES: set[str] = set()


def ensure_master_indexes(db: Database) -> list[str]:
    created: list[str] = []
    for coll_name, specs in MASTER_INDEXES.items():
        coll = db[coll_name]
        for spec in specs:
            created.append(
                coll.create_index(
                    spec["keys"],
                    name=spec["name"],
                    unique=spec.get("unique", False),
                )
            )
    _INDEXED_DATABASES.add(db.name)
    return created


def _ensure_master_indexes_once(db: Database) -> None:
    if db.name in _INDEXED_DATABASES:
        return
    ensure_master_indexes(db)


def _spec_to_doc(spec: TaskTypeSpec, now: datetime) -> dict[str, Any]:
    return {
        "task_type": spec.task_type,
        "label": spec.label,
        "scope": spec.scope,
        "assigned_role": spec.default_role,
        "actions": list(spec.actions),
        "coordination": spec.coordination,
        "partial_qty": spec.partial_qty,
        "is_default_mapping": True,
        "updated_at": now,
    }


def seed_master_defaults(db: Database) -> dict[str, int]:
    """Seed default terkunci. Tidak menimpa konfigurasi yang sudah diubah Owner."""
    _ensure_master_indexes_once(db)
    now = datetime.now(timezone.utc)
    types_coll = task_types_collection(db)
    actions_coll = role_actions_collection(db)

    inserted_types = 0
    inserted_grants = 0

    for spec in DEFAULT_TASK_TYPES:
        res = types_coll.update_one(
            {"task_type": spec.task_type},
            {
                "$setOnInsert": {**_spec_to_doc(spec, now), "created_at": now},
            },
            upsert=True,
        )
        if res.upserted_id is not None:
            inserted_types += 1

        grant = actions_coll.update_one(
            {"role": spec.default_role, "task_type": spec.task_type},
            {
                "$setOnInsert": {
                    "role": spec.default_role,
                    "task_type": spec.task_type,
                    "actions": list(spec.actions),
                    "is_default_mapping": True,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        if grant.upserted_id is not None:
            inserted_grants += 1

    return {"task_types": inserted_types, "role_actions": inserted_grants}


def list_task_types(db: Database) -> list[dict[str, Any]]:
    return list(task_types_collection(db).find({}, {"_id": 0}).sort("task_type", ASCENDING))


def get_task_type(db: Database, task_type: str) -> dict[str, Any] | None:
    return task_types_collection(db).find_one({"task_type": task_type}, {"_id": 0})


def set_task_type_role(
    db: Database,
    *,
    task_type: str,
    role: str,
) -> dict[str, Any]:
    """Pindahkan jenis pekerjaan ke role lain (konfigurasi Master)."""
    if role not in OPERATIONAL_ROLES:
        raise ValueError(f"Role operasional tidak dikenal: {role}")
    doc = task_types_collection(db).find_one_and_update(
        {"task_type": task_type},
        {
            "$set": {
                "assigned_role": role,
                "is_default_mapping": False,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise LookupError(f"Jenis pekerjaan tidak ditemukan: {task_type}")
    return doc


def set_role_actions(
    db: Database,
    *,
    role: str,
    task_type: str,
    actions: list[str],
) -> dict[str, Any]:
    """Berikan/cabut izin aksi granular untuk satu role pada satu jenis pekerjaan."""
    if role not in OPERATIONAL_ROLES:
        raise ValueError(f"Role operasional tidak dikenal: {role}")
    unknown = [a for a in actions if a not in TASK_ACTIONS]
    if unknown:
        raise ValueError(f"Aksi tidak dikenal: {', '.join(sorted(unknown))}")
    if get_task_type(db, task_type) is None:
        raise LookupError(f"Jenis pekerjaan tidak ditemukan: {task_type}")

    now = datetime.now(timezone.utc)
    doc = role_actions_collection(db).find_one_and_update(
        {"role": role, "task_type": task_type},
        {
            "$set": {
                "actions": sorted(set(actions)),
                "is_default_mapping": False,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return doc


def list_role_actions(db: Database, *, role: str | None = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {} if role is None else {"role": role}
    return list(
        role_actions_collection(db)
        .find(query, {"_id": 0})
        .sort([("role", ASCENDING), ("task_type", ASCENDING)])
    )


def role_can(db: Database, *, role: str, task_type: str, action: str) -> bool:
    """Izin operasional dari Master — bukan dari kondisi hardcoded frontend."""
    doc = role_actions_collection(db).find_one(
        {"role": role, "task_type": task_type}, {"_id": 0, "actions": 1}
    )
    if not doc:
        return False
    return action in (doc.get("actions") or [])


def assigned_role_for(db: Database, task_type: str) -> str | None:
    doc = get_task_type(db, task_type)
    if not doc:
        return None
    return doc.get("assigned_role")
