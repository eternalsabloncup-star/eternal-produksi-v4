"""Status cetak read-only untuk baris Jadwal Pengiriman.

Qty terkonfirmasi = jumlah ``tasks.qty_confirmed`` pada tugas ``cetak``
(scope po_line_allocation). Angka itu adalah total ledger yang ditulis
``confirm_task_qty``; retry idempotency tidak menaikkan field ini.

Qty referensi = qty item line PO (``header.items.qty``), pasangan yang sama
dengan ``line_schedule_state.qty_target``. Bukan qty SJ, bukan qty alokasi
jadwal, dan bukan hasil Screen, QC, Loading, atau Pengiriman.

Qty cetak hidup di pasangan (accurate_po_id, accurate_line_id). Tidak ada
alokasi qty cetak ke Surat Jalan. Angka kumulatif hanya ditampilkan pada SJ
aktif yang menjadi satu-satunya pemilik pasangan itu. SJ lain, atau pasangan
yang muncul di lebih dari satu SJ aktif, menerima ``Belum tersedia``.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.repositories import delivery_repository as deliveries
from app.repositories.po_repository import po_collection
from app.repositories.task_repository import tasks_collection

PRINT_TASK = "cetak"
PRINT_KIND = "Cetak"
NON_PRINT_KIND = "Non-Cetak"

_UNAVAILABLE = {
    "state": "unavailable",
    "label": "Belum tersedia",
    "qty_confirmed": None,
    "qty_reference": None,
    "unit": None,
    "percent": None,
}
_NON_CETAK = {
    "state": "non_cetak",
    "label": "Non-Cetak",
    "qty_confirmed": None,
    "qty_reference": None,
    "unit": None,
    "percent": None,
}


def _pair(po_id: str, line_id: str) -> tuple[str, str]:
    return (str(po_id or "").strip(), str(line_id or "").strip())


def _superseded(note: dict[str, Any]) -> bool:
    return bool(str(note.get("superseded_by") or "").strip())


def _unavailable() -> dict[str, Any]:
    return dict(_UNAVAILABLE)


def _non_cetak() -> dict[str, Any]:
    return dict(_NON_CETAK)


def _numeric(confirmed: float, reference: float, unit: str) -> dict[str, Any]:
    if confirmed <= 1e-9:
        state, label = "belum", "Belum Cetak"
    elif confirmed + 1e-9 >= reference:
        state, label = "selesai", "Selesai Cetak"
    else:
        state, label = "proses", "Proses Cetak"
    percent = int(round((confirmed / reference) * 100))
    return {
        "state": state,
        "label": label,
        "qty_confirmed": confirmed,
        "qty_reference": reference,
        "unit": unit,
        "percent": percent,
    }


def _line_meta(
    db: Database, po_ids: list[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    if not po_ids:
        return meta
    for doc in po_collection(db).find(
        {"accurate_id": {"$in": po_ids}},
        {"_id": 0, "accurate_id": 1, "header.items": 1},
    ):
        po_id = str(doc.get("accurate_id") or "").strip()
        header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
        for item in header.get("items") or []:
            if not isinstance(item, dict):
                continue
            line_id = str(item.get("accurate_line_id") or "").strip()
            if not po_id or not line_id:
                continue
            meta[(po_id, line_id)] = {
                "process_kind": str(item.get("process_kind") or "").strip(),
                "qty": float(item.get("qty") or 0),
                "unit": str(item.get("unit") or "").strip(),
            }
    return meta


def _printed_by_pair(
    db: Database, po_ids: list[str], line_ids: list[str]
) -> dict[tuple[str, str], float]:
    printed: dict[tuple[str, str], float] = {}
    if not po_ids or not line_ids:
        return printed
    for row in tasks_collection(db).aggregate(
        [
            {
                "$match": {
                    "task_type": PRINT_TASK,
                    "ref.accurate_po_id": {"$in": po_ids},
                    "ref.accurate_line_id": {"$in": line_ids},
                }
            },
            {
                "$group": {
                    "_id": {
                        "po": "$ref.accurate_po_id",
                        "line": "$ref.accurate_line_id",
                    },
                    "total": {"$sum": "$qty_confirmed"},
                }
            },
        ]
    ):
        key_doc = row.get("_id") if isinstance(row.get("_id"), dict) else {}
        pair = _pair(str(key_doc.get("po") or ""), str(key_doc.get("line") or ""))
        if pair[0] and pair[1]:
            printed[pair] = float(row.get("total") or 0)
    return printed


def _active_owners(
    db: Database, po_ids: list[str], line_ids: list[str]
) -> dict[tuple[str, str], set[str]]:
    """SJ aktif (bukan superseded) yang memuat pasangan PO+line."""
    owners: dict[tuple[str, str], set[str]] = {}
    if not po_ids or not line_ids:
        return owners
    po_set = set(po_ids)
    line_set = set(line_ids)
    cursor = deliveries.delivery_notes_collection(db).find(
        {"items.accurate_line_id": {"$in": line_ids}},
        {
            "_id": 0,
            "accurate_sj_id": 1,
            "accurate_po_id": 1,
            "superseded_by": 1,
            "items.accurate_line_id": 1,
            "items.accurate_po_id": 1,
        },
    )
    for note in cursor:
        if _superseded(note):
            continue
        sj_id = str(note.get("accurate_sj_id") or "").strip()
        if not sj_id:
            continue
        seen: set[tuple[str, str]] = set()
        for item in note.get("items") or []:
            if not isinstance(item, dict):
                continue
            line_id = str(item.get("accurate_line_id") or "").strip()
            if line_id not in line_set:
                continue
            po_id = deliveries.item_source_po_id(item, note)
            if po_id not in po_set:
                continue
            pair = (po_id, line_id)
            if pair in seen:
                continue
            seen.add(pair)
            owners.setdefault(pair, set()).add(sj_id)
    return owners


def _status_for_row(
    row: dict[str, Any],
    *,
    meta: dict[tuple[str, str], dict[str, Any]],
    printed: dict[tuple[str, str], float],
    owners: dict[tuple[str, str], set[str]],
) -> dict[str, Any]:
    items = [i for i in (row.get("items") or []) if isinstance(i, dict)]
    if not items:
        return _unavailable()
    sj_id = str(row.get("accurate_sj_id") or "").strip()
    if not sj_id or _superseded(row):
        return _unavailable()

    cetak: list[tuple[str, str]] = []
    saw_non_cetak = False
    for item in items:
        po_id = deliveries.item_source_po_id(item, row)
        line_id = str(item.get("accurate_line_id") or "").strip()
        if not po_id or not line_id:
            return _unavailable()
        info = meta.get((po_id, line_id))
        if info is None:
            return _unavailable()
        kind = info["process_kind"]
        if kind == NON_PRINT_KIND:
            saw_non_cetak = True
            continue
        if kind != PRINT_KIND:
            return _unavailable()
        cetak.append((po_id, line_id))

    unique = list(dict.fromkeys(cetak))
    if not unique:
        return _non_cetak() if saw_non_cetak else _unavailable()

    unit: str | None = None
    confirmed = 0.0
    reference = 0.0
    for pair in unique:
        if owners.get(pair) != {sj_id}:
            return _unavailable()
        info = meta[pair]
        qty = float(info["qty"])
        row_unit = str(info["unit"] or "")
        if qty <= 0 or not row_unit:
            return _unavailable()
        if unit is None:
            unit = row_unit
        elif unit != row_unit:
            return _unavailable()
        qty_confirmed = float(printed.get(pair) or 0)
        if qty_confirmed < -1e-9:
            return _unavailable()
        confirmed += qty_confirmed
        reference += qty
    if unit is None or reference <= 0:
        return _unavailable()
    return _numeric(confirmed, reference, unit)


def apply_schedule_print_status(db: Database, rows: list[dict[str, Any]]) -> None:
    """Isi ``print_status`` pada salinan baris jadwal. Tidak menulis database."""
    po_ids: list[str] = []
    line_ids: list[str] = []
    seen_po: set[str] = set()
    seen_line: set[str] = set()
    for row in rows:
        for item in row.get("items") or []:
            if not isinstance(item, dict):
                continue
            po_id = deliveries.item_source_po_id(item, row)
            line_id = str(item.get("accurate_line_id") or "").strip()
            if po_id and po_id not in seen_po:
                seen_po.add(po_id)
                po_ids.append(po_id)
            if line_id and line_id not in seen_line:
                seen_line.add(line_id)
                line_ids.append(line_id)

    meta = _line_meta(db, po_ids)
    printed = _printed_by_pair(db, po_ids, line_ids)
    owners = _active_owners(db, po_ids, line_ids)
    for row in rows:
        row["print_status"] = _status_for_row(
            row, meta=meta, printed=printed, owners=owners
        )
