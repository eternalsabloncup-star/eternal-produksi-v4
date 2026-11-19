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
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.integrations.accurate.fields import as_text, business_label, nested_label

DELIVERY_NOTES_COLLECTION = "accurate_delivery_notes"
LOADING_EVENTS_COLLECTION = "loading_events"
DELIVERY_EVENTS_COLLECTION = "delivery_events"
NOTE_REVISIONS_COLLECTION = "delivery_note_revisions"

DELIVERY_PENDING = "menunggu_pengiriman"
DELIVERY_SUCCESS = "berhasil"
DELIVERY_FAILED = "gagal"

# Status revisi SJ (Opsi B — keputusan Owner 19 Nov 2026).
REVISION_ACTIVE = "aktif"
REVISION_NEEDS_RELOADING = "PERLU_LOADING_ULANG"
REVISION_NEEDS_ADMIN_CORRECTION = "PERLU_KOREKSI_ADMINISTRASI"

LOADING_ACTIVE = "aktif"
LOADING_CANCELLED = "dibatalkan"


class DeliveryNoteIdentityError(ValueError):
    """Identitas SJ/PO line tidak lengkap — tidak boleh ditebak dari nama barang."""

    def __init__(self, message: str, *, code: str = "DELIVERY_NOTE_IDENTITY") -> None:
        super().__init__(message)
        self.code = code


def delivery_notes_collection(db: Database) -> Collection:
    return db[DELIVERY_NOTES_COLLECTION]


def loading_events_collection(db: Database) -> Collection:
    return db[LOADING_EVENTS_COLLECTION]


def delivery_events_collection(db: Database) -> Collection:
    return db[DELIVERY_EVENTS_COLLECTION]


def note_revisions_collection(db: Database) -> Collection:
    return db[NOTE_REVISIONS_COLLECTION]


# Spesifikasi indeks delivery — sumber kebenaran untuk createIndex + preflight.
# createIndex tidak boleh dijalankan di dalam transaksi MongoDB.
REQUIRED_DELIVERY_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": DELIVERY_NOTES_COLLECTION,
        "keys": [("accurate_sj_id", ASCENDING)],
        "name": "uniq_accurate_sj_id",
        "unique": True,
    },
    {
        "collection": DELIVERY_NOTES_COLLECTION,
        "keys": [("accurate_po_id", ASCENDING)],
        "name": "idx_sj_po",
        "unique": False,
    },
    {
        "collection": DELIVERY_NOTES_COLLECTION,
        "keys": [("ship_date", ASCENDING)],
        "name": "idx_sj_ship_date",
        "unique": False,
    },
    {
        "collection": LOADING_EVENTS_COLLECTION,
        "keys": [("accurate_sj_id", ASCENDING)],
        "name": "uniq_active_loading_per_sj",
        "unique": True,
        "partialFilterExpression": {"status": LOADING_ACTIVE},
    },
    {
        "collection": LOADING_EVENTS_COLLECTION,
        "keys": [("loading_id", ASCENDING)],
        "name": "uniq_loading_id",
        "unique": True,
    },
    {
        "collection": NOTE_REVISIONS_COLLECTION,
        "keys": [("accurate_sj_id", ASCENDING), ("version", ASCENDING)],
        "name": "uniq_note_revision_version",
        "unique": True,
    },
    {
        "collection": DELIVERY_EVENTS_COLLECTION,
        "keys": [("accurate_sj_id", ASCENDING), ("attempt", ASCENDING)],
        "name": "uniq_delivery_attempt",
        "unique": True,
    },
]


def ensure_delivery_indexes(db: Database) -> list[str]:
    """Buat indeks delivery. Hanya untuk bootstrap eksplisit / test — bukan startup API."""
    created: list[str] = []
    for spec in REQUIRED_DELIVERY_INDEX_SPECS:
        coll = db[spec["collection"]]
        kwargs: dict[str, Any] = {"name": spec["name"], "unique": bool(spec.get("unique"))}
        if "partialFilterExpression" in spec:
            kwargs["partialFilterExpression"] = spec["partialFilterExpression"]
        created.append(coll.create_index(spec["keys"], **kwargs))
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified(
        db.name, [s["name"] for s in REQUIRED_DELIVERY_INDEX_SPECS]
    )
    return created


