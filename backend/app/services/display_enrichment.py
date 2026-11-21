"""Enrichment tampilan baris Jadwal / Tugas — outlet, warna item, tanggal SJ.

Tidak menulis data. Tidak mengubah otorisasi. Batch lookup saja.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.repositories import delivery_repository as deliveries
from app.repositories import schedule_repository as schedules
from app.repositories.po_repository import get_by_accurate_id
from app.repositories.settings_user_repository import (
    display_names_by_emails,
    operational_display_name,
)
from app.models.user import normalize_email


def outlet_name_from_po(po_doc: dict[str, Any] | None) -> str:
    """D1: Nama Outlet = nama customer/pelanggan PO."""
    if not isinstance(po_doc, dict):
        return ""
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    for candidate in (header.get("customer"), po_doc.get("customer")):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def paint_color_from_line(line: dict[str, Any] | None) -> str:
    """D2: Warna Cat = items.color (Accurate charField2) — bukan print_color."""
    if not isinstance(line, dict):
        return ""
    return str(line.get("color") or "").strip()


def _calendar_sort_key(raw: str) -> tuple[int, int, int, str]:
    """Urut kronologis kalender; dukung YYYY-MM-DD dan dd/MM/yyyy."""
    text = str(raw or "").strip()
    if not text:
        return (9999, 99, 99, text)
    # ISO
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            y, m, d = int(text[0:4]), int(text[5:7]), int(text[8:10])
            return (y, m, d, text)
        except ValueError:
            return (9999, 99, 99, text)
    # dd/MM/yyyy
    parts = text.split("/")
    if len(parts) == 3:
        try:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            return (y, m, d, text)
        except ValueError:
            return (9999, 99, 99, text)
    return (9999, 99, 99, text)


def unique_sorted_ship_dates(raw_dates: list[str]) -> list[str]:
    """Dedup nilai tanggal kalender; urut naik; kosong dibuang."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in raw_dates:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    out.sort(key=_calendar_sort_key)
    return out


def is_active_delivery_note(note: dict[str, Any] | None) -> bool:
    """SJ aktif: ada dokumen + belum digantikan (superseded_by kosong).

    Fail-closed: dokumen tanpa identitas / superseded tidak dipakai.
    """
    if not isinstance(note, dict):
        return False
    if not str(note.get("accurate_sj_id") or "").strip():
        return False
    if str(note.get("superseded_by") or "").strip():
        return False
    return True


def list_active_delivery_notes_for_po(
    db: Database, accurate_po_id: str
) -> list[dict[str, Any]]:
    notes = deliveries.list_delivery_notes(db, accurate_po_id=str(accurate_po_id))
    return [n for n in notes if is_active_delivery_note(n)]


