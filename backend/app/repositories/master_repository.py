"""Master/Pengaturan — jenis pekerjaan, pemetaan role, permission aksi.

Seed defaults hanya via maintenance/bootstrap/test eksplisit.
Request path: read-only require (fail-closed) — tanpa seed/createIndex.
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

REQUIRED_MASTER_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": TASK_TYPES_COLLECTION,
        "keys": [("task_type", ASCENDING)],
        "name": "uniq_master_task_type",
        "unique": True,
    },
    {
        "collection": ROLE_ACTION_COLLECTION,
        "keys": [("role", ASCENDING), ("task_type", ASCENDING)],
        "name": "uniq_master_role_task",
        "unique": True,
    },
]

# Alias spek untuk bootstrap/ensure (kompatibilitas).
MASTER_INDEXES: dict[str, list[dict[str, Any]]] = {
    TASK_TYPES_COLLECTION: [
        {
            "keys": spec["keys"],
            "name": spec["name"],
            "unique": bool(spec.get("unique")),
        }
        for spec in REQUIRED_MASTER_INDEX_SPECS
        if spec["collection"] == TASK_TYPES_COLLECTION
    ],
    ROLE_ACTION_COLLECTION: [
        {
            "keys": spec["keys"],
            "name": spec["name"],
            "unique": bool(spec.get("unique")),
        }
        for spec in REQUIRED_MASTER_INDEX_SPECS
        if spec["collection"] == ROLE_ACTION_COLLECTION
    ],
}


def task_types_collection(db: Database) -> Collection:
    return db[TASK_TYPES_COLLECTION]


def role_actions_collection(db: Database) -> Collection:
    return db[ROLE_ACTION_COLLECTION]


def ensure_master_indexes(db: Database) -> list[str]:
    """Buat indeks Master — HANYA bootstrap/maintenance/test eksplisit."""
    created: list[str] = []
    for spec in REQUIRED_MASTER_INDEX_SPECS:
        coll = db[spec["collection"]]
        created.append(
            coll.create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_MASTER_INDEX_SPECS], db=db
    )
    return created


def require_master_indexes(db: Database) -> None:
    """Fail-closed request path: listIndexes only, tidak createIndex."""
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_MASTER_INDEX_SPECS)


def inspect_master_indexes(db: Database) -> dict[str, Any]:
    """Read-only: required / present / missing / conflict."""
    from app.services.index_preflight import inspect_index_specs

    return inspect_index_specs(db, REQUIRED_MASTER_INDEX_SPECS)


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
    """Seed default terkunci. Hanya maintenance/bootstrap/test — bukan request.

    Tidak menimpa konfigurasi yang sudah diubah Owner ($setOnInsert).
    """
    ensure_master_indexes(db)
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

        # payment-bypass: tersedia di Master tetapi tidak di-grant otomatis ke role mana pun.
        # loading-sj: Helper tidak mendapat cancel (Owner: cancel = KP/Admin manual).
        if spec.task_type == "payment-bypass":
            seed_actions: list[str] = []
        elif spec.task_type == "loading-sj":
            seed_actions = ["view", "confirm", "edit"]
        else:
            seed_actions = list(spec.actions)
        grant = actions_coll.update_one(
            {"role": spec.default_role, "task_type": spec.task_type},
            {
                "$setOnInsert": {
                    "role": spec.default_role,
                    "task_type": spec.task_type,
                    "actions": seed_actions,
                    "is_default_mapping": True,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        if grant.upserted_id is not None:
            inserted_grants += 1

        # loading-sj → production-head: view/confirm/edit/cancel (Owner final; test/bootstrap).
        if spec.task_type == "loading-sj":
            kp_grant = actions_coll.update_one(
                {"role": "production-head", "task_type": "loading-sj"},
                {
                    "$setOnInsert": {
                        "role": "production-head",
                        "task_type": "loading-sj",
                        "actions": ["view", "confirm", "edit", "cancel"],
                        "is_default_mapping": True,
                        "created_at": now,
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
            if kp_grant.upserted_id is not None:
                inserted_grants += 1

    return {"task_types": inserted_types, "role_actions": inserted_grants}


def list_task_types(db: Database) -> list[dict[str, Any]]:
    require_master_indexes(db)
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
    require_master_indexes(db)
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
    require_master_indexes(db)
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
    require_master_indexes(db)
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


class MissingMasterConfigError(RuntimeError):
    """Konfigurasi Master wajib belum siap — mutasi ditolak (fail-closed)."""

    code = "MISSING_MASTER_CONFIG"


def require_task_type_assigned_role(db: Database, task_type: str) -> str:
    """Read-only: pastikan task_type Master sudah ada + assigned_role.

    Tidak createIndex, tidak seed/upsert. Bootstrap Master harus eksplisit.
    """
    require_master_indexes(db)
    doc = get_task_type(db, task_type)
    role = (doc or {}).get("assigned_role") if doc else None
    if not doc or not role:
        raise MissingMasterConfigError(
            f"Konfigurasi Master untuk task_type={task_type!r} belum siap "
            "(assigned_role wajib). Jalankan bootstrap Master sebelum operasi."
        )
    return str(role)
