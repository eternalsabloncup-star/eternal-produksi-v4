"""Master cabang, mesin, shift — Settings V4 operasional dasar."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

BRANCHES = "master_branches"
MACHINES = "master_machines"
SHIFTS = "master_shifts"

# Kode cabang: huruf/angka, boleh _/-, max 16, tidak boleh spasi.
_BRANCH_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,15}$")

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


def normalize_branch_code(code: str) -> str:
    return str(code or "").strip().upper()


def validate_branch_code_format(code: str) -> str:
    c = normalize_branch_code(code)
    if not c:
        raise ValueError("Kode cabang wajib.")
    if not _BRANCH_CODE_RE.match(c):
        raise ValueError(
            "Kode cabang tidak valid — gunakan A-Z/0-9/_/- (max 16), tanpa spasi."
        )
    return c


def get_branch(db: Database, code: str) -> dict[str, Any] | None:
    require_org_master_indexes(db)
    return _coll(db, BRANCHES).find_one(
        {"code": normalize_branch_code(code)}, {"_id": 0}
    )


def validate_branch_ids_for_assignment(
    db: Database, branch_ids: list[str] | None
) -> list[str]:
    """Normalisasi + dedupe; tolak kode tidak dikenal atau nonaktif."""
    require_org_master_indexes(db)
    raw = list(branch_ids or [])
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        code = validate_branch_code_format(str(item))
        if code in seen:
            continue
        doc = get_branch(db, code)
        if doc is None:
            raise ValueError(f"Cabang tidak dikenal: {code}")
        if not bool(doc.get("is_active", False)):
            raise ValueError(f"Cabang tidak aktif: {code}")
        seen.add(code)
        out.append(code)
    return out


def upsert_branch(
    db: Database, *, code: str, name: str, is_active: bool = True
) -> dict[str, Any]:
    require_org_master_indexes(db)
    c = validate_branch_code_format(code)
    n = name.strip()
    if not n:
        raise ValueError("Nama cabang wajib.")
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


def find_branches_by_accurate_id(
    db: Database, accurate_branch_id: str
) -> list[dict[str, Any]]:
    """Cari Master yang memetakan Accurate branchId (app-level uniqueness)."""
    require_org_master_indexes(db)
    aid = str(accurate_branch_id or "").strip()
    if not aid:
        return []
    return list(
        _coll(db, BRANCHES).find({"accurate_branch_id": aid}, {"_id": 0}).sort(
            "code", ASCENDING
        )
    )


def set_branch_accurate_mapping(
    db: Database,
    code: str,
    *,
    accurate_branch_id: str | None,
) -> dict[str, Any]:
    """Set/clear mapping Accurate → Master. Uniqueness dienforce di aplikasi.

    Tidak membuat index baru (NO INDEX CREATE pada pekerjaan ini).
    """
    require_org_master_indexes(db)
    c = validate_branch_code_format(code)
    existing = get_branch(db, c)
    if existing is None:
        raise LookupError("Cabang tidak ditemukan.")

    now = datetime.now(timezone.utc)
    if accurate_branch_id is None or str(accurate_branch_id).strip() == "":
        doc = _coll(db, BRANCHES).find_one_and_update(
            {"code": c},
            {
                "$unset": {"accurate_branch_id": ""},
                "$set": {
                    "accurate_mapping_verified": False,
                    "updated_at": now,
                },
            },
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
        assert doc is not None
        return doc

    aid = str(accurate_branch_id).strip()
    if not aid.isdigit():
        raise ValueError(
            "Accurate branchId wajib numerik valid dari response Accurate."
        )
    if len(aid) > 32:
        raise ValueError("Accurate branchId terlalu panjang.")

    # Satu Accurate ID tidak boleh dipetakan ke dua cabang Eternal aktif berbeda.
    for other in find_branches_by_accurate_id(db, aid):
        other_code = str(other.get("code") or "").upper()
        if other_code == c:
            continue
        if bool(other.get("is_active", False)):
            raise ValueError(
                f"Accurate branchId {aid} sudah dipetakan ke cabang aktif {other_code}."
            )

    doc = _coll(db, BRANCHES).find_one_and_update(
        {"code": c},
        {
            "$set": {
                "accurate_branch_id": aid,
                # Tidak ada endpoint Master Cabang Accurate yang terbukti — Owner verify.
                "accurate_mapping_verified": False,
                "updated_at": now,
            },
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    assert doc is not None
    return doc


def enrich_branches_with_mapping_status(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Hitung status Terhubung / Belum dipetakan / Konflik untuk UI."""
    by_aid: dict[str, list[str]] = {}
    for item in items:
        aid = str(item.get("accurate_branch_id") or "").strip()
        if not aid or not bool(item.get("is_active", False)):
            continue
        by_aid.setdefault(aid, []).append(str(item.get("code") or "").upper())

    out: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        aid = str(row.get("accurate_branch_id") or "").strip() or None
        row["accurate_branch_id"] = aid
        if not aid:
            row["mapping_status"] = "unmapped"
        elif len(by_aid.get(aid, [])) > 1:
            row["mapping_status"] = "conflict"
        else:
            row["mapping_status"] = "mapped"
        row["accurate_verified"] = bool(row.get("accurate_mapping_verified", False))
        out.append(row)
    return out


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
