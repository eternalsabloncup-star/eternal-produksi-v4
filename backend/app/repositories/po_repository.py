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


def po_collection(db: Database) -> Collection:
    return db[COLLECTION_NAME]


def ensure_po_indexes(db: Database) -> list[str]:
    created: list[str] = []
    coll = po_collection(db)
    for spec in PO_INDEXES:
        name = coll.create_index(
            spec["keys"],
            name=spec["name"],
            unique=spec.get("unique", False),
        )
        created.append(name)
    return created


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
    ensure_po_indexes(db)
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


def get_by_accurate_id(db: Database, accurate_id: str | int) -> dict[str, Any] | None:
    return po_collection(db).find_one({"accurate_id": str(accurate_id)})


def get_by_po_no(db: Database, po_no: str) -> dict[str, Any] | None:
    return po_collection(db).find_one({"po_no": str(po_no).strip(), "origin": "accurate"})


def count_by_accurate_id(db: Database, accurate_id: str | int) -> int:
    return po_collection(db).count_documents({"accurate_id": str(accurate_id)})


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
) -> tuple[list[dict[str, Any]], int]:
    """Baca dari Mongo saja (bukan Accurate live). Return (rows, total)."""
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
