"""Persistensi PO Accurate — upsert idempotent by accurate_id."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

from app.domain.po import PoHeaderModel


COLLECTION_NAME = "accurate_sales_orders"

PO_INDEXES: list[dict[str, Any]] = [
    {
        "keys": [("accurate_id", ASCENDING)],
        "name": "uniq_accurate_id",
        "unique": True,
    },
    {
        "keys": [("po_no", ASCENDING)],
        "name": "idx_po_no",
    },
    {
        "keys": [("origin", ASCENDING), ("updated_at", ASCENDING)],
        "name": "idx_origin_updated",
    },
]

REQUIRED_PO_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": COLLECTION_NAME,
        "keys": spec["keys"],
        "name": spec["name"],
        "unique": bool(spec.get("unique")),
    }
    for spec in PO_INDEXES
]


def po_collection(db: Database) -> Collection:
    return db[COLLECTION_NAME]


def ensure_po_indexes(db: Database) -> list[str]:
    """Buat indeks PO — HANYA bootstrap/maintenance/test."""
    created: list[str] = []
    coll = po_collection(db)
    for spec in PO_INDEXES:
        name = coll.create_index(
            spec["keys"],
            name=spec["name"],
            unique=spec.get("unique", False),
        )
        created.append(name)
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified([s["name"] for s in REQUIRED_PO_INDEX_SPECS], db=db)
    return created


def require_po_indexes(db: Database) -> None:
    """Fail-closed request/sync path — tidak createIndex."""
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_PO_INDEX_SPECS)


def inspect_po_indexes(db: Database) -> dict[str, Any]:
    from app.services.index_preflight import inspect_index_specs

    return inspect_index_specs(db, REQUIRED_PO_INDEX_SPECS)


def _to_document(po: PoHeaderModel) -> dict[str, Any]:
    if not po.accurate_id:
        raise ValueError("Persistensi Accurate memerlukan accurate_id.")
    now = datetime.now(timezone.utc)
    payload = po.model_dump()
    return {
        "accurate_id": str(po.accurate_id),
        "source_po_id": po.source_po_id,
        "po_no": po.po_no,
        "origin": po.origin,
        # header = snapshot sumber Accurate (bukan field operasional V4).
        "header": payload,
        "line_ids": [str(i.accurate_line_id) for i in po.items if i.accurate_line_id],
        "updated_at": now,
    }


def upsert_sales_order(
    db: Database,
    po: PoHeaderModel,
    *,
    line_diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Simpan/perbarui sumber Accurate. Idempotent by accurate_id.

    Field ``operational`` (riwayat/manual V4) tidak ditimpa sync.
    """
    require_po_indexes(db)
    doc = _to_document(po)
    coll = po_collection(db)
    set_fields: dict[str, Any] = {
        "source_po_id": doc["source_po_id"],
        "po_no": doc["po_no"],
        "origin": doc["origin"],
        "header": doc["header"],
        "line_ids": doc["line_ids"],
        "updated_at": doc["updated_at"],
        "source_synced_at": doc["updated_at"],
    }
    if line_diff is not None:
        # Hanya metadata line id — tanpa data pelanggan.
        set_fields["last_line_diff"] = {
            "added_line_ids": list(line_diff.get("added_line_ids") or []),
            "removed_line_ids": list(line_diff.get("removed_line_ids") or []),
            "changed_line_ids": list(line_diff.get("changed_line_ids") or []),
            "previous_line_count": line_diff.get("previous_line_count"),
            "current_line_count": line_diff.get("current_line_count"),
        }
    result = coll.find_one_and_update(
        {"accurate_id": doc["accurate_id"]},
        {
            "$set": set_fields,
            "$setOnInsert": {
                "created_at": doc["updated_at"],
                "accurate_id": doc["accurate_id"],
                # Slot operasional internal V4 — jangan dihapus saat sumber Accurate berubah.
                "operational": {},
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    assert result is not None
    return result


def set_branch_code_if_empty(
    db: Database, accurate_id: str | int, branch_code: str
) -> dict[str, Any] | None:
    """Legacy helper — isi ``branch_code`` hanya jika kosong (CAS)."""
    require_po_indexes(db)
    code = str(branch_code or "").strip().upper()
    if not code:
        return None
    return po_collection(db).find_one_and_update(
        {
            "accurate_id": str(accurate_id),
            "$or": [
                {"branch_code": {"$exists": False}},
                {"branch_code": None},
                {"branch_code": ""},
            ],
        },
        {"$set": {"branch_code": code, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )


def set_branch_id_if_empty(
    db: Database, accurate_id: str | int, branch_id: str
) -> dict[str, Any] | None:
    """Isi ``branch_id`` kanonis (Accurate) hanya jika belum ada. Tidak menimpa historis/manual."""
    require_po_indexes(db)
    from app.services.branch_identity import normalize_branch_identity

    nid = normalize_branch_identity(branch_id)
    if not nid:
        return None
    return po_collection(db).find_one_and_update(
        {
            "accurate_id": str(accurate_id),
            "$or": [
                {"branch_id": {"$exists": False}},
                {"branch_id": None},
                {"branch_id": ""},
            ],
        },
        {
            "$set": {
                "branch_id": nid,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


def set_po_branch_id(
    db: Database,
    accurate_id: str | int,
    *,
    branch_id: str,
    expected_previous: str | None,
) -> dict[str, Any] | None:
    """Compare-and-set identitas cabang PO (edit berizin)."""
    require_po_indexes(db)
    from app.services.branch_identity import normalize_branch_identity

    nid = normalize_branch_identity(branch_id)
    if not nid:
        raise ValueError("branch_id wajib.")
    filt: dict[str, Any] = {"accurate_id": str(accurate_id)}
    if expected_previous:
        filt["branch_id"] = normalize_branch_identity(expected_previous)
    else:
        filt["$or"] = [
            {"branch_id": {"$exists": False}},
            {"branch_id": None},
            {"branch_id": ""},
        ]
    return po_collection(db).find_one_and_update(
        filt,
        {"$set": {"branch_id": nid, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )


def get_by_accurate_id(db: Database, accurate_id: str | int) -> dict[str, Any] | None:
    return po_collection(db).find_one({"accurate_id": str(accurate_id)})


def get_by_po_no(db: Database, po_no: str) -> dict[str, Any] | None:
    return po_collection(db).find_one({"po_no": str(po_no).strip(), "origin": "accurate"})


def count_by_accurate_id(db: Database, accurate_id: str | int) -> int:
    return po_collection(db).count_documents({"accurate_id": str(accurate_id)})


def branch_scope_query(branch_ids: list[str] | set[str] | None) -> dict[str, Any]:
    """Filter Mongo untuk identitas cabang kanonis + legacy.

    Fail-closed: daftar kosong → tidak match dokumen apa pun.
    Tanpa membuat index baru.
    """
    from app.services.branch_identity import normalize_branch_identity

    ids = [
        nid
        for nid in (normalize_branch_identity(x) for x in (branch_ids or []))
        if nid
    ]
    if not ids:
        return {"_id": {"$exists": False}}
    return {
        "$or": [
            {"branch_id": {"$in": ids}},
            {"branch_code": {"$in": ids}},
            {"header.raw_sync_meta.accurate_branch_id": {"$in": ids}},
        ]
    }


def list_sales_orders(
    db: Database,
    *,
    origin: str | None = "accurate",
    limit: int = 100,
    skip: int = 0,
    q: str | None = None,
    urgent: bool | None = None,
    sort_by: str = "updated_at",
    sort_dir: int = -1,
    branch_ids: list[str] | set[str] | None = None,
    branch_scope: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Baca dari Mongo saja (bukan Accurate live). Return (rows, total).

    ``branch_scope=True`` menerapkan filter cabang sebelum pagination.
    """
    query: dict[str, Any] = {}
    if origin:
        query["origin"] = origin
    text = (q or "").strip()
    if text:
        query["$or"] = [
            {"po_no": {"$regex": text, "$options": "i"}},
            {"header.customer": {"$regex": text, "$options": "i"}},
            {"accurate_id": {"$regex": text, "$options": "i"}},
            {"header.items.item_name": {"$regex": text, "$options": "i"}},
            {"header.items.item_code": {"$regex": text, "$options": "i"}},
        ]
    if urgent is True:
        query["header.urgent"] = True
    elif urgent is False:
        query["header.urgent"] = {"$ne": True}
    if branch_scope:
        scope = branch_scope_query(branch_ids)
        # Gabungkan dengan filter teks ($and) agar tidak menimpa $or pencarian.
        if "$or" in query:
            query = {"$and": [query, scope]}
        else:
            query.update(scope)

    allowed_sort = {
        "updated_at": "updated_at",
        "po_no": "po_no",
        "po_date": "header.po_date",
        "customer": "header.customer",
        "item_count": "header.raw_sync_meta.line_count",
    }
    sort_field = allowed_sort.get(sort_by, "updated_at")
    direction = -1 if int(sort_dir) < 0 else 1

    coll = po_collection(db)
    total = coll.count_documents(query)
    cursor = (
        coll.find(query)
        .sort(sort_field, direction)
        .skip(max(0, skip))
        .limit(max(1, min(limit, 500)))
    )
    return list(cursor), int(total)


def aggregate_inbox_metrics(
    db: Database,
    *,
    origin: str = "accurate",
    branch_ids: list[str] | set[str] | None = None,
    branch_scope: bool = False,
) -> dict[str, int]:
    """KPI Inbox Accurate via agregasi Mongo — tanpa fetch seluruh dokumen.

    Tidak membuat indeks; memakai data nyata di ``header.items`` /
    ``header.urgent``. Field operasional/produksi tidak dikarang.
    """
    match: dict[str, Any] = {"origin": origin}
    if branch_scope:
        match = {"$and": [match, branch_scope_query(branch_ids)]}
    pipeline: list[dict[str, Any]] = [
        {"$match": match},
        {
            "$project": {
                "urgent_flag": {
                    "$cond": [{"$eq": ["$header.urgent", True]}, 1, 0]
                },
                "line_count": {
                    "$size": {"$ifNull": ["$header.items", []]},
                },
                "qty_sum": {
                    "$reduce": {
                        "input": {"$ifNull": ["$header.items", []]},
                        "initialValue": 0,
                        "in": {
                            "$add": [
                                "$$value",
                                {
                                    "$convert": {
                                        "input": "$$this.qty",
                                        "to": "double",
                                        "onError": 0,
                                        "onNull": 0,
                                    }
                                },
                            ]
                        },
                    }
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "qty": {"$sum": "$qty_sum"},
                "lines": {"$sum": "$line_count"},
                "urgent": {"$sum": "$urgent_flag"},
            }
        },
    ]
    rows = list(po_collection(db).aggregate(pipeline))
    if not rows:
        return {"total": 0, "qty": 0, "lines": 0, "urgent": 0, "baru": 0}
    row = rows[0]
    total = int(row.get("total") or 0)
    return {
        "total": total,
        # Inbox Accurate: seluruh PO di inbox = status awal (bukan klaim jadwal).
        "baru": total,
        "qty": int(row.get("qty") or 0),
        "lines": int(row.get("lines") or 0),
        "urgent": int(row.get("urgent") or 0),
        "printed": 0,
    }