def require_delivery_indexes(db: Database, *, session: Any | None = None) -> None:
    """Fail-closed: verifikasi spek indeks tanpa createIndex.

    Di dalam transaksi (``session`` set) tidak ada DDL dan tidak ada listIndexes
    ulang — pemanggil wajib ``require_*`` di luar transaksi terlebih dulu.
    """
    if session is not None:
        return
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_DELIVERY_INDEX_SPECS)


def _ensure_once(db: Database, *, session: Any | None = None) -> None:
    """Alias request-path: fail-closed require (bukan lazy createIndex)."""
    require_delivery_indexes(db, session=session)


def _as_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _as_bool(value: Any) -> bool | None:
    """Tri-state: None berarti Accurate tidak mengirim field tersebut."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = as_text(value).lower()
    if text in {"true", "1", "ya", "yes"}:
        return True
    if text in {"false", "0", "tidak", "no"}:
        return False
    return None


def _first_label(payload: dict[str, Any], *keys: str) -> str:
    """Label pertama yang terisi, menerima bentuk lookup maupun string."""
    for key in keys:
        label = business_label(payload.get(key))
        if label:
            return label
    return ""


# Header SJ yang dibaca dari payload Accurate. Field yang tidak dikirim tetap
# kosong dan dicatat di ``source_meta.absent_fields`` — tidak pernah ditebak.
_HEADER_SOURCE_KEYS = (
    "number",
    "transDate",
    "customer",
    "customerNo",
    "toAddress",
    "poNumber",
    "shipment",
    "description",
    "branch",
    "masterSalesman",
    "masterSalesmanId",
    "charField3",
    "printedByUser",
    "deliveryPrintedTime",
    "deliveryPackingNumber",
    "createdBy",
    "createdByUserName",
    "statusName",
    "attachmentExist",
    "contactInfo",
    "contactInfoId",
)


def _normalize_item(raw: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    sj_line_id = as_text(raw.get("id") or raw.get("sj_line_id"))
    if not sj_line_id:
        raise DeliveryNoteIdentityError("Setiap item SJ wajib memiliki ID baris.")
    if sj_line_id in seen:
        raise DeliveryNoteIdentityError(f"ID baris SJ duplikat: {sj_line_id}")
    seen.add(sj_line_id)

    po_line_id = as_text(raw.get("salesOrderDetailId") or raw.get("accurate_line_id"))
    if not po_line_id:
        raise DeliveryNoteIdentityError(
            f"Item SJ {sj_line_id} tidak memiliki referensi item line PO "
            "(accurate_line_id) — relasi tidak boleh ditebak dari nama barang."
        )

    item_obj = raw.get("item") if isinstance(raw.get("item"), dict) else {}
    return {
        "sj_line_id": sj_line_id,
        "accurate_line_id": po_line_id,
        "item_code": nested_label(item_obj, "no", "itemNo", "code")
        or as_text(raw.get("itemNo") or raw.get("item_code")),
        "item_name": as_text(raw.get("detailName") or raw.get("item_name"))
        or nested_label(item_obj, "name"),
        "qty": _as_float(raw.get("quantity", raw.get("qty"))),
        "unit": _first_label(raw, "itemUnit", "unit")
        or as_text(raw.get("itemUnitName")),
        "warehouse_name": _first_label(raw, "warehouse")
        or as_text(raw.get("warehouse_name")),
        "detail_notes": as_text(raw.get("detailNotes") or raw.get("detail_notes")),
    }


def _customer_po_from_items(raw_items: list[Any]) -> str:
    """PO pelanggan dari item — bukan pengganti nomor Sales Order."""
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        from_item = as_text(raw.get("salesOrderPoNumber"))
        if from_item:
            return from_item
        so = raw.get("salesOrder")
        if isinstance(so, dict):
            from_so = as_text(so.get("poNumber"))
            if from_so:
                return from_so
    return ""


def _resolve_sales_order(
    raw_items: list[Any], payload: dict[str, Any]
) -> tuple[str, str]:
    """Identitas SO: utamakan ``detailItem[].salesOrder.id``.

    - ``salesOrderId`` item dicek konsisten terhadap ``salesOrder.id``.
    - Header ``salesOrder`` boolean Accurate diabaikan (bukan objek SO).
    - Multi-SO dalam satu SJ → ``MULTI_SO_NOT_SUPPORTED`` (fail-closed).
    """
    pairs: dict[str, str] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        so = raw.get("salesOrder")
        so_id_from_obj = as_text(so.get("id")) if isinstance(so, dict) else ""
        so_id_flat = as_text(raw.get("salesOrderId"))
        if so_id_from_obj and so_id_flat and so_id_from_obj != so_id_flat:
            raise DeliveryNoteIdentityError(
                "salesOrderId item bertentangan dengan salesOrder.id — "
                "identitas SO tidak boleh ditebak.",
                code="SO_ID_CONFLICT",
            )
        so_id = so_id_from_obj or so_id_flat
        if not so_id:
            continue
        so_no = as_text(so.get("number")) if isinstance(so, dict) else ""
        if so_id in pairs and pairs[so_id] and so_no and pairs[so_id] != so_no:
            raise DeliveryNoteIdentityError(
                "Nomor Sales Order tidak konsisten antar item SJ.",
                code="SO_NUMBER_CONFLICT",
            )
        pairs[so_id] = so_no or pairs.get(so_id, "")

    if len(pairs) > 1:
        # Hanya jumlah SO — jangan paparkan nomor pelanggan/ID mentah di pesan.
        raise DeliveryNoteIdentityError(
            f"MULTI_SO_NOT_SUPPORTED: satu Surat Jalan merujuk {len(pairs)} "
            "Sales Order berbeda — sinkron otomatis ditolak.",
            code="MULTI_SO_NOT_SUPPORTED",
        )

    line_po_id, line_po_no = ("", "")
    if pairs:
        line_po_id, line_po_no = next(iter(pairs.items()))

    # Header salesOrderId legacy (bila ada) harus cocok; header salesOrder boolean diabaikan.
    header_so = payload.get("salesOrder")
    header_po_id = ""
    if isinstance(header_so, dict):
        header_po_id = as_text(header_so.get("id"))
        if not line_po_no:
            line_po_no = as_text(header_so.get("number"))
    header_po_id = (
        header_po_id
        or as_text(payload.get("salesOrderId") or payload.get("accurate_po_id"))
    )
    if header_po_id and line_po_id and header_po_id != line_po_id:
        raise DeliveryNoteIdentityError(
            "salesOrderId header bertentangan dengan salesOrder item — "
            "identitas SO tidak boleh ditebak.",
            code="SO_ID_CONFLICT",
        )

    accurate_po_id = line_po_id or header_po_id
    if not accurate_po_id:
        raise DeliveryNoteIdentityError("Surat Jalan wajib merujuk Accurate PO ID.")
    po_no = (
        as_text(payload.get("salesOrderNumber") or payload.get("po_no")) or line_po_no
    )
    return accurate_po_id, po_no


def normalize_delivery_note(payload: dict[str, Any]) -> dict[str, Any]:
    """Snapshot SJ Accurate (Delivery Order) → model internal.

    Identitas (SJ ID, nomor SJ, PO, item line) fail-closed. Field header lain
    bersifat opsional: yang tidak dikirim Accurate disimpan kosong dan dicatat
    pada ``source_meta.absent_fields`` agar UI menampilkannya sebagai kosong,
    bukan nilai karangan.
    """
    accurate_sj_id = as_text(payload.get("id") or payload.get("accurate_sj_id"))
    if not accurate_sj_id:
        raise DeliveryNoteIdentityError("Surat Jalan wajib memiliki Accurate SJ ID.")
    sj_no = as_text(payload.get("number") or payload.get("sj_no"))
    if not sj_no:
        raise DeliveryNoteIdentityError("Surat Jalan wajib memiliki nomor SJ asli.")
    raw_items = payload.get("detailItem") or payload.get("items") or []
    if not isinstance(raw_items, list) or not raw_items:
        raise DeliveryNoteIdentityError("Surat Jalan wajib memiliki item.")

    seen: set[str] = set()
    items = [_normalize_item(raw, seen) for raw in raw_items if isinstance(raw, dict)]
    if not items:
        raise DeliveryNoteIdentityError("Surat Jalan wajib memiliki item.")

    accurate_po_id, po_no = _resolve_sales_order(raw_items, payload)

    sj_date = as_text(payload.get("transDate") or payload.get("sj_date"))
    # Accurate DO tidak punya tanggal kirim terpisah; tanggal transaksi dipakai
    # sebagai tanggal kirim kecuali sumber benar-benar mengirim shipDate.
    explicit_ship_date = as_text(
        payload.get("shipDate") or payload.get("deliveryDate") or payload.get("ship_date")
    )
    ship_date = explicit_ship_date or sj_date

    # charField3 = field tambahan netral; semantik zona BELUM terverifikasi.
    # Disalin ke ship_zone untuk tampilan UI, selalu ditandai unproven.
    char_field3 = _first_label(payload, "charField3") or as_text(
        payload.get("char_field3")
    )
    ship_zone = char_field3 or as_text(payload.get("ship_zone"))

    absent = [key for key in _HEADER_SOURCE_KEYS if payload.get(key) in (None, "")]

    source_meta: dict[str, Any] = {"absent_fields": absent}
    if char_field3:
        source_meta["char_field3_raw"] = char_field3
        source_meta["char_field3_zone_unproven"] = True
    if explicit_ship_date:
        source_meta["ship_date_source"] = "shipDate"
    elif sj_date:
        source_meta["ship_date_source"] = "transDate"

    master_salesman_id = as_text(
        payload.get("masterSalesmanId") or payload.get("master_salesman_id")
    )
    if not master_salesman_id:
        salesman_obj = payload.get("masterSalesman")
        if isinstance(salesman_obj, dict):
            master_salesman_id = as_text(salesman_obj.get("id"))

    created_by_id = as_text(payload.get("createdBy") or payload.get("created_by_id"))

    customer_po_no = as_text(
        payload.get("poNumber") or payload.get("customer_po_no")
    ) or _customer_po_from_items(raw_items)

    return {
        "accurate_sj_id": accurate_sj_id,
        "sj_no": sj_no,
        "accurate_po_id": accurate_po_id,
        # Identitas PO V4 = nomor Sales Order Accurate, bukan DO.poNumber.
        "po_no": po_no,
        # DO.poNumber / salesOrderPoNumber = nomor PO pelanggan, terpisah.
        "customer_po_no": customer_po_no,
        "sj_date": sj_date,
        "ship_date": ship_date,
        "customer_name": _first_label(payload, "customer", "customerName")
        or as_text(payload.get("customer_name")),
        "customer_no": as_text(payload.get("customerNo") or payload.get("customer_no")),
        "destination": business_label(payload.get("toAddress"))
        or as_text(payload.get("destination")),
        "contact_info": _first_label(payload, "contactInfo")
        or as_text(payload.get("contact_info")),
        "contact_phone": as_text(
            payload.get("contactPhone")
            or payload.get("mobilePhone")
            or payload.get("contact_phone")
        ),
        "salesman_name": _first_label(payload, "masterSalesman")
        or as_text(payload.get("salesman_name")),
        "master_salesman_id": master_salesman_id,
        "created_by_id": created_by_id,
        "created_by_user_name": as_text(
            payload.get("createdByUserName") or payload.get("created_by_user_name")
        ),
        "shipment": _first_label(payload, "shipment")
        or as_text(payload.get("shipmentName")),
        "char_field3": char_field3,
        # Tampilkan nilai mentah; jangan klaim sebagai zona terverifikasi.
        "ship_zone": ship_zone,
        "branch_name": _first_label(payload, "branch") or as_text(payload.get("branch_name")),
        "description": as_text(payload.get("description") or payload.get("notes")),
        # printedByUser ≠ pembuat data; keduanya disimpan terpisah.
        "printed_by_user": _first_label(payload, "printedByUser", "printUserName", "userPrinted")
        or as_text(payload.get("printed_by_user")),
        "printed_time": as_text(
            payload.get("deliveryPrintedTime")
            or payload.get("printedTime")
            or payload.get("printed_time")
        ),
        "packing_number": as_text(
            payload.get("deliveryPackingNumber") or payload.get("packing_number")
        ),
        "accurate_status_name": as_text(
            payload.get("statusName") or payload.get("accurate_status_name")
        ),
        "attachment_exist": _as_bool(
            payload.get("attachmentExist", payload.get("attachment_exist"))
        ),
        "items": items,
        "source_meta": source_meta,
        "superseded_by": as_text(payload.get("superseded_by")) or None,
    }


_ITEM_SIGNATURE_FIELDS = (
    "accurate_line_id",
    "item_code",
    "item_name",
    "qty",
    "unit",
    "warehouse_name",
    "detail_notes",
)

# Field header yang perubahannya dicatat pada riwayat revisi. Perubahan header
# saja tidak menaikkan versi SJ: aturan Opsi B hanya berlaku untuk item line.
_HEADER_DIFF_FIELDS = (
    "po_no",
    "customer_po_no",
    "customer_name",
    "customer_no",
    "destination",
    "contact_info",
    "contact_phone",
    "salesman_name",
    "created_by_user_name",
    "shipment",
    "ship_zone",
    "branch_name",
    "description",
    "printed_by_user",
    "printed_time",
    "packing_number",
    "accurate_status_name",
    "attachment_exist",
    "sj_date",
)


def _item_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    """Field bisnis item; key baru yang belum ada di snapshot lama = kosong."""
    return tuple(item.get(field) or "" for field in _ITEM_SIGNATURE_FIELDS)


def diff_note_header(
    before: dict[str, Any] | None, after: dict[str, Any]
) -> list[dict[str, Any]]:
    """Field header SJ yang berubah antara dua snapshot."""
    if before is None:
        return []
    changes: list[dict[str, Any]] = []
    for field in _HEADER_DIFF_FIELDS:
        prev = before.get(field)
        curr = after.get(field)
        if (prev or "") == (curr or ""):
            continue
        changes.append({"field": field, "before": prev, "after": curr})
    return changes


def diff_note_items(
    before: dict[str, Any] | None, after: dict[str, Any]
) -> list[dict[str, Any]]:
    """Item line yang berubah antara dua snapshot SJ (per ``sj_line_id``)."""
    if before is None:
        return []
    old = {str(i["sj_line_id"]): i for i in before.get("items") or []}
    new = {str(i["sj_line_id"]): i for i in after.get("items") or []}
    changes: list[dict[str, Any]] = []
    for line_id in sorted(set(old) | set(new)):
        prev = old.get(line_id)
        curr = new.get(line_id)
        if prev is not None and curr is not None and _item_signature(prev) == _item_signature(curr):
            continue
        changes.append(
            {
                "sj_line_id": line_id,
                "accurate_line_id": (curr or prev or {}).get("accurate_line_id"),
                "qty_before": (prev or {}).get("qty"),
                "qty_after": (curr or {}).get("qty"),
                "change": "ditambahkan"
                if prev is None
                else "dihapus"
                if curr is None
                else "diubah",
            }
        )
    return changes


def upsert_delivery_note(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    """Idempoten per ``accurate_sj_id``; snapshot + histori revisi atomik.

    Update snapshot dan insert histori versi di satu transaksi replica set.
    Payload tidak berubah → tidak menambah versi / histori.
    """
    from app.repositories import task_repository as tasks

    _ensure_once(db)
    doc = normalize_delivery_note(payload)

    def _operation(session: Any) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        before = delivery_notes_collection(db).find_one(
            {"accurate_sj_id": doc["accurate_sj_id"]}, {"_id": 0}, session=session
        )

        changes = diff_note_items(before, doc)
        header_changes = diff_note_header(before, doc)
        ship_date_changed = before is not None and before.get("ship_date") != doc.get(
            "ship_date"
        )
        # Item/ship_date = revisi versi; header-only = update snapshot tanpa bump versi.
        changed = bool(changes) or ship_date_changed
        version = int((before or {}).get("sj_version") or 0) + (
            1 if changed or before is None else 0
        )
        if before is not None and not changed:
            version = int(before.get("sj_version") or 1)

        update: dict[str, Any] = {**doc, "synced_at": now, "sj_version": version}
        if before is None:
            update["revision_status"] = REVISION_ACTIVE

        delivery_notes_collection(db).update_one(
            {"accurate_sj_id": doc["accurate_sj_id"]},
            {"$set": update, "$setOnInsert": {"created_at": now}},
            upsert=True,
            session=session,
        )

        # Histori hanya untuk insert awal atau perubahan item/ship_date.
        if changed or before is None:
            note_revisions_collection(db).insert_one(
                {
                    "accurate_sj_id": doc["accurate_sj_id"],
                    "version": version,
                    "snapshot_before": before,
                    "snapshot_after": doc,
                    "changed_items": changes,
                    "changed_header_fields": header_changes,
                    "ship_date_changed": ship_date_changed,
                    "created_at": now,
                },
                session=session,
            )

        after = delivery_notes_collection(db).find_one(
            {"accurate_sj_id": doc["accurate_sj_id"]}, {"_id": 0}, session=session
        )
        return {
            "delivery_note": after,
            "was_insert": before is None,
            "source_changed": changed,
            "changed_items": changes,
            "changed_header_fields": header_changes,
            "sj_version": version,
        }

    # Race concurrent: WriteConflict / DuplicateKey versi → retry.
    last_err: Exception | None = None
    for _ in range(12):
        try:
            return tasks.run_in_transaction(db, _operation, attempts=8)
        except DuplicateKeyError as exc:
            last_err = exc
            continue
        except OperationFailure as exc:
            if exc.has_error_label("TransientTransactionError"):
                last_err = exc
                continue
            raise
    raise last_err if last_err else RuntimeError("upsert_delivery_note gagal")


def list_note_revisions(db: Database, accurate_sj_id: str) -> list[dict[str, Any]]:
    _ensure_once(db)
    return list(
        note_revisions_collection(db)
        .find({"accurate_sj_id": str(accurate_sj_id)}, {"_id": 0})
        .sort("version", ASCENDING)
    )


def set_revision_status(
    db: Database,
    *,
    accurate_sj_id: str,
    status: str,
    reason: str | None = None,
    session: Any | None = None,
) -> dict[str, Any] | None:
    _ensure_once(db, session=session)
    return delivery_notes_collection(db).find_one_and_update(
        {"accurate_sj_id": str(accurate_sj_id)},
        {
            "$set": {
                "revision_status": status,
                "revision_reason": reason,
                "revision_status_at": datetime.now(timezone.utc),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
        session=session,
    )


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


def next_loading_sequence(db: Database, accurate_sj_id: str) -> int:
    _ensure_once(db)
    return (
        loading_events_collection(db).count_documents(
            {"accurate_sj_id": str(accurate_sj_id)}
        )
        + 1
    )


def insert_loading_event(
    db: Database,
    *,
    accurate_sj_id: str,
    accurate_po_id: str,
    sj_no: str,
    items: list[dict[str, Any]],
    actor_email: str,
    idempotency_key: str,
    sj_version: int = 1,
    sequence: int = 1,
    session: Any | None = None,
) -> dict[str, Any]:
    """Satu Loading aktif per SJ; versi batal tetap tersimpan sebagai histori."""
    _ensure_once(db, session=session)
    doc = {
        "loading_id": f"loading:{accurate_sj_id}:v{sequence}",
        "accurate_sj_id": str(accurate_sj_id),
        "accurate_po_id": str(accurate_po_id),
        "sj_no": sj_no,
        "sj_version": sj_version,
        "sequence": sequence,
        "status": LOADING_ACTIVE,
        "items": items,
        "actor_email": actor_email,
        "idempotency_key": idempotency_key,
        "created_at": datetime.now(timezone.utc),
        "cancelled_at": None,
        "cancelled_by": None,
        "cancel_reason": None,
    }
    loading_events_collection(db).insert_one(dict(doc), session=session)
    doc.pop("_id", None)
    return doc


def get_loading_event(db: Database, accurate_sj_id: str) -> dict[str, Any] | None:
    """Loading yang sedang aktif untuk satu SJ (bukan yang sudah dibatalkan)."""
    _ensure_once(db)
    return loading_events_collection(db).find_one(
        {"accurate_sj_id": str(accurate_sj_id), "status": LOADING_ACTIVE}, {"_id": 0}
    )


def get_loading_by_id(db: Database, loading_id: str) -> dict[str, Any] | None:
    _ensure_once(db)
    return loading_events_collection(db).find_one({"loading_id": loading_id}, {"_id": 0})


def list_loading_events(db: Database, accurate_sj_id: str) -> list[dict[str, Any]]:
    _ensure_once(db)
    return list(
        loading_events_collection(db)
        .find({"accurate_sj_id": str(accurate_sj_id)}, {"_id": 0})
        .sort("sequence", ASCENDING)
    )


def cancel_loading_event(
    db: Database,
    *,
    loading_id: str,
    actor_email: str,
    reason: str,
    session: Any | None = None,
) -> dict[str, Any] | None:
    """Batalkan satu Loading aktif. Dua kali pembatalan menghasilkan None."""
    _ensure_once(db, session=session)
    return loading_events_collection(db).find_one_and_update(
        {"loading_id": loading_id, "status": LOADING_ACTIVE},
        {
            "$set": {
                "status": LOADING_CANCELLED,
                "cancelled_at": datetime.now(timezone.utc),
                "cancelled_by": actor_email,
                "cancel_reason": reason,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
        session=session,
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
