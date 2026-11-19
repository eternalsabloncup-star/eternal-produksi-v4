"""Master cabang, mesin, shift — Settings V4 operasional dasar."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

BRANCHES = "master_branches"
MACHINES = "master_machines"
SHIFTS = "master_shifts"

REQUIRED_ORG_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": BRANCHES,
        "keys": [("code", ASCENDING)],
        "name": "uniq_master_branch_code",
        "unique": True,
    },
    {
        "collection": MACHINES,
        "keys": [("code", ASCENDING)],
        "name": "uniq_master_machine_code",
        "unique": True,
    },
    {
        "collection": SHIFTS,
        "keys": [("code", ASCENDING)],
        "name": "uniq_master_shift_code",
        "unique": True,
    },
]


def ensure_org_master_indexes(db: Database) -> list[str]:
    created: list[str] = []
    for spec in REQUIRED_ORG_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"], name=spec["name"], unique=bool(spec.get("unique"))
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified([s["name"] for s in REQUIRED_ORG_INDEX_SPECS], db=db)
    return created


def require_org_master_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_ORG_INDEX_SPECS)


def _coll(db: Database, name: str) -> Collection:
    return db[name]


def list_docs(db: Database, collection: str) -> list[dict[str, Any]]:
    require_org_master_indexes(db)
    return list(_coll(db, collection).find({}, {"_id": 0}).sort("code", ASCENDING))


def upsert_branch(
    db: Database, *, code: str, name: str, is_active: bool = True
) -> dict[str, Any]:
    require_org_master_indexes(db)
    c = code.strip().upper()
    n = name.strip()
    if not c or not n:
        raise ValueError("Kode dan nama cabang wajib.")
    now = datetime.now(timezone.utc)
    doc = _coll(db, BRANCHES).find_one_and_update(
        {"code": c},
        {
            "$set": {"code": c, "name": n, "is_active": is_active, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    assert doc is not None
    return doc


def set_branch_active(db: Database, code: str, *, is_active: bool) -> dict[str, Any]:
    require_org_master_indexes(db)
    doc = _coll(db, BRANCHES).find_one_and_update(
        {"code": code.strip().upper()},
        {"$set": {"is_active": is_active, "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise LookupError("Cabang tidak ditemukan.")
    return doc


def upsert_machine(
    db: Database,
    *,
    code: str,
    label: str,
    branch_code: str | None,
    is_active: bool = True,
) -> dict[str, Any]:
    require_org_master_indexes(db)
    c = code.strip().upper()
    if not c or not label.strip():
        raise ValueError("Kode dan label mesin wajib.")
    now = datetime.now(timezone.utc)
    branch = (branch_code or "").strip().upper() or None
    if branch:
        if _coll(db, BRANCHES).find_one({"code": branch}) is None:
            raise LookupError(f"Cabang tidak ditemukan: {branch}")
    doc = _coll(db, MACHINES).find_one_and_update(
        {"code": c},
        {
            "$set": {
                "code": c,
                "label": label.strip(),
                "branch_code": branch,
                "is_active": is_active,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    assert doc is not None
    return doc


def set_machine_active(db: Database, code: str, *, is_active: bool) -> dict[str, Any]:
    require_org_master_indexes(db)
    doc = _coll(db, MACHINES).find_one_and_update(
        {"code": code.strip().upper()},
        {"$set": {"is_active": is_active, "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise LookupError("Mesin tidak ditemukan.")
    return doc


def upsert_shift(
    db: Database,
    *,
    code: str,
    label: str,
    start_time: str,
    end_time: str,
    is_active: bool = True,
) -> dict[str, Any]:
    """Jam shift sebagai string HH:MM — validasi format dasar saja."""
    require_org_master_indexes(db)
    c = code.strip().upper()
    if not c or not label.strip():
        raise ValueError("Kode dan label shift wajib.")
    for t in (start_time, end_time):
        parts = t.strip().split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError("Jam shift harus format HH:MM.")
        hh, mm = int(parts[0]), int(parts[1])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("Jam shift di luar rentang.")
    now = datetime.now(timezone.utc)
    try:
        doc = _coll(db, SHIFTS).find_one_and_update(
            {"code": c},
            {
                "$set": {
                    "code": c,
                    "label": label.strip(),
                    "start_time": start_time.strip(),
                    "end_time": end_time.strip(),
                    "is_active": is_active,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise RuntimeError("Kode shift bentrok.") from exc
    assert doc is not None
    return doc


def set_shift_active(db: Database, code: str, *, is_active: bool) -> dict[str, Any]:
    require_org_master_indexes(db)
    doc = _coll(db, SHIFTS).find_one_and_update(
        {"code": code.strip().upper()},
        {"$set": {"is_active": is_active, "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise LookupError("Shift tidak ditemukan.")
    return doc


def count_active_users_on_branch(db: Database, branch_code: str) -> int:
    from app.db import users_collection

    return users_collection(db).count_documents(
        {"is_active": True, "branch_ids": branch_code.strip().upper()}
    )
