"""Surat Jalan Accurate + event Loading dan Pengiriman.

SJ dibuat di Accurate lebih dulu; V4 hanya menyimpan snapshot sumber dan
mengelola workflow operasionalnya. Relasi item SJ ↔ item line PO memakai
``accurate_line_id``; tanpa itu sinkronisasi fail-closed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

DELIVERY_NOTES_COLLECTION = "accurate_delivery_notes"
LOADING_EVENTS_COLLECTION = "loading_events"
DELIVERY_EVENTS_COLLECTION = "delivery_events"

DELIVERY_PENDING = "menunggu_pengiriman"
DELIVERY_SUCCESS = "berhasil"
DELIVERY_FAILED = "gagal"

_INDEXED_DATABASES: set[str] = set()


class DeliveryNoteIdentityError(ValueError):
    """Identitas SJ/PO line tidak lengkap — tidak boleh ditebak dari nama barang."""


def delivery_notes_collection(db: Database) -> Collection:
    return db[DELIVERY_NOTES_COLLECTION]


def loading_events_collection(db: Database) -> Collection:
    return db[LOADING_EVENTS_COLLECTION]


def delivery_events_collection(db: Database) -> Collection:
    return db[DELIVERY_EVENTS_COLLECTION]


def ensure_delivery_indexes(db: Database) -> list[str]:
    created = [
        delivery_notes_collection(db).create_index(
            [("accurate_sj_id", ASCENDING)], name="uniq_accurate_sj_id", unique=True
        ),
        delivery_notes_collection(db).create_index(
            [("accurate_po_id", ASCENDING)], name="idx_sj_po"
        ),
        delivery_notes_collection(db).create_index(
            [("ship_date", ASCENDING)], name="idx_sj_ship_date"
        ),
        loading_events_collection(db).create_index(
            [("accurate_sj_id", ASCENDING)], name="uniq_loading_per_sj", unique=True
        ),
        delivery_events_collection(db).create_index(
            [("accurate_sj_id", ASCENDING), ("attempt", ASCENDING)],
            name="uniq_delivery_attempt",
            unique=True,
        ),
    ]
    _INDEXED_DATABASES.add(db.name)
    return created


def _ensure_once(db: Database) -> None:
    if db.name not in _INDEXED_DATABASES:
        ensure_delivery_indexes(db)


def _as_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def normalize_delivery_note(payload: dict[str, Any]) -> dict[str, Any]:
    """Snapshot SJ Accurate → model internal. Fail-closed pada identitas."""
    accurate_sj_id = str(payload.get("id") or payload.get("accurate_sj_id") or "").strip()
    if not accurate_sj_id:
        raise DeliveryNoteIdentityError("Surat Jalan wajib memiliki Accurate SJ ID.")
    sj_no = str(payload.get("number") or payload.get("sj_no") or "").strip()
    if not sj_no:
        raise DeliveryNoteIdentityError("Surat Jalan wajib memiliki nomor SJ asli.")
    accurate_po_id = str(
        payload.get("salesOrderId") or payload.get("accurate_po_id") or ""
    ).strip()
    if not accurate_po_id:
        raise DeliveryNoteIdentityError("Surat Jalan wajib merujuk Accurate PO ID.")

    raw_items = payload.get("detailItem") or payload.get("items") or []
    if not isinstance(raw_items, list) or not raw_items:
        raise DeliveryNoteIdentityError("Surat Jalan wajib memiliki item.")

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        sj_line_id = str(raw.get("id") or raw.get("sj_line_id") or "").strip()
        if not sj_line_id:
            raise DeliveryNoteIdentityError("Setiap item SJ wajib memiliki ID baris.")
        if sj_line_id in seen:
            raise DeliveryNoteIdentityError(f"ID baris SJ duplikat: {sj_line_id}")
        seen.add(sj_line_id)
        po_line_id = str(
            raw.get("salesOrderDetailId") or raw.get("accurate_line_id") or ""
        ).strip()
        if not po_line_id:
            raise DeliveryNoteIdentityError(
                f"Item SJ {sj_line_id} tidak memiliki referensi item line PO "
                "(accurate_line_id) — relasi tidak boleh ditebak dari nama barang."
            )
        items.append(
            {
                "sj_line_id": sj_line_id,
                "accurate_line_id": po_line_id,
                "item_code": str(raw.get("itemNo") or raw.get("item_code") or ""),
                "item_name": str(raw.get("detailName") or raw.get("item_name") or ""),
                "qty": _as_float(raw.get("quantity", raw.get("qty"))),
                "unit": str(raw.get("itemUnitName") or raw.get("unit") or ""),
            }
        )

    return {
        "accurate_sj_id": accurate_sj_id,
        "sj_no": sj_no,
        "accurate_po_id": accurate_po_id,
        "po_no": str(payload.get("salesOrderNumber") or payload.get("po_no") or ""),
        "ship_date": str(payload.get("transDate") or payload.get("ship_date") or ""),
        "destination": str(payload.get("toAddress") or payload.get("destination") or ""),
        "shipment": str(payload.get("shipmentName") or payload.get("shipment") or ""),
        "items": items,
        "superseded_by": str(payload.get("superseded_by") or "") or None,
    }


def upsert_delivery_note(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    """Idempoten per ``accurate_sj_id``; perubahan sumber tercatat."""
    _ensure_once(db)
    doc = normalize_delivery_note(payload)
    now = datetime.now(timezone.utc)
    before = delivery_notes_collection(db).find_one(
        {"accurate_sj_id": doc["accurate_sj_id"]}, {"_id": 0}
    )
    delivery_notes_collection(db).update_one(
        {"accurate_sj_id": doc["accurate_sj_id"]},
        {
            "$set": {**doc, "synced_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    after = delivery_notes_collection(db).find_one(
        {"accurate_sj_id": doc["accurate_sj_id"]}, {"_id": 0}
    )
    changed = before is not None and (
        before.get("items") != after.get("items")
        or before.get("ship_date") != after.get("ship_date")
    )
    return {"delivery_note": after, "was_insert": before is None, "source_changed": changed}


def get_delivery_note(db: Database, accurate_sj_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return delivery_notes_collection(db).find_one(
        {"accurate_sj_id": str(accurate_sj_id)}, {"_id": 0}
    )


def list_delivery_notes(
    db: Database,
    *,
    accurate_po_id: str | None = None,
    ship_date_from: str | None = None,
    ship_date_to: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_once(db)
    query: dict[str, Any] = {}
    if accurate_po_id:
        query["accurate_po_id"] = str(accurate_po_id)
    if ship_date_from or ship_date_to:
        window: dict[str, Any] = {}
        if ship_date_from:
            window["$gte"] = ship_date_from
        if ship_date_to:
            window["$lte"] = ship_date_to
        query["ship_date"] = window
    return list(
        delivery_notes_collection(db).find(query, {"_id": 0}).sort("ship_date", ASCENDING)
    )


def mark_superseded(
    db: Database, *, accurate_sj_id: str, replacement_sj_id: str
) -> dict[str, Any]:
    _ensure_once(db)
    doc = delivery_notes_collection(db).find_one_and_update(
        {"accurate_sj_id": str(accurate_sj_id)},
        {"$set": {"superseded_by": str(replacement_sj_id)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    return doc


def insert_loading_event(
    db: Database,
    *,
    accurate_sj_id: str,
    accurate_po_id: str,
    sj_no: str,
    items: list[dict[str, Any]],
    actor_email: str,
    idempotency_key: str,
    session: Any | None = None,
) -> dict[str, Any]:
    """Satu event Loading per SJ; insert kedua ditolak oleh unique index."""
    _ensure_once(db)
    doc = {
        "accurate_sj_id": str(accurate_sj_id),
        "accurate_po_id": str(accurate_po_id),
        "sj_no": sj_no,
        "items": items,
        "actor_email": actor_email,
        "idempotency_key": idempotency_key,
        "created_at": datetime.now(timezone.utc),
    }
    loading_events_collection(db).insert_one(dict(doc), session=session)
    doc.pop("_id", None)
    return doc


def get_loading_event(db: Database, accurate_sj_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return loading_events_collection(db).find_one(
        {"accurate_sj_id": str(accurate_sj_id)}, {"_id": 0}
    )


def next_delivery_attempt(db: Database, accurate_sj_id: str) -> int:
    _ensure_once(db)
    return (
        delivery_events_collection(db).count_documents(
            {"accurate_sj_id": str(accurate_sj_id)}
        )
        + 1
    )


def insert_delivery_event(
    db: Database,
    *,
    accurate_sj_id: str,
    accurate_po_id: str,
    attempt: int,
    status: str,
    courier_email: str,
    recipient_name: str | None,
    photo_evidence_ids: list[str],
    signature_evidence_id: str | None,
    failure_reason: str | None,
) -> dict[str, Any]:
    _ensure_once(db)
    doc = {
        "delivery_event_id": f"delivery:{accurate_sj_id}:attempt-{attempt}",
        "accurate_sj_id": str(accurate_sj_id),
        "accurate_po_id": str(accurate_po_id),
        "attempt": attempt,
        "status": status,
        "courier_email": courier_email,
        "recipient_name": recipient_name,
        "photo_evidence_ids": list(photo_evidence_ids),
        "signature_evidence_id": signature_evidence_id,
        "failure_reason": failure_reason,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        delivery_events_collection(db).insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise ValueError(
            f"Percobaan pengiriman {attempt} untuk SJ {accurate_sj_id} sudah tercatat."
        ) from exc
    doc.pop("_id", None)
    return doc


def list_delivery_events(db: Database, accurate_sj_id: str) -> list[dict[str, Any]]:
    _ensure_once(db)
    return list(
        delivery_events_collection(db)
        .find({"accurate_sj_id": str(accurate_sj_id)}, {"_id": 0})
        .sort("attempt", ASCENDING)
    )
