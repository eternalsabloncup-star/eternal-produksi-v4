"""Durable SJ sync quarantine / deferred review (bukan ingest Surat Jalan).

Kontrak:
- MULTI_SO_NOT_SUPPORTED → review terbuka, tidak menulis ``accurate_delivery_notes``.
- po_not_registered → deferred retryable setelah PO terdaftar.
- Upsert idempotent per ``accurate_sj_id`` (``_id`` stabil).
- Tanpa createIndex di request path — identitas unik lewat ``_id``.
- Payload mentah / PII pelanggan tidak disimpan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from app.integrations.accurate.fields import as_text

COLLECTION = "accurate_sj_sync_reviews"

REASON_MULTI_SO = "MULTI_SO_NOT_SUPPORTED"
REASON_PO_NOT_REGISTERED = "po_not_registered"

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"


def review_doc_id(accurate_sj_id: str) -> str:
    return f"sj:{as_text(accurate_sj_id)}"


def collect_line_sales_order_ids(payload: dict[str, Any]) -> list[str]:
    """Kumpulkan SO id dari baris DO — tanpa menebak / memilih PO tunggal."""
    found: set[str] = set()
    raw_items = payload.get("detailItem") or payload.get("items") or []
    if not isinstance(raw_items, list):
        return []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        so = raw.get("salesOrder")
        so_id_from_obj = as_text(so.get("id")) if isinstance(so, dict) else ""
        so_id_flat = as_text(raw.get("salesOrderId"))
        so_id = so_id_from_obj or so_id_flat
        if so_id:
            found.add(so_id)
    return sorted(found)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def upsert_sj_sync_review(
    db: Database,
    *,
    accurate_sj_id: str,
    reason: str,
    source_so_ids: list[str] | None = None,
    accurate_po_id: str | None = None,
    sj_no: str | None = None,
    retryable: bool = False,
    last_error_code: str | None = None,
) -> dict[str, Any]:
    """Idempotent upsert review. Tidak menulis delivery note."""
    sj_id = as_text(accurate_sj_id)
    if not sj_id:
        raise ValueError("accurate_sj_id wajib untuk SJ sync review")
    now = _now()
    doc_id = review_doc_id(sj_id)
    so_ids = [as_text(x) for x in (source_so_ids or []) if as_text(x)]
    po_id = as_text(accurate_po_id) or None
    number = as_text(sj_no) or None
    code = as_text(last_error_code) or reason

    existing = db[COLLECTION].find_one({"_id": doc_id})
    if existing is None:
        doc = {
            "_id": doc_id,
            "kind": "sj_sync_review",
            "accurate_sj_id": sj_id,
            "sj_no": number,
            "reason": reason,
            "source_so_ids": so_ids,
            "accurate_po_id": po_id,
            "retryable": bool(retryable),
            "status": STATUS_OPEN,
            "disposition": None,
            "first_seen_at": now,
            "updated_at": now,
            "last_error_code": code,
            "seen_count": 1,
        }
        db[COLLECTION].insert_one(doc)
        return doc

    # Jangan turunkan status resolved → open kecuali reason berubah material.
    updates: dict[str, Any] = {
        "updated_at": now,
        "last_error_code": code,
        "reason": reason,
        "retryable": bool(retryable),
        "seen_count": int(existing.get("seen_count") or 0) + 1,
    }
    if so_ids:
        updates["source_so_ids"] = so_ids
    if po_id:
        updates["accurate_po_id"] = po_id
    if number:
        updates["sj_no"] = number
    if existing.get("status") != STATUS_RESOLVED or existing.get("reason") != reason:
        updates["status"] = STATUS_OPEN
        updates["disposition"] = None

    db[COLLECTION].update_one({"_id": doc_id}, {"$set": updates})
    merged = dict(existing)
    merged.update(updates)
    return merged


def mark_sj_sync_review_resolved(
    db: Database,
    *,
    accurate_sj_id: str,
    disposition: str,
) -> None:
    sj_id = as_text(accurate_sj_id)
    if not sj_id:
        return
    db[COLLECTION].update_one(
        {"_id": review_doc_id(sj_id)},
        {
            "$set": {
                "status": STATUS_RESOLVED,
                "disposition": disposition,
                "updated_at": _now(),
                "retryable": False,
            }
        },
    )


def list_open_retryable_reviews(
    db: Database, *, limit: int = 50
) -> list[dict[str, Any]]:
    cur = (
        db[COLLECTION]
        .find(
            {
                "status": STATUS_OPEN,
                "retryable": True,
            }
        )
        .sort("updated_at", 1)
        .limit(max(1, min(int(limit), 200)))
    )
    return list(cur)


def get_sj_sync_review(db: Database, accurate_sj_id: str) -> dict[str, Any] | None:
    return db[COLLECTION].find_one({"_id": review_doc_id(as_text(accurate_sj_id))})
