"""Snapshot Faktur Penjualan Accurate — idempotent by accurate_invoice_id."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.collection import Collection
from pymongo.database import Database

INVOICES_COLLECTION = "accurate_sales_invoices"

REQUIRED_INVOICE_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": INVOICES_COLLECTION,
        "keys": [("accurate_invoice_id", ASCENDING)],
        "name": "uniq_accurate_invoice_id",
        "unique": True,
    },
    {
        "collection": INVOICES_COLLECTION,
        "keys": [("accurate_po_ids", ASCENDING)],
        "name": "idx_invoice_po_ids",
        "unique": False,
    },
]


def invoices_collection(db: Database) -> Collection:
    return db[INVOICES_COLLECTION]


def ensure_invoice_indexes(db: Database) -> list[str]:
    created: list[str] = []
    for spec in REQUIRED_INVOICE_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_INVOICE_INDEX_SPECS], db=db
    )
    return created


def require_invoice_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_INVOICE_INDEX_SPECS)


def upsert_invoice(db: Database, normalized: dict[str, Any]) -> dict[str, Any]:
    require_invoice_indexes(db)
    invoice_id = str(normalized["accurate_invoice_id"])
    now = datetime.now(timezone.utc)
    doc = {
        **normalized,
        "synced_at": now,
        "updated_at": now,
    }
    invoices_collection(db).update_one(
        {"accurate_invoice_id": invoice_id},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return invoices_collection(db).find_one(
        {"accurate_invoice_id": invoice_id}, {"_id": 0}
    )


def get_invoice(db: Database, accurate_invoice_id: str) -> dict[str, Any] | None:
    require_invoice_indexes(db)
    return invoices_collection(db).find_one(
        {"accurate_invoice_id": str(accurate_invoice_id)}, {"_id": 0}
    )


def resolve_po_ids_for_invoice(db: Database, accurate_invoice_id: str) -> list[str]:
    doc = get_invoice(db, accurate_invoice_id)
    if not doc:
        return []
    return [str(x) for x in (doc.get("accurate_po_ids") or []) if str(x).strip()]
