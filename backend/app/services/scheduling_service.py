"""Penjadwalan Kepala Produksi: alokasi operator, revisi, penjadwalan ulang."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pymongo.database import Database

from app.domain.operational import TaskRef, build_allocation_id, build_task_key
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import schedule_repository as schedules
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event
from app.services.task_engine import require_action


class ScheduleGateError(RuntimeError):
    """Gate Design/Payment belum terpenuhi."""


class ScheduleQuantityError(ValueError):
    """Total alokasi melampaui Qty item line."""


class AllocationLockedError(RuntimeError):
    """Legacy: alokasi terkunci — digantikan aturan floor Qty cetak."""


class AllocationBelowConfirmedPrintError(RuntimeError):
    """Qty revisi di bawah Qty cetak terkonfirmasi untuk allocation_id ini."""

    code = "ALLOCATION_BELOW_CONFIRMED_PRINT"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "Qty alokasi tidak boleh lebih kecil dari Qty cetak yang sudah dikonfirmasi operator."
        )


class AllocationConcurrencyError(RuntimeError):
    """Revisi bentrok dengan perubahan konkuren pada alokasi yang sama."""


class AllocationDownstreamConflictError(RuntimeError):
    """Qty baru bentrok dengan progres Screen yang sudah tercatat (tanpa aturan pindah ledger)."""


# Pilihan operasional per alokasi — bukan Master Produk screen_size.
SCREEN_KIND_VALUES = frozenset({"Besar", "Kecil"})


def _line_from_po(po_doc: dict[str, Any], accurate_line_id: str) -> dict[str, Any]:
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    for item in header.get("items") or []:
        if isinstance(item, dict) and str(item.get("accurate_line_id")) == str(
            accurate_line_id
        ):
            return item
    raise LookupError(f"Item line tidak ditemukan pada PO: {accurate_line_id}")


def printed_qty_for_allocation(
    db: Database, *, accurate_po_id: str, accurate_line_id: str, allocation_id: str
) -> float:
    """Qty cetak terkonfirmasi dari ledger (0 bila tugas cetak belum ada)."""
    key = build_task_key(
        "cetak",
        "po_line_allocation",
        TaskRef(
            accurate_po_id=accurate_po_id,
            accurate_line_id=accurate_line_id,
            allocation_id=allocation_id,
        ),
    )
    task = tasks.get_task(db, key)
    return float((task or {}).get("qty_confirmed") or 0)


def printed_qty_for_line(
    db: Database, *, accurate_po_id: str, accurate_line_id: str
) -> float:
    total = 0.0
    for alloc in schedules.list_allocations(
        db, accurate_line_id=accurate_line_id, include_released=True
    ):
        total += printed_qty_for_allocation(
            db,
            accurate_po_id=accurate_po_id,
            accurate_line_id=accurate_line_id,
            allocation_id=alloc["allocation_id"],
        )
    return total


def _board_kpi_from_light_rows(
    db: Database, light_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """KPI global dari seluruh baris Cetak accepted — tanpa SJ detail / N+1 per line."""
    from app.repositories.gate_repository import GATE_OPEN, gates_collection
    from app.repositories.schedule_repository import (
        STATUS_ACTIVE,
        allocations_collection,
    )
    from app.repositories.task_repository import tasks_collection

    if not light_rows:
        return {
            "total_qty_so": 0.0,
            "qty_allocated": 0.0,
            "qty_printed": 0.0,
            "rows_unscheduled": 0,
            "rows_unlocked": 0,
            "print_line_count": 0,
        }

    # Key komposit PO+line — jangan gabungkan accurate_line_id lintas PO.
    line_ids = list({str(r["accurate_line_id"]) for r in light_rows})
    po_ids = list({str(r["accurate_po_id"]) for r in light_rows})
    pair_keys = {
        (str(r["accurate_po_id"]), str(r["accurate_line_id"])) for r in light_rows
    }

    alloc_by_pair: dict[tuple[str, str], float] = {}
    if line_ids and po_ids:
        for row in allocations_collection(db).aggregate(
            [
                {
                    "$match": {
                        "accurate_line_id": {"$in": line_ids},
                        "accurate_po_id": {"$in": po_ids},
                        "status": STATUS_ACTIVE,
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "po": "$accurate_po_id",
                            "line": "$accurate_line_id",
                        },
                        "total": {"$sum": "$qty"},
                    }
                },
            ]
        ):
            key_doc = row.get("_id") if isinstance(row.get("_id"), dict) else {}
            po_k = str(key_doc.get("po") or "")
            line_k = str(key_doc.get("line") or "")
            if (po_k, line_k) in pair_keys:
                alloc_by_pair[(po_k, line_k)] = float(row.get("total") or 0)

    printed_by_pair: dict[tuple[str, str], float] = {}
    if line_ids and po_ids:
        for row in tasks_collection(db).aggregate(
            [
                {
                    "$match": {
                        "task_type": "cetak",
                        "ref.accurate_line_id": {"$in": line_ids},
                        "ref.accurate_po_id": {"$in": po_ids},
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
            po_k = str(key_doc.get("po") or "")
            line_k = str(key_doc.get("line") or "")
            if (po_k, line_k) in pair_keys:
                printed_by_pair[(po_k, line_k)] = float(row.get("total") or 0)

    unlocked_pos: set[str] = set()
    if po_ids:
        for doc in gates_collection(db).find(
            {"accurate_po_id": {"$in": po_ids}},
            {"_id": 0, "accurate_po_id": 1, "design": 1, "payment": 1},
        ):
            po_id = str(doc.get("accurate_po_id") or "")
            design = doc.get("design") if isinstance(doc.get("design"), dict) else {}
            payment = (
                doc.get("payment") if isinstance(doc.get("payment"), dict) else {}
            )
            if (
                design.get("state") == GATE_OPEN
                and payment.get("state") == GATE_OPEN
            ):
                unlocked_pos.add(po_id)

    total_qty = 0.0
    qty_allocated = 0.0
    qty_printed = 0.0
    rows_unscheduled = 0
    rows_unlocked = 0
    for row in light_rows:
        line_id = str(row["accurate_line_id"])
        po_id = str(row["accurate_po_id"])
        pair = (po_id, line_id)
        target = float(row.get("qty_target") or 0)
        allocated = float(alloc_by_pair.get(pair) or 0)
        printed = float(printed_by_pair.get(pair) or 0)
        total_qty += target
        qty_allocated += allocated
        qty_printed += printed
        if max(target - allocated, 0.0) > 0:
            rows_unscheduled += 1
        if po_id in unlocked_pos:
            rows_unlocked += 1

    return {
        "total_qty_so": total_qty,
        "qty_allocated": qty_allocated,
        "qty_printed": qty_printed,
        "rows_unscheduled": rows_unscheduled,
        "rows_unlocked": rows_unlocked,
        "print_line_count": len(light_rows),
    }


def _attach_ship_dates_to_light_rows(
    db: Database, rows: list[dict[str, Any]]
) -> None:
    """Isi ship_dates pada light rows via batch SJ aktif (bukan per line)."""
    from app.repositories.delivery_repository import delivery_notes_collection
    from app.services.display_enrichment import (
        is_active_delivery_note,
        ship_dates_by_line_id,
    )

    po_ids = list({str(r["accurate_po_id"]) for r in rows})
    if not po_ids:
        return
    notes = list(
        delivery_notes_collection(db).find(
            {"accurate_po_id": {"$in": po_ids}}, {"_id": 0}
        )
    )
    by_po: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        if not is_active_delivery_note(note):
            continue
        po_id = str(note.get("accurate_po_id") or "")
        if not po_id:
            continue
        by_po.setdefault(po_id, []).append(note)
    dates_by_po = {
        po_id: ship_dates_by_line_id(po_notes) for po_id, po_notes in by_po.items()
    }
    for row in rows:
        po_id = str(row["accurate_po_id"])
        line_id = str(row["accurate_line_id"])
        row["ship_dates"] = list(dates_by_po.get(po_id, {}).get(line_id) or [])


def _apply_column_filters(
    rows: list[dict[str, Any]],
    column_filters: dict[str, list[str] | None] | None,
) -> list[dict[str, Any]]:
    if not column_filters:
        return rows
    out = rows
    mapping = {
        "outletName": "outlet_name",
        "paintColor": "paint_color",
        "poNo": "po_no",
        "itemName": "item_name",
        "lineId": "accurate_line_id",
    }
    for col_id, selected in column_filters.items():
        if selected is None:
            continue
        wanted = {str(v) for v in selected}
        if col_id == "shipDates":
            out = [
                r
                for r in out
                if wanted.intersection(
                    {str(d) for d in (r.get("ship_dates") or [])}
                )
            ]
            continue
        field = mapping.get(col_id)
        if not field:
            continue
        out = [r for r in out if str(r.get(field) or "") in wanted]
    return out


def _sort_light_rows(
    rows: list[dict[str, Any]],
    *,
    sort_by: str | None,
    sort_dir: str | None,
) -> list[dict[str, Any]]:
    from app.services.display_enrichment import _calendar_sort_key

    direction = (sort_dir or "desc").lower()
    reverse = direction != "asc"
    field_map = {
        "updated_at": "updated_at",
        "poNo": "po_no",
        "outletName": "outlet_name",
        "paintColor": "paint_color",
        "itemName": "item_name",
        "lineId": "accurate_line_id",
        "qtyTarget": "qty_target",
        "shipDates": "ship_dates",
    }
    key_field = field_map.get(sort_by or "", "updated_at")

    def sort_key(row: dict[str, Any]) -> tuple:
        if key_field == "ship_dates":
            dates = row.get("ship_dates") or []
            first = str(dates[0]) if dates else ""
            return (_calendar_sort_key(first), str(row.get("accurate_po_id") or ""), str(row.get("accurate_line_id") or ""))
        if key_field == "qty_target":
            return (
                float(row.get("qty_target") or 0),
                str(row.get("accurate_po_id") or ""),
                str(row.get("accurate_line_id") or ""),
            )
        return (
            str(row.get(key_field) or ""),
            str(row.get("accurate_po_id") or ""),
            str(row.get("accurate_line_id") or ""),
        )

    return sorted(rows, key=sort_key, reverse=reverse)


def _facets_from_light_rows(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    def uniq(field: str) -> list[str]:
        vals = sorted({str(r.get(field) or "") for r in rows if str(r.get(field) or "")})
        return vals

    ship: set[str] = set()
    for r in rows:
        for d in r.get("ship_dates") or []:
            text = str(d or "").strip()
            if text:
                ship.add(text)
    return {
        "outletName": uniq("outlet_name"),
        "paintColor": uniq("paint_color"),
        "poNo": uniq("po_no"),
        "itemName": uniq("item_name"),
        "lineId": uniq("accurate_line_id"),
        "shipDates": sorted(ship),
    }


def build_accepted_schedule_board(
    db: Database,
    *,
    branch_ids: list[str] | set[str] | None = None,
    branch_scope: bool = False,
    q: str | None = None,
    page: int = 1,
    page_size: int = 100,
    column_filters: dict[str, list[str] | None] | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> dict[str, Any]:
    """Board Jadwal: accepted-only + Cetak, filter/sort sebelum pagination, KPI global.

    Light rows di Python (bukan $facet BSON) agar aman dari batas dokumen agregasi.
    Enrich SJ penuh hanya untuk halaman; ship_dates batch dipakai bila filter/sort tanggal.
    """
    from app.repositories.po_repository import get_by_accurate_id, po_collection
    from app.services.display_enrichment import (
        list_active_delivery_notes_for_po,
        outlet_name_from_po,
        ship_dates_by_line_id,
    )
    from app.services.read_models import accepted_po_query

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 100), 500))
    skip = (page - 1) * page_size

    query = accepted_po_query(
        branch_ids=branch_ids,
        branch_scope=branch_scope,
        q=None,
    )
    text = (q or "").strip()
    post_unwind: dict[str, Any] = {
        "header.items.process_kind": "Cetak",
        "header.items.accurate_line_id": {"$exists": True, "$nin": [None, ""]},
    }
    if text:
        safe = text.replace("\\", "\\\\")
        post_unwind = {
            "$and": [
                post_unwind,
                {
                    "$or": [
                        {"po_no": {"$regex": safe, "$options": "i"}},
                        {"header.customer": {"$regex": safe, "$options": "i"}},
                        {"accurate_id": {"$regex": safe, "$options": "i"}},
                        {"header.items.item_name": {"$regex": safe, "$options": "i"}},
                        {"header.items.item_code": {"$regex": safe, "$options": "i"}},
                        {"header.items.color": {"$regex": safe, "$options": "i"}},
                        {
                            "header.items.accurate_line_id": {
                                "$regex": safe,
                                "$options": "i",
                            }
                        },
                    ]
                },
            ]
        }

    pipeline: list[dict[str, Any]] = [
        {"$match": query},
        {
            "$project": {
                "_id": 0,
                "accurate_id": 1,
                "po_no": 1,
                "updated_at": 1,
                "header.customer": 1,
                "header.items": 1,
            }
        },
        {"$unwind": "$header.items"},
        {"$match": post_unwind},
        {
            "$project": {
                "accurate_po_id": {"$toString": "$accurate_id"},
                "accurate_line_id": {"$toString": "$header.items.accurate_line_id"},
                "qty_target": {
                    "$convert": {
                        "input": "$header.items.qty",
                        "to": "double",
                        "onError": 0,
                        "onNull": 0,
                    }
                },
                "po_no": {"$ifNull": ["$po_no", ""]},
                "outlet_name": {"$ifNull": ["$header.customer", ""]},
                "paint_color": {"$ifNull": ["$header.items.color", ""]},
                "item_name": {"$ifNull": ["$header.items.item_name", ""]},
                "item_code": {"$ifNull": ["$header.items.item_code", ""]},
                "updated_at": 1,
            }
        },
    ]

    light_rows = list(po_collection(db).aggregate(pipeline))
    # Batch SJ sekali untuk facet tanggal + filter/sort shipDates (bukan per line).
    if light_rows:
        _attach_ship_dates_to_light_rows(db, light_rows)

    # Checklist header dari scope q (sebelum filter kolom) agar opsi tidak hilang.
    facets = _facets_from_light_rows(light_rows)
    filtered = _apply_column_filters(light_rows, column_filters)
    sorted_rows = _sort_light_rows(
        filtered, sort_by=sort_by or "updated_at", sort_dir=sort_dir or "desc"
    )

    total_rows = len(sorted_rows)
    total_pages = max(1, (total_rows + page_size - 1) // page_size) if total_rows else 1
    page_keys = sorted_rows[skip : skip + page_size]
    if total_rows == 0:
        total_pages = 1
        page_keys = []
    elif page > total_pages:
        page_keys = []

    kpi = _board_kpi_from_light_rows(db, sorted_rows)

    page_po_ids = list(dict.fromkeys(str(r["accurate_po_id"]) for r in page_keys))
    po_by_id: dict[str, dict[str, Any]] = {}
    dates_by_po: dict[str, dict[str, list[str]]] = {}
    for po_id in page_po_ids:
        doc = get_by_accurate_id(db, po_id)
        if doc is None:
            continue
        po_by_id[po_id] = doc
        notes = list_active_delivery_notes_for_po(db, po_id)
        dates_by_po[po_id] = ship_dates_by_line_id(notes)

    items: list[dict[str, Any]] = []
    for key in page_keys:
        po_id = str(key["accurate_po_id"])
        line_id = str(key["accurate_line_id"])
        po_doc = po_by_id.get(po_id)
        if po_doc is None:
            continue
        items.append(
            line_schedule_state(
                db,
                po_doc,
                line_id,
                outlet_name=outlet_name_from_po(po_doc),
                ship_dates=dates_by_po.get(po_id, {}).get(line_id, []),
            )
        )

    return {
        "items": items,
        "total": total_rows,
        "total_rows": total_rows,
        "page": page,
        "pageSize": page_size,
        "total_pages": total_pages,
        "po_count": len({str(r["accurate_po_id"]) for r in sorted_rows}),
        "accepted_only": True,
        "source": "mongodb",
        "kpi": kpi,
        "filter_facets": facets,
        "sort_by": sort_by or "updated_at",
        "sort_dir": (sort_dir or "desc").lower(),
    }


def line_schedule_state(
    db: Database,
    po_doc: dict[str, Any],
    accurate_line_id: str,
    *,
    outlet_name: str | None = None,
    ship_dates: list[str] | None = None,
) -> dict[str, Any]:
    accurate_po_id = str(po_doc.get("accurate_id") or "")
    line = _line_from_po(po_doc, accurate_line_id)
    allocations = schedules.list_allocations(
        db, accurate_line_id=accurate_line_id, include_released=True
    )
    from app.repositories import settings_user_repository as users

    allocated = schedules.active_allocated_qty(db, accurate_line_id)
    qty_target = float(line.get("qty") or 0)
    from app.services.production_service import screen_confirmed_qty

    for alloc in allocations:
        printed = printed_qty_for_allocation(
            db,
            accurate_po_id=accurate_po_id,
            accurate_line_id=accurate_line_id,
            allocation_id=alloc["allocation_id"],
        )
        alloc["qty_printed"] = printed
        # Jangan isi default palsu bila field opsional absen pada record lama.
        if "print_color" not in alloc:
            alloc["print_color"] = None
        if "screen_kind" not in alloc:
            alloc["screen_kind"] = None
        op = users.find_active_operator_by_email(
            db, str(alloc.get("operator_email") or "")
        )
        name = (op or {}).get("display_name") or ""
        alloc["operator_display_name"] = name or None
        alloc_qty = float(alloc.get("qty") or 0)
        others = (
            max(allocated - alloc_qty, 0.0)
            if alloc.get("status") == schedules.STATUS_ACTIVE
            else allocated
        )
        # Floor = max(cetak allocation ini, screen allocation ini); ceiling = sisa kapasitas PO.
        screened = screen_confirmed_qty(db, alloc)
        revise_min = max(printed, screened)
        revise_max = max(qty_target - others, revise_min)
        can_revise = (
            alloc.get("status") == schedules.STATUS_ACTIVE and revise_max >= revise_min
        )
        alloc["qty_revise_min"] = revise_min
        alloc["qty_revise_max"] = revise_max
        alloc["can_revise"] = can_revise
    from app.repositories import product_master_repository as products

    item_code = str(line.get("item_code") or "")
    from app.services.display_enrichment import (
        outlet_name_from_po,
        paint_color_from_line,
    )

    resolved_outlet = (
        outlet_name
        if outlet_name is not None
        else outlet_name_from_po(po_doc)
    )
    return {
        "accurate_po_id": accurate_po_id,
        "po_no": po_doc.get("po_no"),
        "accurate_line_id": accurate_line_id,
        "item_code": item_code,
        "item_name": line.get("item_name"),
        "process_kind": line.get("process_kind"),
        "outlet_name": resolved_outlet or "",
        "paint_color": paint_color_from_line(line),
        "ship_dates": list(ship_dates) if ship_dates is not None else [],
        "qty_target": qty_target,
        "qty_allocated": allocated,
        "qty_unallocated": max(qty_target - allocated, 0),
        "qty_printed": printed_qty_for_line(
            db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
        ),
        # Ukuran screen dari Master Produk (bukan DEMO Kecil/Besar).
        "screen_size": products.screen_size_for_item(db, item_code),
        "allocations": allocations,
        "gates": gates.get_gates(db, accurate_po_id),
        "scheduling_unlocked": gates.both_gates_open(db, accurate_po_id),
    }


def create_allocations(
    db: Database,
    user: dict[str, Any],
    *,
    po_doc: dict[str, Any],
    accurate_line_id: str,
    allocations: list[dict[str, Any]],
    idempotency_key: str,
    schedule_date: str | None = None,
) -> dict[str, Any]:
    """Konfirmasi jadwal: bagi Qty item line ke satu/beberapa operator."""
    from app.services.po_acceptance import PoAcceptanceError, require_po_accepted
    from app.services.po_repair_maintenance import WriterFence, po_repair_writer_section

    with po_repair_writer_section(db) as fence:
        try:
            require_po_accepted(po_doc)
        except PoAcceptanceError as exc:
            raise ScheduleGateError(str(exc)) from exc

        return _create_allocations_under_writer_lease(
            db,
            user,
            po_doc=po_doc,
            accurate_line_id=accurate_line_id,
            allocations=allocations,
            idempotency_key=idempotency_key,
            schedule_date=schedule_date,
            writer_fence=fence,
        )


def _create_allocations_under_writer_lease(
    db: Database,
    user: dict[str, Any],
    *,
    po_doc: dict[str, Any],
    accurate_line_id: str,
    allocations: list[dict[str, Any]],
    idempotency_key: str,
    schedule_date: str | None = None,
    writer_fence: WriterFence,
) -> dict[str, Any]:
    writer_fence.assert_valid(db)
    require_action(db, user, task_type="penjadwalan", action="confirm")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key wajib diisi.")

    cached = schedules.recall_command(db, idempotency_key.strip())
    if cached is not None:
        return {**cached, "duplicate": True}

    accurate_po_id = str(po_doc.get("accurate_id") or "")
    if not gates.both_gates_open(db, accurate_po_id):
        current = gates.get_gates(db, accurate_po_id)
        raise ScheduleGateError(
            "Penjadwalan terkunci: Gate Design "
            f"{current['design']['state']} / Gate Payment {current['payment']['state']}."
        )

    line = _line_from_po(po_doc, accurate_line_id)
    if str(line.get("process_kind")) != "Cetak":
        raise ScheduleQuantityError("Item Non-Cetak tidak melewati penjadwalan.")

    qty_target = float(line.get("qty") or 0)
    requested = sum(float(a.get("qty") or 0) for a in allocations)
    if requested <= 0:
        raise ScheduleQuantityError("Qty alokasi harus lebih besar dari nol.")
    already = schedules.active_allocated_qty(db, accurate_line_id)
    if already + requested > qty_target:
        raise ScheduleQuantityError(
            f"Total alokasi {already + requested:g} melampaui Qty item line {qty_target:g}."
        )

    # Fail-closed Master SEBELUM mutasi alokasi — cegah alokasi tanpa follow-up.
    master.require_task_type_assigned_role(db, "persiapan-cup")
    master.require_task_type_assigned_role(db, "screen")
    master.require_task_type_assigned_role(db, "konfirmasi-sample")

    actor = str(user.get("email_normalized") or "")
    # Tanggal kerja sebagai string kalender YYYY-MM-DD — tanpa geser timezone.
    sched_date = (schedule_date or date.today().isoformat()).strip()
    if len(sched_date) >= 10:
        sched_date = sched_date[:10]
    from app.repositories import settings_user_repository as users
    from app.services.object_auth import object_branch_code
    from app.services.po_repair_maintenance import mutate_under_writer_fence

    pending: list[dict[str, Any]] = []
    for entry in allocations:
        operator = str(entry.get("operator_email") or "").strip()
        qty = float(entry.get("qty") or 0)
        if not operator:
            raise ScheduleQuantityError("Setiap alokasi wajib memiliki operator.")
        if qty <= 0:
            raise ScheduleQuantityError("Qty alokasi harus lebih besar dari nol.")

        print_color_raw = entry.get("print_color")
        print_color: str | None = None
        if print_color_raw is not None and str(print_color_raw).strip():
            print_color = str(print_color_raw).strip()[:100]

        screen_kind_raw = entry.get("screen_kind")
        screen_kind: str | None = None
        if screen_kind_raw is not None and str(screen_kind_raw).strip():
            screen_kind = str(screen_kind_raw).strip()
            if screen_kind not in SCREEN_KIND_VALUES:
                raise ScheduleQuantityError(
                    "Jenis screen alokasi harus Besar atau Kecil "
                    "(terpisah dari ukuran screen Master Produk)."
                )

        master_op = users.find_active_operator_by_email(db, operator)
        if master_op is not None and not master_op.get("display_name"):
            raise ScheduleQuantityError(
                "Operator di Master Pengguna belum memiliki nama tampilan."
            )
        if master_op is not None:
            operator = str(master_op["email"])

        pending.append(
            {
                "operator_email": operator,
                "qty": qty,
                "print_color": print_color,
                "screen_kind": screen_kind,
            }
        )

    # ID di luar txn: next_sequence berbasis count tidak melihat insert uncommitted.
    base_seq = schedules.next_sequence(db, accurate_line_id)
    for i, item in enumerate(pending):
        item["allocation_id"] = build_allocation_id(accurate_line_id, base_seq + i)

    branch_code = object_branch_code(po_doc)
    po_no = str(po_doc.get("po_no") or "")
    item_code = str(line.get("item_code") or "")

    def _write_allocations(session: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in pending:
            out.append(
                schedules.insert_allocation(
                    db,
                    allocation_id=item["allocation_id"],
                    accurate_po_id=accurate_po_id,
                    po_no=po_no,
                    accurate_line_id=accurate_line_id,
                    item_code=item_code,
                    operator_email=item["operator_email"],
                    qty=item["qty"],
                    schedule_date=sched_date,
                    created_by=actor,
                    print_color=item["print_color"],
                    screen_kind=item["screen_kind"],
                    branch_code=branch_code,
                    session=session,
                )
            )
        return out

    created = mutate_under_writer_fence(db, writer_fence, _write_allocations)

    def _write_followups(session: Any) -> list[str]:
        keys = _create_follow_up_tasks(
            db, po_doc, line, created, session=session
        )
        _mark_scheduling_task(
            db, accurate_po_id, accurate_line_id, requested, session=session
        )
        return keys

    follow_ups = mutate_under_writer_fence(db, writer_fence, _write_followups)

    append_audit_event(
        db,
        action="schedule.allocations.create",
        actor_email=actor,
        target_email=None,
        detail={
            "accurate_po_id": accurate_po_id,
            "accurate_line_id": accurate_line_id,
            "allocation_ids": [a["allocation_id"] for a in created],
            "qty_total": requested,
        },
    )

    result = {
        "allocations": created,
        "follow_up_task_keys": follow_ups,
        "duplicate": False,
    }
    schedules.remember_command(db, idempotency_key=idempotency_key.strip(), result=result)
    return result


def _create_follow_up_tasks(
    db: Database,
    po_doc: dict[str, Any],
    line: dict[str, Any],
    created: list[dict[str, Any]],
    *,
    session: Any | None = None,
) -> list[str]:
    """Persiapan Helper (per item line) dan Screen (per alokasi) berjalan paralel.

    Konfirmasi keduanya baru dilengkapi pada Checkpoint D.
    """
    # Fail-closed Master sebelum membuat tugas turunan (tanpa seed/fallback).
    prep_role = master.require_task_type_assigned_role(db, "persiapan-cup")
    screen_role = master.require_task_type_assigned_role(db, "screen")
    sample_role = master.require_task_type_assigned_role(db, "konfirmasi-sample")

    accurate_po_id = str(po_doc.get("accurate_id") or "")
    po_no = str(po_doc.get("po_no") or "")
    line_id = str(line.get("accurate_line_id") or "")
    keys: list[str] = []

    prep = tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role=prep_role,
        ref=TaskRef(
            accurate_po_id=accurate_po_id,
            po_no=po_no,
            accurate_line_id=line_id,
            item_code=str(line.get("item_code") or ""),
            process_kind="Cetak",
        ),
        qty_target=float(line.get("qty") or 0),
        label="Persiapan Cup",
        context={"item_name": line.get("item_name")},
        session=session,
    )
    keys.append(prep["task_key"])

    for alloc in created:
        alloc_ref = TaskRef(
            accurate_po_id=accurate_po_id,
            po_no=po_no,
            accurate_line_id=line_id,
            item_code=str(line.get("item_code") or ""),
            allocation_id=alloc["allocation_id"],
        )
        screen = tasks.upsert_task(
            db,
            task_type="screen",
            scope="po_line_allocation",
            assigned_role=screen_role,
            ref=alloc_ref,
            label="Screen",
            context={
                "operator_email": alloc["operator_email"],
                "qty_alokasi": alloc["qty"],
            },
            session=session,
        )
        keys.append(screen["task_key"])

        sample = tasks.upsert_task(
            db,
            task_type="konfirmasi-sample",
            scope="po_line_allocation",
            assigned_role=sample_role,
            ref=alloc_ref,
            label="Konfirmasi Sampel",
            context={
                "operator_email": alloc["operator_email"],
                "qty_alokasi": alloc["qty"],
                "display_kind": "perlu-sampel",
            },
            session=session,
        )
        keys.append(sample["task_key"])
    return keys


def _mark_scheduling_task(
    db: Database,
    accurate_po_id: str,
    accurate_line_id: str,
    qty: float,
    *,
    session: Any | None = None,
) -> None:
    key = build_task_key(
        "penjadwalan",
        "po_line",
        TaskRef(accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id),
    )
    task = tasks.get_task(db, key, session=session)
    if task is None:
        return
    allocated = schedules.active_allocated_qty(
        db, accurate_line_id, session=session
    )
    target = float(task.get("qty_target") or 0)
    status = (
        tasks.TASK_STATUS_DONE
        if target and allocated >= target
        else tasks.TASK_STATUS_PARTIAL
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": key},
        {"$set": {"qty_confirmed": allocated, "status": status}},
        session=session,
    )


def revise_allocation_qty(
    db: Database,
    user: dict[str, Any],
    *,
    po_doc: dict[str, Any],
    allocation_id: str,
    qty: float,
    reason: str,
) -> dict[str, Any]:
    """Revisi Qty alokasi — boleh meski sudah ada cetak, selama >= Qty cetak & <= kapasitas PO.

    Mutasi alokasi + sync target cetak + kapasitas line berjalan dalam satu
    transaksi replica set agar tidak commit state invalid terhadap print konkuren
    atau revisi alokasi lain pada item yang sama.
    """
    from app.services.po_repair_maintenance import WriterFence, po_repair_writer_section

    with po_repair_writer_section(db) as fence:
        return _revise_allocation_qty_under_writer_lease(
            db,
            user,
            po_doc=po_doc,
            allocation_id=allocation_id,
            qty=qty,
            reason=reason,
            writer_fence=fence,
        )


def _revise_allocation_qty_under_writer_lease(
    db: Database,
    user: dict[str, Any],
    *,
    po_doc: dict[str, Any],
    allocation_id: str,
    qty: float,
    reason: str,
    writer_fence: WriterFence,
) -> dict[str, Any]:
    writer_fence.assert_valid(db)
    require_action(db, user, task_type="penjadwalan", action="edit")
    if qty < 0:
        raise ScheduleQuantityError("Qty alokasi tidak boleh negatif.")
    # Semantik Qty alokasi: bilangan bulat nonnegatif (tolak pecahan).
    if float(qty) != float(int(qty)):
        raise ScheduleQuantityError("Qty alokasi harus bilangan bulat.")
    qty = float(int(qty))
    if not reason.strip():
        raise ValueError("Alasan revisi wajib diisi.")

    alloc = schedules.get_allocation(db, allocation_id)
    if alloc is None:
        raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}")
    if alloc.get("status") != schedules.STATUS_ACTIVE and qty > 0:
        # Qty > 0 pada alokasi released tidak diizinkan lewat jalur revisi biasa.
        if alloc.get("status") == schedules.STATUS_RELEASED:
            raise ScheduleQuantityError("Alokasi tidak aktif tidak dapat direvisi.")

    accurate_po_id = str(alloc["accurate_po_id"])
    line_id = str(alloc["accurate_line_id"])
    expected_qty = float(alloc["qty"])

    printed = printed_qty_for_allocation(
        db,
        accurate_po_id=accurate_po_id,
        accurate_line_id=line_id,
        allocation_id=allocation_id,
    )
    if qty < printed:
        raise AllocationBelowConfirmedPrintError()

    # Screen per-alokasi: jangan biarkan Qty di bawah progres Screen tanpa aturan bisnis pindah ledger.
    from app.services.production_service import print_task_key, screen_confirmed_qty

    screened = screen_confirmed_qty(db, alloc)
    if qty < screened:
        raise AllocationDownstreamConflictError(
            "Qty alokasi tidak boleh lebih kecil dari Qty Screen yang sudah dikonfirmasi "
            f"({screened:g}) untuk alokasi ini."
        )

    line = _line_from_po(po_doc, line_id)
    qty_target = float(line.get("qty") or 0)
    others = schedules.active_allocated_qty(db, line_id) - expected_qty
    if others < 0:
        others = 0.0
    if others + qty > qty_target:
        raise ScheduleQuantityError(
            f"Total alokasi {others + qty:g} melampaui Qty item line {qty_target:g}."
        )

    actor = str(user.get("email_normalized") or "")
    reason_s = reason.strip()
    print_key = print_task_key(alloc)

    def _operation(session: Any) -> dict[str, Any]:
        writer_fence.assert_valid(db, session=session)
        # Serialize kapasitas line antar alokasi (bukan process-local lock).
        schedules.touch_line_guard(db, line_id, session=session)
        live = schedules.get_allocation(db, allocation_id, session=session)
        if live is None:
            raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}")
        if float(live["qty"]) != expected_qty:
            raise AllocationConcurrencyError(
                "Alokasi berubah oleh proses lain — muat ulang lalu coba revisi lagi."
            )

        print_task = tasks.get_task(db, print_key, session=session)
        printed_now = float((print_task or {}).get("qty_confirmed") or 0)
        if qty < printed_now:
            raise AllocationBelowConfirmedPrintError()

        total_others = (
            schedules.active_allocated_qty(db, line_id, session=session) - expected_qty
        )
        if total_others < 0:
            total_others = 0.0
        if total_others + qty > qty_target:
            raise ScheduleQuantityError(
                f"Total alokasi {total_others + qty:g} melampaui Qty item line {qty_target:g}."
            )

        updated = schedules.update_allocation_qty(
            db,
            allocation_id=allocation_id,
            qty=qty,
            actor_email=actor,
            reason=reason_s,
            expected_qty=expected_qty,
            session=session,
        )
        if updated is None:
            raise AllocationConcurrencyError(
                "Alokasi berubah oleh proses lain — muat ulang lalu coba revisi lagi."
            )

        # Sinkronkan target cetak agar print konkuren tidak memakai stale qty_target.
        if print_task is not None:
            synced = tasks.tasks_collection(db).find_one_and_update(
                {
                    "task_key": print_key,
                    "$expr": {
                        "$lte": [{"$ifNull": ["$qty_confirmed", 0]}, qty]
                    },
                },
                {
                    "$set": {
                        "qty_target": qty,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                session=session,
            )
            if synced is None:
                raise AllocationBelowConfirmedPrintError()

        final_total = schedules.active_allocated_qty(db, line_id, session=session)
        if final_total > qty_target:
            raise ScheduleQuantityError(
                f"Total alokasi {final_total:g} melampaui Qty item line {qty_target:g}."
            )
        return updated

    try:
        updated = tasks.run_in_transaction(db, _operation)
    except AllocationConcurrencyError:
        fresh = schedules.get_allocation(db, allocation_id)
        if fresh is None:
            raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}") from None
        printed_now = printed_qty_for_allocation(
            db,
            accurate_po_id=accurate_po_id,
            accurate_line_id=line_id,
            allocation_id=allocation_id,
        )
        if qty < printed_now:
            raise AllocationBelowConfirmedPrintError() from None
        raise

    _mark_scheduling_task(db, accurate_po_id, line_id, qty)
    append_audit_event(
        db,
        action="schedule.allocation.revise",
        actor_email=actor,
        target_email=None,
        detail={
            "allocation_id": allocation_id,
            "accurate_po_id": accurate_po_id,
            "accurate_line_id": line_id,
            "qty_before": expected_qty,
            "qty_after": qty,
            "qty_printed": printed,
            "reason": reason_s,
        },
    )
    # Enrich response untuk konsumen UI.
    updated["qty_printed"] = printed_qty_for_allocation(
        db,
        accurate_po_id=accurate_po_id,
        accurate_line_id=line_id,
        allocation_id=allocation_id,
    )
    return updated


def scan_reschedule(
    db: Database,
    user: dict[str, Any],
    *,
    po_doc: dict[str, Any],
    as_of: str | None = None,
) -> dict[str, Any]:
    """Pergantian hari dengan Qty cetak kurang → satu tugas Penjadwalan Ulang."""
    from app.services.po_repair_maintenance import WriterFence, po_repair_writer_section

    with po_repair_writer_section(db) as fence:
        return _scan_reschedule_under_writer_lease(
            db, user, po_doc=po_doc, as_of=as_of, writer_fence=fence
        )


def _scan_reschedule_under_writer_lease(
    db: Database,
    user: dict[str, Any],
    *,
    po_doc: dict[str, Any],
    as_of: str | None = None,
    writer_fence: WriterFence,
) -> dict[str, Any]:
    from app.services.po_repair_maintenance import mutate_under_writer_fence

    require_action(db, user, task_type="penjadwalan-ulang", action="input")
    today = as_of or date.today().isoformat()
    accurate_po_id = str(po_doc.get("accurate_id") or "")
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    pending: list[dict[str, Any]] = []
    skipped: list[str] = []

    for line in header.get("items") or []:
        if not isinstance(line, dict) or line.get("process_kind") != "Cetak":
            continue
        line_id = str(line.get("accurate_line_id") or "")
        allocations = schedules.list_allocations(
            db, accurate_line_id=line_id, include_released=True
        )
        stale = [a for a in allocations if str(a.get("schedule_date")) < today]
        if not stale:
            skipped.append(f"{line_id}:tidak-ada-jadwal-hari-sebelumnya")
            continue

        scheduled = sum(
            float(a["qty"]) for a in stale if a.get("status") == schedules.STATUS_ACTIVE
        )
        printed = printed_qty_for_line(
            db, accurate_po_id=accurate_po_id, accurate_line_id=line_id
        )
        shortfall = scheduled - printed
        if shortfall <= 0:
            skipped.append(f"{line_id}:tidak-ada-kekurangan")
            continue
        pending.append(
            {
                "line_id": line_id,
                "item_code": str(line.get("item_code") or ""),
                "shortfall": shortfall,
                "scheduled": scheduled,
                "printed": printed,
            }
        )

    def _write(session: Any) -> list[str]:
        created: list[str] = []
        reschedule_role = master.require_task_type_assigned_role(
            db, "penjadwalan-ulang"
        )
        for item in pending:
            task = tasks.upsert_task(
                db,
                task_type="penjadwalan-ulang",
                scope="po_line",
                assigned_role=reschedule_role,
                ref=TaskRef(
                    accurate_po_id=accurate_po_id,
                    po_no=str(po_doc.get("po_no") or ""),
                    accurate_line_id=item["line_id"],
                    item_code=item["item_code"],
                    process_kind="Cetak",
                ),
                qty_target=item["shortfall"],
                label="Penjadwalan Ulang",
                context={
                    "as_of": today,
                    "qty_scheduled": item["scheduled"],
                    "qty_printed": item["printed"],
                    "qty_shortfall": item["shortfall"],
                },
                session=session,
            )
            created.append(task["task_key"])
        return created

    created = (
        mutate_under_writer_fence(db, writer_fence, _write) if pending else []
    )

    if created:
        append_audit_event(
            db,
            action="schedule.reschedule.scan",
            actor_email=str(user.get("email_normalized") or ""),
            target_email=None,
            detail={"accurate_po_id": accurate_po_id, "as_of": today, "tasks": created},
        )
    return {"as_of": today, "created_task_keys": created, "skipped": skipped}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
