"""Master Produk V4 — atribut item (ukuran screen) per item_code.

Tidak seed ukuran. Tidak createIndex di request path.
Nilai screen_size bebas teks dari Owner; kosong = Belum diatur.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

PRODUCTS_COLLECTION = "master_products"
SCREEN_UNSET = "Belum diatur"

REQUIRED_PRODUCT_INDEX_SPECS = [
    {
        "collection": PRODUCTS_COLLECTION,
        "keys": [("item_code", ASCENDING)],
        "name": "uniq_master_product_item_code",
        "unique": True,
    },
]


def products_collection(db: Database) -> Collection:
    return db[PRODUCTS_COLLECTION]


def ensure_product_master_indexes(db: Database) -> list[str]:
    """Hanya bootstrap/test — bukan request path."""
    created: list[str] = []
    for spec in REQUIRED_PRODUCT_INDEX_SPECS:
        coll = db[spec["collection"]]
        created.append(
            coll.create_index(spec["keys"], name=spec["name"], unique=bool(spec.get("unique")))
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified([s["name"] for s in REQUIRED_PRODUCT_INDEX_SPECS], db=db)
    return created


def require_product_master_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_PRODUCT_INDEX_SPECS)


def get_product(db: Database, item_code: str) -> dict[str, Any] | None:
    code = str(item_code or "").strip()
    if not code:
        return None
    return products_collection(db).find_one({"item_code": code}, {"_id": 0})


def screen_size_for_item(db: Database, item_code: str) -> str:
    """Nilai tampilan Jadwal: screen_size Master atau Belum diatur."""
    doc = get_product(db, item_code)
    if doc is None:
        return SCREEN_UNSET
    value = str(doc.get("screen_size") or "").strip()
    return value if value else SCREEN_UNSET


def upsert_product_screen_size(
    db: Database,
    *,
    item_code: str,
    screen_size: str,
    actor_email: str,
) -> dict[str, Any]:
    """Simpan ukuran screen. String kosong menghapus nilai (Belum diatur)."""
    require_product_master_indexes(db)
    code = str(item_code or "").strip()
    if not code:
        raise ValueError("item_code wajib diisi.")
    size = str(screen_size or "").strip()
    now = datetime.now(timezone.utc)
    doc = products_collection(db).find_one_and_update(
        {"item_code": code},
        {
            "$set": {
                "item_code": code,
                "screen_size": size,
                "updated_at": now,
                "updated_by": actor_email,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    assert doc is not None
    return doc


def list_products(db: Database, *, limit: int = 500) -> list[dict[str, Any]]:
    require_product_master_indexes(db)
    return list(
        products_collection(db)
        .find({}, {"_id": 0})
        .sort("item_code", ASCENDING)
        .limit(max(1, min(limit, 2000)))
    )
