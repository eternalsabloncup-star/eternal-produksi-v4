"""Read model Checkpoint F: Buffer Stock, Alur Produksi, Dashboard, Laporan.

Semua angka dibaca dari ledger dan state yang sudah ada (Checkpoint A–E).
Nilai yang rumusnya belum diputuskan Owner dilaporkan sebagai
``NOT_CONFIGURED``, bukan ditebak.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.domain.operational import TaskRef, build_task_key
from app.repositories import delivery_repository as deliveries
from app.repositories import design_repository as designs
from app.repositories import gate_repository as gates
from app.repositories import sample_repository as samples
from app.repositories import schedule_repository as schedules
from app.repositories import task_repository as tasks
from app.repositories.po_repository import po_collection
from app.services import production_service as production

NOT_CONFIGURED = "Belum dikonfigurasi"

NODE_DONE = "selesai"
NODE_RUNNING = "berjalan"
NODE_WAITING = "menunggu"
NODE_NOT_APPLICABLE = "tidak_berlaku"


def _header(po_doc: dict[str, Any]) -> dict[str, Any]:
    header = po_doc.get("header")
    return header if isinstance(header, dict) else {}


def _print_lines(po_doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        i
        for i in (_header(po_doc).get("items") or [])
        if isinstance(i, dict) and i.get("process_kind") == "Cetak"
    ]


def is_buffer_po(po_doc: dict[str, Any]) -> bool:
    """Atribut Buffer dari Accurate (charField5 / searchCharField5)."""
    label = str(_header(po_doc).get("buffer_label") or "").strip().lower()
    if not label:
        return False
    normalized = label.replace(" ", "")
    if normalized.startswith("non"):
        return False
    return "buffer" in normalized


# --- Buffer Stock ----------------------------------------------------------


def buffer_stock_rows(db: Database) -> dict[str, Any]:
    """Satu baris per item line pada PO beratribut Buffer."""
    rows: list[dict[str, Any]] = []
    totals = {"ordered": 0.0, "printed": 0.0, "remaining": 0.0, "shipped": 0.0}

    for po_doc in po_collection(db).find({"origin": "accurate"}, {"_id": 0}):
        if not is_buffer_po(po_doc):
            continue
        header = _header(po_doc)
        accurate_po_id = str(po_doc.get("accurate_id") or "")
        for item in header.get("items") or []:
            if not isinstance(item, dict):
                continue
            line_id = str(item.get("accurate_line_id") or "")
            ordered = float(item.get("qty") or 0)
            printed = (
                production.printed_total_for_line(
                    db, accurate_po_id=accurate_po_id, accurate_line_id=line_id
                )
                if item.get("process_kind") == "Cetak"
                else 0.0
            )
            shipped = _delivered_qty_for_line(db, accurate_po_id, line_id)
            rows.append(
                {
                    "accurate_po_id": accurate_po_id,
                    # Nomor PO Buffer memakai nomor PO Accurate asli.
                    "po_no": po_doc.get("po_no"),
                    "accurate_line_id": line_id,
                    # Outlet/Gudang belum punya sumber terbukti di payload F12.
                    "outlet_warehouse": NOT_CONFIGURED,
                    "item_code": item.get("item_code"),
                    "item_name": item.get("item_name"),
                    "color": item.get("color"),
                    "unit": item.get("unit"),
                    "qty_ordered": ordered,
                    "qty_printed": printed,
                    "qty_remaining": max(ordered - printed, 0),
                    # Rumus saldo/mutasi gudang Buffer belum diputuskan Owner.
                    "qty_ready_warehouse": NOT_CONFIGURED,
                    "qty_shipped": shipped,
                    "buffer_status": NOT_CONFIGURED,
                    "sales_pic": header.get("seller") or NOT_CONFIGURED,
                    "process_kind": item.get("process_kind"),
                }
            )
            totals["ordered"] += ordered
            totals["printed"] += printed
            totals["remaining"] += max(ordered - printed, 0)
            totals["shipped"] += shipped

    return {
        "items": rows,
        "kpi": {
            "total_buffer_ordered": totals["ordered"],
            "qty_printed": totals["printed"],
            "qty_remaining": totals["remaining"],
            "qty_ready_warehouse": NOT_CONFIGURED,
            "qty_shipped": totals["shipped"],
        },
        "not_configured": [
            "qty_ready_warehouse",
            "buffer_status",
            "outlet_warehouse",
        ],
        "source": "mongodb",
    }


def _delivered_qty_for_line(
    db: Database, accurate_po_id: str, accurate_line_id: str
) -> float:
    """Qty terkirim = item SJ pada pengiriman yang berstatus berhasil."""
    total = 0.0
    for note in deliveries.list_delivery_notes(db, accurate_po_id=accurate_po_id):
        events = deliveries.list_delivery_events(db, note["accurate_sj_id"])
        if not any(e["status"] == deliveries.DELIVERY_SUCCESS for e in events):
            continue
        for item in note.get("items") or []:
            if str(item.get("accurate_line_id")) == accurate_line_id:
                total += float(item.get("qty") or 0)
    return total


# --- Alur Produksi 15 node -------------------------------------------------

PRODUCTION_FLOW_NODES: tuple[tuple[str, str], ...] = (
    ("flow-order-masuk", "Order Masuk"),
    ("flow-design", "Design"),
    ("flow-approval-design", "Approval Design"),
    ("flow-payment", "Payment"),
    ("flow-approval-payment", "Approval Payment"),
    ("flow-penjadwalan", "Penjadwalan"),
    ("flow-screener", "Screener"),
    ("flow-persiapan", "Persiapan"),
    ("flow-operator", "Operator"),
    ("flow-konfirmasi-sample", "Konfirmasi Sample"),
    ("flow-cetak", "Cetak"),
    ("flow-packing", "Packing"),
    ("flow-surat-jalan", "Surat Jalan"),
    ("flow-loading", "Loading"),
    ("flow-pengiriman", "Pengiriman"),
)


def _node(node_id: str, label: str, status: str, detail: str) -> dict[str, Any]:
    return {"id": node_id, "label": label, "status": status, "detail": detail}


def production_flow_state(db: Database, po_doc: dict[str, Any]) -> dict[str, Any]:
    """Status 15 node dari fakta operasional, bukan posisi visual."""
    accurate_po_id = str(po_doc.get("accurate_id") or "")
    print_lines = _print_lines(po_doc)
    has_print = bool(print_lines)
    gate = gates.get_gates(db, accurate_po_id)

    latest_design = designs.latest_submission(db, accurate_po_id)
    approval = (
        designs.get_approval(
            db,
            accurate_po_id=accurate_po_id,
            design_version=int(latest_design["version"]),
        )
        if latest_design
        else None
    )

    allocations: list[dict[str, Any]] = []
    for line in print_lines:
        allocations.extend(
            schedules.list_allocations(
                db,
                accurate_line_id=str(line.get("accurate_line_id")),
                include_released=True,
            )
        )

    screen_done = sum(
        1 for a in allocations if production.screen_confirmed_qty(db, a) > 0
    )
    prep_total = 0.0
    printed_total = 0.0
    packed_total = 0.0
    for line in print_lines:
        line_id = str(line.get("accurate_line_id"))
        prep = tasks.get_task(db, production.prep_task_key(accurate_po_id, line_id)) or {}
        prep_total += float(prep.get("qty_confirmed") or 0)
        printed_total += production.printed_total_for_line(
            db, accurate_po_id=accurate_po_id, accurate_line_id=line_id
        )
        qc = tasks.get_task(db, production.qc_task_key(accurate_po_id, line_id)) or {}
        packed_total += float(qc.get("qty_confirmed") or 0)

    samples_approved = sum(
        1 for a in allocations if samples.approved_sample(db, a["allocation_id"])
    )
    samples_uploaded = sum(
        1 for a in allocations if samples.latest_sample(db, a["allocation_id"])
    )

    notes = deliveries.list_delivery_notes(db, accurate_po_id=accurate_po_id)
    loaded = [n for n in notes if deliveries.get_loading_event(db, n["accurate_sj_id"])]
    delivered = [
        n
        for n in notes
        if any(
            e["status"] == deliveries.DELIVERY_SUCCESS
            for e in deliveries.list_delivery_events(db, n["accurate_sj_id"])
        )
    ]

    qty_target = sum(float(i.get("qty") or 0) for i in print_lines)
    na = "Item Non-Cetak tidak melewati tahap ini."

    nodes = [
        _node("flow-order-masuk", "Order Masuk", NODE_DONE, f"PO {po_doc.get('po_no')}"),
        _node(
            "flow-design",
            "Design",
            NODE_NOT_APPLICABLE
            if not has_print
            else NODE_DONE
            if latest_design and latest_design.get("confirmed_at")
            else NODE_RUNNING
            if latest_design
            else NODE_WAITING,
            na
            if not has_print
            else f"Versi desain: {latest_design['version']}"
            if latest_design
            else "Belum ada file desain.",
        ),
        _node(
            "flow-approval-design",
            "Approval Design",
            NODE_DONE
            if gate["design"]["state"] == gates.GATE_OPEN
            else NODE_WAITING,
            gate["design"].get("reason") or "",
        ),
        _node(
            "flow-payment",
            "Payment",
            NODE_DONE
            if gate["payment"]["state"] == gates.GATE_OPEN
            else NODE_RUNNING
            if gate["payment"]["state"] == gates.GATE_REVIEW
            else NODE_WAITING,
            gate["payment"].get("reason") or "",
        ),
        _node(
            "flow-approval-payment",
            "Approval Payment",
            NODE_DONE
            if gate["payment"]["state"] == gates.GATE_OPEN
            else NODE_WAITING,
            gate["payment"].get("reason") or "",
        ),
        _node(
            "flow-penjadwalan",
            "Penjadwalan",
            NODE_NOT_APPLICABLE
            if not has_print
            else NODE_DONE
            if allocations
            else NODE_WAITING,
            na if not has_print else f"{len(allocations)} alokasi operator.",
        ),
        _node(
            "flow-screener",
            "Screener",
            NODE_NOT_APPLICABLE
            if not has_print
            else NODE_DONE
            if allocations and screen_done == len(allocations)
            else NODE_RUNNING
            if screen_done
            else NODE_WAITING,
            na if not has_print else f"{screen_done}/{len(allocations)} alokasi di-Screen.",
        ),
        _node(
            "flow-persiapan",
            "Persiapan",
            NODE_NOT_APPLICABLE
            if not has_print
            else NODE_DONE
            if qty_target and prep_total >= qty_target
            else NODE_RUNNING
            if prep_total
            else NODE_WAITING,
            na if not has_print else f"Disiapkan {prep_total:g} dari {qty_target:g}.",
        ),
        _node(
            "flow-operator",
            "Operator",
            NODE_NOT_APPLICABLE
            if not has_print
            else NODE_DONE
            if allocations and samples_uploaded == len(allocations)
            else NODE_RUNNING
            if samples_uploaded
            else NODE_WAITING,
            na
            if not has_print
            else f"{samples_uploaded}/{len(allocations)} operator mengunggah sampel.",
        ),
        _node(
            "flow-konfirmasi-sample",
            "Konfirmasi Sample",
            NODE_NOT_APPLICABLE
            if not has_print
            else NODE_DONE
            if allocations and samples_approved == len(allocations)
            else NODE_RUNNING
            if samples_approved
            else NODE_WAITING,
            na if not has_print else f"{samples_approved}/{len(allocations)} sampel disetujui.",
        ),
        _node(
            "flow-cetak",
            "Cetak",
            NODE_NOT_APPLICABLE
            if not has_print
            else NODE_DONE
            if qty_target and printed_total >= qty_target
            else NODE_RUNNING
            if printed_total
            else NODE_WAITING,
            na if not has_print else f"Qty cetak {printed_total:g} dari {qty_target:g}.",
        ),
        _node(
            "flow-packing",
            "Packing",
            NODE_NOT_APPLICABLE
            if not has_print
            else NODE_DONE
            if printed_total and packed_total >= printed_total
            else NODE_RUNNING
            if packed_total
            else NODE_WAITING,
            na if not has_print else f"QC & Packing {packed_total:g} dari {printed_total:g}.",
        ),
        _node(
            "flow-surat-jalan",
            "Surat Jalan",
            NODE_DONE if notes else NODE_WAITING,
            f"{len(notes)} Surat Jalan Accurate." if notes else "Belum ada SJ Accurate.",
        ),
        _node(
            "flow-loading",
            "Loading",
            NODE_DONE
            if notes and len(loaded) == len(notes)
            else NODE_RUNNING
            if loaded
            else NODE_WAITING,
            f"{len(loaded)}/{len(notes)} SJ sudah Loading." if notes else "Menunggu SJ.",
        ),
        _node(
            "flow-pengiriman",
            "Pengiriman",
            NODE_DONE
            if notes and len(delivered) == len(notes)
            else NODE_RUNNING
            if delivered
            else NODE_WAITING,
            f"{len(delivered)}/{len(notes)} SJ terkirim." if notes else "Menunggu SJ.",
        ),
    ]
    return {
        "accurate_po_id": accurate_po_id,
        "po_no": po_doc.get("po_no"),
        "has_print_items": has_print,
        "nodes": nodes,
        "parallel_pairs": [
            ["flow-design", "flow-payment"],
            ["flow-screener", "flow-persiapan"],
        ],
        "source": "mongodb",
    }


# --- Dashboard -------------------------------------------------------------


def dashboard_metrics(db: Database) -> dict[str, Any]:
    """KPI operasional tanpa double count dan tanpa mencampur satuan."""
    po_docs = list(po_collection(db).find({"origin": "accurate"}, {"_id": 0}))
    po_total = len(po_docs)
    waiting_design = 0
    waiting_payment = 0
    schedulable_lines = 0
    printed_by_unit: dict[str, float] = {}
    packed_by_unit: dict[str, float] = {}

    for po_doc in po_docs:
        accurate_po_id = str(po_doc.get("accurate_id") or "")
        gate = gates.get_gates(db, accurate_po_id)
        if gate["design"]["state"] != gates.GATE_OPEN:
            waiting_design += 1
        if gate["payment"]["state"] != gates.GATE_OPEN:
            waiting_payment += 1
        both_open = gates.both_gates_open(db, accurate_po_id)
        for line in _print_lines(po_doc):
            line_id = str(line.get("accurate_line_id"))
            unit = str(line.get("unit") or "-")
            if both_open:
                schedulable_lines += 1
            printed = production.printed_total_for_line(
                db, accurate_po_id=accurate_po_id, accurate_line_id=line_id
            )
            if printed:
                printed_by_unit[unit] = printed_by_unit.get(unit, 0) + printed
            qc = tasks.get_task(db, production.qc_task_key(accurate_po_id, line_id)) or {}
            packed = float(qc.get("qty_confirmed") or 0)
            if packed:
                packed_by_unit[unit] = packed_by_unit.get(unit, 0) + packed

    open_tasks = tasks.tasks_collection(db).count_documents(
        {"status": {"$ne": tasks.TASK_STATUS_DONE}}
    )
    notes = deliveries.list_delivery_notes(db)
    loaded = 0
    delivered_ok = 0
    delivered_failed = 0
    for note in notes:
        if deliveries.get_loading_event(db, note["accurate_sj_id"]):
            loaded += 1
        events = deliveries.list_delivery_events(db, note["accurate_sj_id"])
        if any(e["status"] == deliveries.DELIVERY_SUCCESS for e in events):
            delivered_ok += 1
        elif any(e["status"] == deliveries.DELIVERY_FAILED for e in events):
            delivered_failed += 1

    return {
        "po_total": po_total,
        "po_waiting_design_gate": waiting_design,
        "po_waiting_payment_gate": waiting_payment,
        "lines_ready_to_schedule": schedulable_lines,
        "tasks_open": open_tasks,
        "qty_printed_by_unit": printed_by_unit,
        "qty_packed_by_unit": packed_by_unit,
        "sj_total": len(notes),
        "sj_loaded": loaded,
        "sj_delivered_success": delivered_ok,
        "sj_delivered_failed": delivered_failed,
        "limitations": [
            "Filter cabang belum tersedia: sumber cabang belum terbukti di payload Accurate.",
            "Qty dilaporkan per satuan; satuan berbeda tidak dijumlahkan.",
        ],
        "source": "mongodb",
    }


# --- Laporan ---------------------------------------------------------------

REPORT_KINDS = (
    "po",
    "allocations",
    "preparation",
    "screen",
    "printing",
    "qc-packing",
    "delivery-notes",
    "deliveries",
    "tasks",
)


def _confirmation_rows(
    db: Database,
    task_type: str,
    *,
    date_from: str | None,
    date_to: str | None,
    actor: str | None,
    accurate_po_id: str | None,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"task_type": task_type}
    if actor:
        query["actor_email"] = actor
    if accurate_po_id:
        query["ref.accurate_po_id"] = str(accurate_po_id)
    rows = []
    for row in (
        tasks.confirmations_collection(db)
        .find(query, {"_id": 0})
        .sort("created_at", 1)
    ):
        created = row.get("created_at")
        iso = created.isoformat() if hasattr(created, "isoformat") else str(created)
        if date_from and iso < date_from:
            continue
        if date_to and iso > f"{date_to}\uffff":
            continue
        rows.append({**row, "created_at": iso})
    return rows


def report(
    db: Database,
    kind: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    actor: str | None = None,
    accurate_po_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Laporan berbasis ledger dan event nyata — tanpa laba/HPP."""
    if kind not in REPORT_KINDS:
        raise ValueError(f"Jenis laporan tidak dikenal: {kind}")

    if kind == "po":
        query: dict[str, Any] = {"origin": "accurate"}
        if accurate_po_id:
            query["accurate_id"] = str(accurate_po_id)
        items = []
        for po_doc in po_collection(db).find(query, {"_id": 0}):
            header = _header(po_doc)
            gate = gates.get_gates(db, str(po_doc.get("accurate_id")))
            items.append(
                {
                    "accurate_id": po_doc.get("accurate_id"),
                    "po_no": po_doc.get("po_no"),
                    "customer": header.get("customer"),
                    "po_date": header.get("po_date"),
                    "eta_ship": header.get("eta_ship"),
                    "seller": header.get("seller"),
                    "buffer_label": header.get("buffer_label"),
                    "item_count": len(header.get("items") or []),
                    "gate_design": gate["design"]["state"],
                    "gate_payment": gate["payment"]["state"],
                }
            )
        return {"kind": kind, "items": items, "total": len(items), "source": "mongodb"}

    if kind == "allocations":
        items = []
        for alloc in schedules.allocations_collection(db).find({}, {"_id": 0}):
            if accurate_po_id and str(alloc["accurate_po_id"]) != str(accurate_po_id):
                continue
            if actor and alloc.get("operator_email") != actor:
                continue
            if status and alloc.get("status") != status:
                continue
            items.append(
                {
                    **alloc,
                    "created_at": alloc["created_at"].isoformat(),
                    "updated_at": alloc["updated_at"].isoformat(),
                    "qty_printed": float(
                        (
                            tasks.get_task(db, production.print_task_key(alloc)) or {}
                        ).get("qty_confirmed")
                        or 0
                    ),
                }
            )
        return {"kind": kind, "items": items, "total": len(items), "source": "mongodb"}

    if kind in {"preparation", "screen", "printing", "qc-packing", "tasks"}:
        task_type = {
            "preparation": "persiapan-cup",
            "screen": "screen",
            "printing": "cetak",
            "qc-packing": "qc-packing",
        }.get(kind)
        if task_type is None:
            rows: list[dict[str, Any]] = []
            for tt in ("persiapan-cup", "screen", "cetak", "qc-packing", "pengiriman"):
                rows.extend(
                    _confirmation_rows(
                        db,
                        tt,
                        date_from=date_from,
                        date_to=date_to,
                        actor=actor,
                        accurate_po_id=accurate_po_id,
                    )
                )
            rows.sort(key=lambda r: r["created_at"])
            return {"kind": kind, "items": rows, "total": len(rows), "source": "mongodb"}
        rows = _confirmation_rows(
            db,
            task_type,
            date_from=date_from,
            date_to=date_to,
            actor=actor,
            accurate_po_id=accurate_po_id,
        )
        return {"kind": kind, "items": rows, "total": len(rows), "source": "mongodb"}

    if kind == "delivery-notes":
        items = []
        for note in deliveries.list_delivery_notes(db, accurate_po_id=accurate_po_id):
            loading = deliveries.get_loading_event(db, note["accurate_sj_id"])
            items.append(
                {
                    "accurate_sj_id": note["accurate_sj_id"],
                    "sj_no": note["sj_no"],
                    "po_no": note["po_no"],
                    "ship_date": note["ship_date"],
                    "item_count": len(note["items"]),
                    "loaded": loading is not None,
                    "loaded_by": (loading or {}).get("actor_email"),
                    "loaded_at": (loading or {}).get("created_at").isoformat()
                    if loading
                    else None,
                }
            )
        return {"kind": kind, "items": items, "total": len(items), "source": "mongodb"}

    # kind == "deliveries"
    items = []
    for note in deliveries.list_delivery_notes(db, accurate_po_id=accurate_po_id):
        for event in deliveries.list_delivery_events(db, note["accurate_sj_id"]):
            if status and event["status"] != status:
                continue
            if actor and event.get("courier_email") != actor:
                continue
            items.append(
                {
                    **event,
                    "sj_no": note["sj_no"],
                    "po_no": note["po_no"],
                    "created_at": event["created_at"].isoformat(),
                    "photo_count": len(event.get("photo_evidence_ids") or []),
                }
            )
    return {"kind": kind, "items": items, "total": len(items), "source": "mongodb"}