def ship_dates_by_line_id(notes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map accurate_line_id → ship_dates unik dari SJ aktif yang memuat line."""
    buckets: dict[str, list[str]] = {}
    for note in notes:
        if not is_active_delivery_note(note):
            continue
        ship = str(note.get("ship_date") or "").strip()
        if not ship:
            continue
        for item in note.get("items") or []:
            if not isinstance(item, dict):
                continue
            line_id = str(item.get("accurate_line_id") or "").strip()
            if not line_id:
                continue
            buckets.setdefault(line_id, []).append(ship)
    return {lid: unique_sorted_ship_dates(vals) for lid, vals in buckets.items()}


def ship_dates_for_po(notes: list[dict[str, Any]]) -> list[str]:
    dates: list[str] = []
    for note in notes:
        if not is_active_delivery_note(note):
            continue
        ship = str(note.get("ship_date") or "").strip()
        if ship:
            dates.append(ship)
    return unique_sorted_ship_dates(dates)


def ship_dates_for_sj_id(
    db: Database, accurate_sj_id: str
) -> list[str]:
    note = deliveries.get_delivery_note(db, str(accurate_sj_id))
    if not is_active_delivery_note(note):
        return []
    ship = str((note or {}).get("ship_date") or "").strip()
    return [ship] if ship else []


def enrich_schedule_line(
    line_state: dict[str, Any],
    *,
    outlet_name: str,
    paint_color: str,
    ship_dates: list[str],
) -> dict[str, Any]:
    line_state["outlet_name"] = outlet_name or ""
    line_state["paint_color"] = paint_color or ""
    line_state["ship_dates"] = list(ship_dates)
    return line_state


def _po_line_identity_index(
    po_doc: dict[str, Any] | None,
) -> dict[str, dict[str, str]]:
    """Map accurate_line_id → {item_name, item_code} dari header.items PO.

    Identitas line wajib cocok; tidak menebak dari urutan/nama bebas.
    """
    out: dict[str, dict[str, str]] = {}
    if not isinstance(po_doc, dict):
        return out
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    items = header.get("items") if isinstance(header.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("accurate_line_id") or "").strip()
        if not line_id or line_id in out:
            continue
        out[line_id] = {
            "item_name": str(item.get("item_name") or "").strip(),
            "item_code": str(item.get("item_code") or "").strip(),
        }
    return out


def _sj_line_identity_index(
    note: dict[str, Any] | None,
) -> dict[str, dict[str, str]]:
    """Map accurate_line_id → identitas barang dari item SJ aktif (bila ada)."""
    out: dict[str, dict[str, str]] = {}
    if not isinstance(note, dict):
        return out
    for item in note.get("items") or []:
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("accurate_line_id") or "").strip()
        if not line_id or line_id in out:
            continue
        out[line_id] = {
            "item_name": str(item.get("item_name") or "").strip(),
            "item_code": str(item.get("item_code") or "").strip(),
        }
    return out


def enrich_operational_tasks(
    db: Database, task_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Batch-enrich task list: outlet_name, ship_dates, item_name (setelah auth).

    Nama Barang diambil dari PO item line yang terhubung (accurate_line_id),
    bukan dari urutan baris atau pencocokan teks bebas. Read-only — tidak
    menulis MongoDB / schema TaskRef.
    """
    if not task_rows:
        return task_rows

    po_ids: set[str] = set()
    sj_ids: set[str] = set()
    alloc_ids: set[str] = set()
    for task in task_rows:
        ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
        po_id = str(ref.get("accurate_po_id") or "").strip()
        sj_id = str(ref.get("accurate_delivery_note_id") or "").strip()
        alloc_id = str(ref.get("allocation_id") or "").strip()
        if po_id:
            po_ids.add(po_id)
        if sj_id:
            sj_ids.add(sj_id)
        if alloc_id:
            alloc_ids.add(alloc_id)

    po_by_id: dict[str, dict[str, Any]] = {}
    for po_id in po_ids:
        doc = get_by_accurate_id(db, po_id)
        if doc is not None:
            po_by_id[po_id] = doc

    line_identity_by_po: dict[str, dict[str, dict[str, str]]] = {
        po_id: _po_line_identity_index(doc) for po_id, doc in po_by_id.items()
    }

    notes_by_po: dict[str, list[dict[str, Any]]] = {}
    line_dates_by_po: dict[str, dict[str, list[str]]] = {}
    for po_id in po_ids:
        notes = list_active_delivery_notes_for_po(db, po_id)
        notes_by_po[po_id] = notes
        line_dates_by_po[po_id] = ship_dates_by_line_id(notes)

    sj_note_by_id: dict[str, dict[str, Any]] = {}
    sj_line_identity: dict[str, dict[str, dict[str, str]]] = {}
    for sj_id in sj_ids:
        note = deliveries.get_delivery_note(db, sj_id)
        if is_active_delivery_note(note):
            sj_note_by_id[sj_id] = note  # type: ignore[assignment]
            sj_line_identity[sj_id] = _sj_line_identity_index(note)

    # Warna Cat SoT = allocation.print_color (input KP), bukan Accurate item.color.
    print_color_by_alloc: dict[str, str] = {}
    for alloc_id in alloc_ids:
        alloc = schedules.get_allocation(db, alloc_id)
        if not isinstance(alloc, dict):
            continue
        color = str(alloc.get("print_color") or "").strip()
        print_color_by_alloc[alloc_id] = color

    for task in task_rows:
        ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
        # Pastikan ref mutable dict pada task (enrichment in-place).
        if not isinstance(task.get("ref"), dict):
            task["ref"] = ref
        po_id = str(ref.get("accurate_po_id") or "").strip()
        line_id = str(ref.get("accurate_line_id") or "").strip()
        sj_id = str(ref.get("accurate_delivery_note_id") or "").strip()

        outlet = ""
        if po_id and po_id in po_by_id:
            outlet = outlet_name_from_po(po_by_id[po_id])
        elif sj_id and sj_id in sj_note_by_id:
            outlet = str(sj_note_by_id[sj_id].get("customer_name") or "").strip()

        ship_dates: list[str] = []
        if sj_id:
            note = sj_note_by_id.get(sj_id)
            if note is not None:
                ship = str(note.get("ship_date") or "").strip()
                ship_dates = [ship] if ship else []
        elif po_id and line_id:
            ship_dates = list(line_dates_by_po.get(po_id, {}).get(line_id) or [])
        elif po_id:
            ship_dates = ship_dates_for_po(notes_by_po.get(po_id) or [])

        item_name = ""
        item_code = str(ref.get("item_code") or "").strip()
        if po_id and line_id:
            identity = line_identity_by_po.get(po_id, {}).get(line_id) or {}
            item_name = identity.get("item_name") or ""
            if not item_code:
                item_code = identity.get("item_code") or ""
        elif sj_id and line_id:
            identity = sj_line_identity.get(sj_id, {}).get(line_id) or {}
            item_name = identity.get("item_name") or ""
            if not item_code:
                item_code = identity.get("item_code") or ""
        # Fallback jujur dari context yang sudah tersimpan (bukan invent).
        if not item_name:
            ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
            item_name = str(ctx.get("item_name") or "").strip()

        if item_name:
            ref["item_name"] = item_name
        if item_code and not str(ref.get("item_code") or "").strip():
            ref["item_code"] = item_code
        # Top-level mirror (selaras outlet_name) untuk kontrak tampilan FE.
        task["item_name"] = item_name
        task["outlet_name"] = outlet
        task["ship_dates"] = ship_dates

        alloc_id = str(ref.get("allocation_id") or "").strip()
        if alloc_id:
            task["print_color"] = print_color_by_alloc.get(alloc_id, "")
        else:
            task["print_color"] = ""

        # Enrich mismatched_items (koordinasi Loading–SJ) bila line diketahui.
        ctx = task.get("context") if isinstance(task.get("context"), dict) else None
        if ctx and isinstance(ctx.get("mismatched_items"), list):
            po_lines = line_identity_by_po.get(po_id, {}) if po_id else {}
            sj_lines = sj_line_identity.get(sj_id, {}) if sj_id else {}
            for row in ctx["mismatched_items"]:
                if not isinstance(row, dict):
                    continue
                mid = str(row.get("accurate_line_id") or "").strip()
                if not mid:
                    continue
                ident = po_lines.get(mid) or sj_lines.get(mid) or {}
                if ident.get("item_name") and not str(row.get("item_name") or "").strip():
                    row["item_name"] = ident["item_name"]
                if ident.get("item_code") and not str(row.get("item_code") or "").strip():
                    row["item_code"] = ident["item_code"]

    return task_rows


def enrich_actor_display_names(
    db: Database, rows: list[dict[str, Any]], *, email_field: str = "actor_email"
) -> list[dict[str, Any]]:
    """Tambah actor_display_name dari Master (batch) — tidak mengubah storage email."""
    if not rows:
        return rows
    emails: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = str(row.get(email_field) or "").strip()
        if raw:
            emails.append(raw)
    names = display_names_by_emails(db, emails)
    for row in rows:
        if not isinstance(row, dict):
            continue
        email_n = normalize_email(str(row.get(email_field) or ""))
        raw_name = names.get(email_n, "") if email_n else ""
        row["actor_display_name"] = operational_display_name(raw_name)
    return rows
