"""Paint inventory persistence — indexes, balances, ledger, documents.

createIndex only via ensure_paint_indexes (bootstrap/test). Request path uses
require_paint_indexes (fail-closed). Qty canonical unit: integer milligrams.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

CATALOG = "paint_catalog"
PURPOSES = "paint_purposes"
BALANCES = "paint_stock_balances"
DOCUMENTS = "paint_documents"
LEDGER = "paint_ledger"
STOCKTAKES = "paint_stocktakes"
PERIOD_LOCKS = "paint_period_locks"
ACCURATE_MAPPINGS = "paint_accurate_mappings"

G_TO_MG = Decimal("1000")
MG_QUANT = Decimal("1")


REQUIRED_PAINT_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": CATALOG,
        "keys": [("paint_id", ASCENDING)],
        "name": "uniq_paint_catalog_id",
        "unique": True,
    },
    {
        "collection": CATALOG,
        "keys": [("brand", ASCENDING), ("kind", ASCENDING), ("color", ASCENDING)],
        "name": "uniq_paint_brand_kind_color",
        "unique": True,
    },
    {
        "collection": PURPOSES,
        "keys": [("purpose_id", ASCENDING)],
        "name": "uniq_paint_purpose_id",
        "unique": True,
    },
    {
        "collection": BALANCES,
        "keys": [("branch_id", ASCENDING), ("paint_id", ASCENDING)],
        "name": "uniq_paint_balance_branch_paint",
        "unique": True,
    },
    {
        "collection": DOCUMENTS,
        "keys": [("document_id", ASCENDING)],
        "name": "uniq_paint_document_id",
        "unique": True,
    },
    {
        "collection": DOCUMENTS,
        "keys": [("idempotency_key", ASCENDING)],
        "name": "uniq_paint_document_idempotency",
        "unique": True,
    },
    {
        "collection": LEDGER,
        "keys": [("ledger_id", ASCENDING)],
        "name": "uniq_paint_ledger_id",
        "unique": True,
    },
    {
        "collection": LEDGER,
        "keys": [("idempotency_key", ASCENDING)],
        "name": "uniq_paint_ledger_idempotency",
        "unique": True,
    },
    {
        "collection": LEDGER,
        "keys": [("branch_id", ASCENDING), ("paint_id", ASCENDING), ("created_at", ASCENDING)],
        "name": "idx_paint_ledger_branch_paint_created",
        "unique": False,
    },
    {
        "collection": STOCKTAKES,
        "keys": [("stocktake_id", ASCENDING)],
        "name": "uniq_paint_stocktake_id",
        "unique": True,
    },
    {
        "collection": PERIOD_LOCKS,
        "keys": [("branch_id", ASCENDING)],
        "name": "uniq_paint_period_lock_branch",
        "unique": True,
    },
    {
        "collection": ACCURATE_MAPPINGS,
        "keys": [("accurate_item_code", ASCENDING)],
        "name": "uniq_paint_accurate_item_code",
        "unique": True,
    },
]


class PaintQuantityError(ValueError):
    """Qty cat tidak valid atau stok tidak cukup."""


class PaintConflictError(RuntimeError):
    """CAS/version conflict or period lock violation."""


def coll(db: Database, name: str) -> Collection:
    return db[name]


def ensure_paint_indexes(db: Database) -> list[str]:
    created: list[str] = []
    for spec in REQUIRED_PAINT_INDEX_SPECS:
        created.append(
            coll(db, spec["collection"]).create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified([s["name"] for s in REQUIRED_PAINT_INDEX_SPECS], db=db)
    return created


def require_paint_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_PAINT_INDEX_SPECS)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def grams_to_mg(grams: float | int | str | Decimal) -> int:
    try:
        g = Decimal(str(grams))
    except (InvalidOperation, ValueError) as exc:
        raise PaintQuantityError("Berat gram tidak valid.") from exc
    if g <= 0:
        raise PaintQuantityError("Berat gram harus lebih besar dari nol.")
    # Max 3 decimal places in grams.
    exp = g.as_tuple().exponent
    if isinstance(exp, int) and exp < -3:
        raise PaintQuantityError("Berat gram maksimal 3 angka di belakang koma.")
    mg = (g * G_TO_MG).quantize(MG_QUANT, rounding=ROUND_HALF_UP)
    value = int(mg)
    if value <= 0:
        raise PaintQuantityError("Berat setelah konversi mg harus positif.")
    return value


def mg_to_grams(mg: int) -> float:
    return float(Decimal(mg) / G_TO_MG)


def public_qty(mg: int) -> dict[str, Any]:
    return {"qty_mg": int(mg), "qty_g": mg_to_grams(int(mg))}


def get_balance(
    db: Database,
    *,
    branch_id: str,
    paint_id: str,
    session: Any | None = None,
) -> dict[str, Any] | None:
    return coll(db, BALANCES).find_one(
        {"branch_id": branch_id, "paint_id": paint_id},
        {"_id": 0},
        session=session,
    )


def ensure_balance_doc(
    db: Database,
    *,
    branch_id: str,
    paint_id: str,
    session: Any | None = None,
) -> dict[str, Any]:
    existing = get_balance(db, branch_id=branch_id, paint_id=paint_id, session=session)
    if existing is not None:
        return existing
    doc = {
        "branch_id": branch_id,
        "paint_id": paint_id,
        "qty_mg": 0,
        "version": 0,
        "updated_at": now_utc(),
    }
    try:
        coll(db, BALANCES).insert_one(dict(doc), session=session)
    except DuplicateKeyError:
        found = get_balance(db, branch_id=branch_id, paint_id=paint_id, session=session)
        assert found is not None
        return found
    return doc


def apply_balance_delta(
    db: Database,
    *,
    branch_id: str,
    paint_id: str,
    delta_mg: int,
    expected_version: int | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Atomic balance mutation. Debit refuses negative resulting stock."""
    ensure_balance_doc(db, branch_id=branch_id, paint_id=paint_id, session=session)
    filt: dict[str, Any] = {"branch_id": branch_id, "paint_id": paint_id}
    if expected_version is not None:
        filt["version"] = int(expected_version)
    if delta_mg < 0:
        filt["qty_mg"] = {"$gte": -delta_mg}
    updated = coll(db, BALANCES).find_one_and_update(
        filt,
        {
            "$inc": {"qty_mg": int(delta_mg), "version": 1},
            "$set": {"updated_at": now_utc()},
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if updated is None:
        current = get_balance(db, branch_id=branch_id, paint_id=paint_id, session=session)
        if expected_version is not None and current and current.get("version") != expected_version:
            raise PaintConflictError("Versi saldo berubah — ulangi operasi.")
        raise PaintQuantityError("Stok cat tidak mencukupi untuk operasi ini.")
    return updated


def insert_ledger(
    db: Database,
    *,
    branch_id: str,
    paint_id: str,
    delta_mg: int,
    entry_type: str,
    document_id: str,
    idempotency_key: str,
    actor_email: str,
    detail: dict[str, Any] | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    doc = {
        "ledger_id": new_id("pl"),
        "branch_id": branch_id,
        "paint_id": paint_id,
        "delta_mg": int(delta_mg),
        "entry_type": entry_type,
        "document_id": document_id,
        "idempotency_key": idempotency_key.strip(),
        "actor_email": actor_email,
        "detail": dict(detail or {}),
        "created_at": now_utc(),
    }
    try:
        coll(db, LEDGER).insert_one(dict(doc), session=session)
    except DuplicateKeyError:
        existing = coll(db, LEDGER).find_one(
            {"idempotency_key": idempotency_key.strip()},
            {"_id": 0},
            session=session,
        )
        assert existing is not None
        return {"ledger": existing, "duplicate": True}
    return {"ledger": doc, "duplicate": False}


def get_document(
    db: Database, document_id: str, *, session: Any | None = None
) -> dict[str, Any] | None:
    return coll(db, DOCUMENTS).find_one(
        {"document_id": document_id}, {"_id": 0}, session=session
    )


def find_document_by_idempotency(
    db: Database, idempotency_key: str, *, session: Any | None = None
) -> dict[str, Any] | None:
    return coll(db, DOCUMENTS).find_one(
        {"idempotency_key": idempotency_key.strip()},
        {"_id": 0},
        session=session,
    )


def insert_document(
    db: Database, doc: dict[str, Any], *, session: Any | None = None
) -> dict[str, Any]:
    try:
        coll(db, DOCUMENTS).insert_one(dict(doc), session=session)
    except DuplicateKeyError:
        existing = find_document_by_idempotency(
            db, str(doc.get("idempotency_key") or ""), session=session
        )
        if existing is None:
            existing = get_document(db, str(doc.get("document_id") or ""), session=session)
        assert existing is not None
        return {"document": existing, "duplicate": True}
    return {"document": doc, "duplicate": False}


def period_lock_for_branch(
    db: Database, branch_id: str, *, session: Any | None = None
) -> dict[str, Any] | None:
    return coll(db, PERIOD_LOCKS).find_one(
        {"branch_id": branch_id}, {"_id": 0}, session=session
    )


def assert_period_open(
    db: Database,
    *,
    branch_id: str,
    effective_at: datetime,
    session: Any | None = None,
) -> None:
    lock = period_lock_for_branch(db, branch_id, session=session)
    if not lock:
        return
    end = lock.get("period_end")
    if end is None:
        return
    if effective_at.tzinfo is None:
        effective_at = effective_at.replace(tzinfo=timezone.utc)
    if getattr(end, "tzinfo", None) is None and isinstance(end, datetime):
        end = end.replace(tzinfo=timezone.utc)
    if effective_at <= end:
        raise PaintConflictError(
            "Periode stok cat sudah dikunci oleh stock opname — "
            "transaksi backdated ditolak."
        )
