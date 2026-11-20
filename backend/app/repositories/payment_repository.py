"""Penerimaan pembayaran Accurate — snapshot sumber + rekonsiliasi PO/faktur.

Hanya kasus normal yang membuka Gate Payment. Kasus yang belum diputuskan
Owner (refund, pembatalan, multi-faktur ambigu, overpayment) ditandai REVIEW
dengan alasan yang dapat diaudit; gate tidak dibuka otomatis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.collection import Collection
from pymongo.database import Database

RECEIPTS_COLLECTION = "accurate_payment_receipts"

REQUIRED_PAYMENT_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": RECEIPTS_COLLECTION,
        "keys": [("accurate_receipt_id", ASCENDING)],
        "name": "uniq_accurate_receipt_id",
        "unique": True,
    },
    {
        "collection": RECEIPTS_COLLECTION,
        "keys": [("accurate_po_id", ASCENDING)],
        "name": "idx_receipt_po",
        "unique": False,
    },
]


def receipts_collection(db: Database) -> Collection:
    return db[RECEIPTS_COLLECTION]


def ensure_payment_indexes(db: Database) -> list[str]:
    """Buat indeks payment — HANYA bootstrap/maintenance/test."""
    created: list[str] = []
    for spec in REQUIRED_PAYMENT_INDEX_SPECS:
        created.append(
            db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                unique=bool(spec.get("unique")),
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        [s["name"] for s in REQUIRED_PAYMENT_INDEX_SPECS], db=db
    )
    return created


def require_payment_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_PAYMENT_INDEX_SPECS)


def inspect_payment_indexes(db: Database) -> dict[str, Any]:
    from app.services.index_preflight import inspect_index_specs

    return inspect_index_specs(db, REQUIRED_PAYMENT_INDEX_SPECS)


def _ensure_once(db: Database) -> None:
    """Alias request-path: require (bukan lazy createIndex)."""
    require_payment_indexes(db)


def _as_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _optional_amount(value: Any) -> float | None:
    """Simpan None bila caller menyerahkan None — jangan mengarang 0."""
    if value is None:
        return None
    return _as_float(value)


def upsert_receipt(
    db: Database,
    *,
    accurate_receipt_id: str,
    accurate_po_id: str,
    po_no: str,
    invoice_no: str | None,
    amount: Any,
    receipt_date: str | None,
    raw_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Idempoten by ``accurate_receipt_id`` — sync ulang tidak menggandakan."""
    _ensure_once(db)
    if not accurate_receipt_id.strip():
        raise ValueError("accurate_receipt_id wajib diisi.")
    now = datetime.now(timezone.utc)
    doc = {
        "accurate_receipt_id": str(accurate_receipt_id).strip(),
        "accurate_po_id": str(accurate_po_id).strip(),
        "po_no": po_no,
        "invoice_no": invoice_no,
        "amount": _optional_amount(amount),
        "receipt_date": receipt_date,
        "raw_ref": dict(raw_ref or {}),
        "synced_at": now,
    }
    receipts_collection(db).update_one(
        {"accurate_receipt_id": doc["accurate_receipt_id"]},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return receipts_collection(db).find_one(
        {"accurate_receipt_id": doc["accurate_receipt_id"]}, {"_id": 0}
    )


def list_receipts(db: Database, accurate_po_id: str) -> list[dict[str, Any]]:
    _ensure_once(db)
    return list(
        receipts_collection(db)
        .find({"accurate_po_id": str(accurate_po_id)}, {"_id": 0})
        .sort("synced_at", ASCENDING)
    )


def reconcile_po_payment(db: Database, accurate_po_id: str) -> dict[str, Any]:
    """Keputusan gate pembayaran untuk satu PO.

    Terbuka hanya pada kasus normal yang jelas: ada penerimaan positif yang
    cocok ke PO dan memiliki nomor faktur. Tanpa ambang minimal 50%.
    """
    receipts = list_receipts(db, accurate_po_id)
    if not receipts:
        return {
            "decision": "closed",
            "reason": "Belum ada penerimaan pembayaran Accurate untuk PO ini.",
            "total_amount": 0.0,
            "receipt_ids": [],
        }

    invalid = [r for r in receipts if r.get("amount") is None]
    negative = [r for r in receipts if isinstance(r.get("amount"), (int, float)) and r["amount"] < 0]
    zero = [r for r in receipts if r.get("amount") == 0]
    positive = [r for r in receipts if isinstance(r.get("amount"), (int, float)) and r["amount"] > 0]
    unmatched_invoice = [r for r in positive if not (r.get("invoice_no") or "").strip()]
    invoices = {
        (r.get("invoice_no") or "").strip() for r in positive if (r.get("invoice_no") or "").strip()
    }
    flagged_review = [
        r
        for r in receipts
        if isinstance(r.get("raw_ref"), dict) and r["raw_ref"].get("review_required")
    ]

    if invalid:
        return {
            "decision": "review",
            "reason": "Terdapat penerimaan dengan nominal tidak valid/malformed.",
            "total_amount": sum(
                float(r["amount"]) for r in receipts if isinstance(r.get("amount"), (int, float))
            ),
            "receipt_ids": [r["accurate_receipt_id"] for r in invalid],
        }
    if flagged_review:
        return {
            "decision": "review",
            "reason": str(
                (flagged_review[0].get("raw_ref") or {}).get("review_reason")
                or "Penerimaan ditandai REVIEW oleh sync Accurate."
            ),
            "total_amount": sum(
                float(r["amount"]) for r in receipts if isinstance(r.get("amount"), (int, float))
            ),
            "receipt_ids": [r["accurate_receipt_id"] for r in flagged_review],
        }
    if negative:
        return {
            "decision": "review",
            "reason": (
                "Terdapat penerimaan bernilai negatif (refund/pembatalan). "
                "Aturan refund/pembatalan belum diputuskan Owner."
            ),
            "total_amount": sum(
                float(r["amount"]) for r in receipts if isinstance(r.get("amount"), (int, float))
            ),
            "receipt_ids": [r["accurate_receipt_id"] for r in negative],
        }
    if unmatched_invoice:
        return {
            "decision": "review",
            "reason": "Penerimaan tanpa nomor faktur terkait — relasi PO/faktur belum terverifikasi.",
            "total_amount": sum(r.get("amount", 0) for r in positive),
            "receipt_ids": [r["accurate_receipt_id"] for r in unmatched_invoice],
        }
    if len(invoices) > 1:
        return {
            "decision": "review",
            "reason": (
                "Penerimaan terkait lebih dari satu faktur. "
                "Aturan multi-faktur belum diputuskan Owner."
            ),
            "total_amount": sum(r.get("amount", 0) for r in positive),
            "receipt_ids": [r["accurate_receipt_id"] for r in positive],
        }
    if not positive:
        return {
            "decision": "closed",
            "reason": "Penerimaan bernilai nol — belum ada pembayaran positif.",
            "total_amount": 0.0,
            "receipt_ids": [r["accurate_receipt_id"] for r in zero],
        }

    return {
        "decision": "open",
        "reason": (
            "Penerimaan positif Accurate cocok dengan PO dan faktur "
            f"{sorted(invoices)[0]} (tanpa ambang minimal)."
        ),
        "total_amount": sum(r.get("amount", 0) for r in positive),
        "receipt_ids": [r["accurate_receipt_id"] for r in positive],
    }
