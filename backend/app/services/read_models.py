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
# Identitas PO/line tidak lengkap — jangan anggap qty Ready = 0.
READY_INCOMPLETE = "Data tidak lengkap"

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


def _non_print_lines(po_doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        i
        for i in (_header(po_doc).get("items") or [])
        if isinstance(i, dict) and i.get("process_kind") == "Non-Cetak"
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


def compute_ready_gudang(
    *,
    qty_printed: float,
    qty_delivered_confirmed: float,
) -> dict[str, Any]:
    """Ready Gudang = Qty Sudah Cetak − Qty Konfirmasi Terkirim (Owner).

    Tidak memakai qty SJ/Loading/jadwal. Nilai negatif = ketidaksesuaian jelas
    (terkirim > cetak), bukan di-nol-kan diam-diam.
    """
    printed = float(qty_printed)
    shipped = float(qty_delivered_confirmed)
    ready = printed - shipped
    return {
        "qty_ready_warehouse": ready,
        "ready_mismatch": ready < 0,
        "qty_over_shipped": max(-ready, 0.0),
    }


def buffer_stock_rows(
    db: Database,
    *,
    branch_ids: list[str] | set[str] | None = None,
    branch_scope: bool = False,
) -> dict[str, Any]:
    """Satu baris per item line pada PO beratribut Buffer.

    ``branch_scope=True`` membatasi ke cabang aktor (fail-closed daftar kosong).
    Tidak menulis stok/ledger — read model monitoring saja.
    """
    rows: list[dict[str, Any]] = []
    totals = {
        "ordered": 0.0,
        "printed": 0.0,
        "remaining": 0.0,
        "shipped": 0.0,
        "ready": 0.0,
    }
    ready_incomplete_rows = 0
    ready_mismatch_rows = 0

    query: dict[str, Any] = {"origin": "accurate"}
    if branch_scope:
        from app.repositories.po_repository import branch_scope_query

        scope = branch_scope_query(branch_ids)
        query = {"$and": [query, scope]}

    for po_doc in po_collection(db).find(query, {"_id": 0}):
        if not is_buffer_po(po_doc):
            continue
        header = _header(po_doc)
        accurate_po_id = str(po_doc.get("accurate_id") or "")
        for item in header.get("items") or []:
            if not isinstance(item, dict):
                continue
            line_id = str(item.get("accurate_line_id") or "").strip()
            ordered = float(item.get("qty") or 0)
            identity_ok = bool(accurate_po_id) and bool(line_id)
            printed = (
                production.printed_total_for_line(
                    db, accurate_po_id=accurate_po_id, accurate_line_id=line_id
                )
                if identity_ok and item.get("process_kind") == "Cetak"
                else 0.0
            )
            shipped = (
                _delivered_qty_for_line(db, accurate_po_id, line_id)
                if identity_ok
                else 0.0
            )
            if identity_ok:
                ready_info = compute_ready_gudang(
                    qty_printed=printed, qty_delivered_confirmed=shipped
                )
                ready_value: float | str = ready_info["qty_ready_warehouse"]
                ready_mismatch = bool(ready_info["ready_mismatch"])
                if ready_mismatch:
                    ready_mismatch_rows += 1
                totals["ready"] += float(ready_value)
            else:
                ready_value = READY_INCOMPLETE
                ready_mismatch = False
                ready_incomplete_rows += 1

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
                    # Owner: Ready = cetak terkonfirmasi − terkirim terkonfirmasi.
                    "qty_ready_warehouse": ready_value,
                    "ready_mismatch": ready_mismatch,
                    "qty_shipped": shipped if identity_ok else READY_INCOMPLETE,
                    "buffer_status": NOT_CONFIGURED,
                    "sales_pic": header.get("seller") or NOT_CONFIGURED,
                    "process_kind": item.get("process_kind"),
                }
            )
            totals["ordered"] += ordered
            totals["printed"] += printed
            totals["remaining"] += max(ordered - printed, 0)
            if identity_ok:
                totals["shipped"] += shipped

    kpi_ready: float | str = totals["ready"]
    if ready_incomplete_rows and not rows:
        kpi_ready = READY_INCOMPLETE

    return {
        "items": rows,
        "kpi": {
            "total_buffer_ordered": totals["ordered"],
            "qty_printed": totals["printed"],
            "qty_remaining": totals["remaining"],
            "qty_ready_warehouse": kpi_ready,
            "qty_shipped": totals["shipped"],
            "ready_mismatch_rows": ready_mismatch_rows,
            "ready_incomplete_rows": ready_incomplete_rows,
        },
        "not_configured": [
            "buffer_status",
            "outlet_warehouse",
        ],
        "ready_formula": "qty_printed_confirmed - qty_delivery_success_confirmed",
        "source": "mongodb",
    }


def _delivered_qty_for_line(
    db: Database, accurate_po_id: str, accurate_line_id: str
) -> float:
    """Qty terkirim = qty item pada SJ yang punya ≥1 event sukses.

    - Hanya status berhasil (bukan gagal / loading / SJ dibuat saja).
    - Satu SJ dihitung sekali meski ada retry/konfirmasi berulang.
    - SJ yang sudah ``superseded_by`` tidak dihitung (hindari dobel revisi).
    - Identitas line wajib cocok ``accurate_line_id`` (tidak ditebak nama barang).
    """
    if not accurate_po_id or not accurate_line_id:
        return 0.0
    total = 0.0
    seen_sj: set[str] = set()
    for note in deliveries.list_delivery_notes(db, accurate_po_id=accurate_po_id):
        if note.get("superseded_by"):
            continue
        sj_id = str(note.get("accurate_sj_id") or "")
        if not sj_id or sj_id in seen_sj:
            continue
        events = deliveries.list_delivery_events(db, sj_id)
        if not any(e.get("status") == deliveries.DELIVERY_SUCCESS for e in events):
            continue
        seen_sj.add(sj_id)
        for item in note.get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("accurate_line_id") or "") != accurate_line_id:
                continue
            if not deliveries.item_belongs_to_po(
                item, accurate_po_id=accurate_po_id, note=note
            ):
                continue
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

# Bucket virtual untuk PO yang seluruh tahap applicable sudah selesai.
FLOW_SELESAI_ID = "flow-selesai"
FLOW_SELESAI_LABEL = "Selesai"

# Filter agregat UI — bukan workflow node; PO accepted unik (tanpa duplikasi paralel).
FLOW_ALL_ID = "flow-all"
FLOW_ALL_LABEL = "Semua"

# Membership frontier — terpisah dari status node (production_flow_state).
_TRACK_DESIGN = ("flow-design", "flow-approval-design")
_TRACK_PAYMENT = ("flow-payment", "flow-approval-payment")
_PARALLEL_AFTER_SCHEDULE = ("flow-screener", "flow-persiapan")
_SERIAL_AFTER_PREP = (
    "flow-operator",
    "flow-konfirmasi-sample",
    "flow-cetak",
    "flow-packing",
    "flow-surat-jalan",
    "flow-loading",
    "flow-pengiriman",
)

# Batch cursor by-stage — tanpa memotong populasi accepted.
PRODUCTION_FLOW_BATCH_SIZE = 100

MONITOR_LABEL_AKTIF = "Aktif"
MONITOR_LABEL_DALAM_PROSES = "Dalam Proses"
MONITOR_LABEL_SELESAI = "Selesai"


def _node(node_id: str, label: str, status: str, detail: str) -> dict[str, Any]:
    return {"id": node_id, "label": label, "status": status, "detail": detail}


def _status_passed(status: str) -> bool:
    return status in (NODE_DONE, NODE_NOT_APPLICABLE)


def _order_masuk_left_entry(statuses: dict[str, str]) -> bool:
    """Order Masuk ditinggalkan begitu Design atau Payment mulai (running/done)."""
    design = statuses.get("flow-design", NODE_WAITING)
    payment = statuses.get("flow-payment", NODE_WAITING)
    if design == NODE_NOT_APPLICABLE:
        return payment in (NODE_RUNNING, NODE_DONE) or statuses.get(
            "flow-approval-payment", NODE_WAITING
        ) in (NODE_RUNNING, NODE_DONE)
    return design in (NODE_RUNNING, NODE_DONE) or payment in (NODE_RUNNING, NODE_DONE)


def _track_frontier(track: tuple[str, ...], statuses: dict[str, str]) -> list[str]:
    """Tahap aktif pada satu track: running, atau menunggu frontier (bukan semua menunggu)."""
    for node_id in track:
        st = statuses.get(node_id, NODE_WAITING)
        if st == NODE_NOT_APPLICABLE or st == NODE_DONE:
            continue
        if st == NODE_RUNNING:
            return [node_id]
        if st == NODE_WAITING:
            return [node_id]
    return []


def monitoring_label(*, node_id: str, node_status: str) -> str:
    """Label tabel monitoring — bukan status SoT node."""
    if node_id == FLOW_SELESAI_ID:
        return MONITOR_LABEL_SELESAI
    if node_status == NODE_RUNNING:
        return MONITOR_LABEL_DALAM_PROSES
    return MONITOR_LABEL_AKTIF


def _line_print_active_stages(
    db: Database,
    *,
    accurate_po_id: str,
    line: dict[str, Any],
    line_allocs: list[dict[str, Any]],
) -> set[str]:
    """Membership per item Cetak setelah penjadwalan — union di level PO."""
    active: set[str] = set()
    line_id = str(line.get("accurate_line_id") or "")
    line_qty = float(line.get("qty") or 0)
    active_allocs = [
        a
        for a in line_allocs
        if str(a.get("status") or "") != schedules.STATUS_RELEASED
    ]
    allocated_qty = sum(float(a.get("qty") or 0) for a in active_allocs)
    # Penjadwalan selesai hanya jika seluruh qty line dialokasikan (aktif).
    if not active_allocs or (line_qty > 0 and allocated_qty + 1e-9 < line_qty):
        active.add("flow-penjadwalan")
        return active

    screened = [a for a in active_allocs if production.screen_confirmed_qty(db, a) > 0]
    all_screened = bool(active_allocs) and len(screened) == len(active_allocs)

    prep = tasks.get_task(db, production.prep_task_key(accurate_po_id, line_id)) or {}
    prep_qty = float(prep.get("qty_confirmed") or 0)
    prep_done = bool(line_qty) and prep_qty >= line_qty

    # Screener || Persiapan
    if not all_screened:
        active.add("flow-screener")
    if not prep_done:
        active.add("flow-persiapan")
    if not all_screened or not prep_done:
        return active

    # Sampel ditolak (terbaru) tanpa approved → kembali ke Operator.
    for a in active_allocs:
        latest = samples.latest_sample(db, a["allocation_id"])
        if (
            latest
            and latest.get("status") == samples.STATUS_REJECTED
            and samples.approved_sample(db, a["allocation_id"]) is None
        ):
            active.add("flow-operator")
            return active

    uploaded = sum(
        1 for a in active_allocs if samples.latest_sample(db, a["allocation_id"])
    )
    approved = sum(
        1 for a in active_allocs if samples.approved_sample(db, a["allocation_id"])
    )
    all_uploaded = uploaded == len(active_allocs)
    all_approved = approved == len(active_allocs)
    if not all_uploaded:
        active.add("flow-operator")
        return active
    if not all_approved:
        active.add("flow-konfirmasi-sample")
        return active

    printed = production.printed_total_for_line(
        db, accurate_po_id=accurate_po_id, accurate_line_id=line_id
    )
    qc = tasks.get_task(db, production.qc_task_key(accurate_po_id, line_id)) or {}
    packed = float(qc.get("qty_confirmed") or 0)

    # Cetak || Packing paralel (partial print + partial packing).
    if not line_qty or printed + 1e-9 < line_qty:
        active.add("flow-cetak")
    if printed > 0 and packed + 1e-9 < printed:
        active.add("flow-packing")
    return active


def active_stage_membership_from_facts(
    db: Database,
    *,
    statuses: dict[str, str],
    has_print: bool,
    accurate_po_id: str,
    print_lines: list[dict[str, Any]],
    allocations_by_line: dict[str, list[dict[str, Any]]],
    non_print_lines: list[dict[str, Any]] | None = None,
    po_doc: dict[str, Any] | None = None,
) -> list[str]:
    """Membership tahap aktif dari fakta operasional (+ progres per line)."""
    if not statuses:
        return []

    # T5: saat strict ON, Selesai HANYA dari fulfillment checker — tanpa
    # fallback all(_status_passed) yang bisa mengabaikan evidence/qty.
    from app.config import get_settings

    strict_on = bool(get_settings().strict_po_completion_enabled)
    if po_doc is not None and strict_on:
        from app.services.po_completion import evaluate_po_fulfillment

        if evaluate_po_fulfillment(db, po_doc).get("complete"):
            return [FLOW_SELESAI_ID]
        # Incomplete: lanjutkan membership operasional (bukan Selesai).
    elif all(
        _status_passed(statuses.get(nid, NODE_WAITING)) for nid, _ in PRODUCTION_FLOW_NODES
    ):
        return [FLOW_SELESAI_ID]

    if not _order_masuk_left_entry(statuses):
        return ["flow-order-masuk"]

    active: set[str] = set()
    active.update(_track_frontier(_TRACK_DESIGN, statuses))
    active.update(_track_frontier(_TRACK_PAYMENT, statuses))

    design_done = _status_passed(statuses.get("flow-approval-design", NODE_WAITING))
    payment_done = _status_passed(statuses.get("flow-approval-payment", NODE_WAITING))
    if not (design_done and payment_done):
        return _stable_stage_list(active)

    # Non-Cetak packing (eligible task exists + remaining).
    for line in non_print_lines or []:
        line_id = str(line.get("accurate_line_id") or "")
        if not line_id:
            continue
        qc = tasks.get_task(db, production.qc_task_key(accurate_po_id, line_id))
        if qc is None:
            continue
        target = float(qc.get("qty_target") or line.get("qty") or 0)
        confirmed = float(qc.get("qty_confirmed") or 0)
        if target <= 0 or confirmed + 1e-9 < target:
            active.add("flow-packing")

    if not has_print:
        if "flow-packing" in active:
            return _stable_stage_list(active)
        for nid in ("flow-surat-jalan", "flow-loading", "flow-pengiriman"):
            st = statuses.get(nid, NODE_WAITING)
            if st in (NODE_RUNNING, NODE_WAITING):
                active.add(nid)
                break
        return _stable_stage_list(active)

    # Penjadwalan + union tahap produksi per line Cetak
    for line in print_lines:
        line_id = str(line.get("accurate_line_id") or "")
        line_allocs = allocations_by_line.get(line_id) or []
        active.update(
            _line_print_active_stages(
                db,
                accurate_po_id=accurate_po_id,
                line=line,
                line_allocs=line_allocs,
            )
        )

    # Fulfillment PO-level setelah seluruh line cetak selesai packing
    # (+ packing Non-Cetak bila masih sisa).
    print_work_pending = bool(
        active
        & {
            "flow-penjadwalan",
            "flow-screener",
            "flow-persiapan",
            "flow-operator",
            "flow-konfirmasi-sample",
            "flow-cetak",
            "flow-packing",
        }
    )
    if not print_work_pending:
        for nid in ("flow-surat-jalan", "flow-loading", "flow-pengiriman"):
            st = statuses.get(nid, NODE_WAITING)
            if st in (NODE_RUNNING, NODE_WAITING):
                active.add(nid)
                break

    return _stable_stage_list(active)


def _stable_stage_list(active: set[str]) -> list[str]:
    order = [nid for nid, _ in PRODUCTION_FLOW_NODES] + [FLOW_SELESAI_ID]
    return [nid for nid in order if nid in active]


def active_stage_membership(flow: dict[str, Any]) -> list[str]:
    """Fallback dari node status saja (tes/unit tanpa fakta line).

    Prefer ``active_stage_membership_from_facts`` di jalur production_flow_state.
    """
    nodes = flow.get("nodes") or []
    statuses = {
        str(n.get("id")): str(n.get("status") or NODE_WAITING)
        for n in nodes
        if isinstance(n, dict) and n.get("id")
    }
    if not statuses:
        return []
    if all(_status_passed(statuses.get(nid, NODE_WAITING)) for nid, _ in PRODUCTION_FLOW_NODES):
        return [FLOW_SELESAI_ID]
    if not _order_masuk_left_entry(statuses):
        return ["flow-order-masuk"]
    active: set[str] = set()
    active.update(_track_frontier(_TRACK_DESIGN, statuses))
    active.update(_track_frontier(_TRACK_PAYMENT, statuses))
    design_done = _status_passed(statuses.get("flow-approval-design", NODE_WAITING))
    payment_done = _status_passed(statuses.get("flow-approval-payment", NODE_WAITING))
    if design_done and payment_done:
        sched = statuses.get("flow-penjadwalan", NODE_WAITING)
        if sched in (NODE_WAITING, NODE_RUNNING):
            active.add("flow-penjadwalan")
        elif sched == NODE_DONE:
            for nid in _PARALLEL_AFTER_SCHEDULE:
                st = statuses.get(nid, NODE_WAITING)
                if st in (NODE_RUNNING, NODE_WAITING):
                    active.add(nid)
            if not (active & set(_PARALLEL_AFTER_SCHEDULE)):
                for nid in _SERIAL_AFTER_PREP:
                    st = statuses.get(nid, NODE_WAITING)
                    if st in (NODE_RUNNING, NODE_WAITING):
                        active.add(nid)
                        # Multi-stage: lanjut jika RUNNING berikutnya juga aktif
                        # (cetak berjalan + packing menunggu beda line → keduanya)
                        if st == NODE_WAITING:
                            break
        elif sched == NODE_NOT_APPLICABLE:
            for nid in ("flow-surat-jalan", "flow-loading", "flow-pengiriman"):
                st = statuses.get(nid, NODE_WAITING)
                if st in (NODE_RUNNING, NODE_WAITING):
                    active.add(nid)
                    break
    return _stable_stage_list(active)


def accepted_po_query(
    *,
    branch_ids: list[str] | set[str] | None = None,
    branch_scope: bool = False,
    q: str | None = None,
    urgent: bool | None = None,
) -> dict[str, Any]:
    """Filter Mongo: origin accurate + accepted (+ branch/q/urgent seperti Inbox)."""
    from app.repositories.po_repository import branch_scope_query
    from app.services.po_acceptance import ACCEPTANCE_ACCEPTED

    query: dict[str, Any] = {
        "origin": "accurate",
        "operational.acceptance.status": ACCEPTANCE_ACCEPTED,
    }
    text = (q or "").strip()
    if text:
        query["$or"] = [
            {"po_no": {"$regex": text, "$options": "i"}},
            {"header.customer": {"$regex": text, "$options": "i"}},
            {"accurate_id": {"$regex": text, "$options": "i"}},
            {"header.items.item_name": {"$regex": text, "$options": "i"}},
            {"header.items.item_code": {"$regex": text, "$options": "i"}},
        ]
    if urgent is True:
        query["header.urgent"] = True
    elif urgent is False:
        query["header.urgent"] = {"$ne": True}
    if branch_scope:
        scope = branch_scope_query(branch_ids)
        if "$or" in query:
            query = {"$and": [query, scope]}
        else:
            query.update(scope)
    return query


def production_flow_state(db: Database, po_doc: dict[str, Any]) -> dict[str, Any]:
    """Status 15 node dari fakta operasional, bukan posisi visual."""
    accurate_po_id = str(po_doc.get("accurate_id") or "")
    print_lines = _print_lines(po_doc)
    non_print_lines = _non_print_lines(po_doc)
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
    allocations_by_line: dict[str, list[dict[str, Any]]] = {}
    for line in print_lines:
        line_id = str(line.get("accurate_line_id") or "")
        line_allocs = schedules.list_allocations(
            db,
            accurate_line_id=line_id,
            include_released=True,
        )
        allocations_by_line[line_id] = line_allocs
        allocations.extend(line_allocs)

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
    nc_packed = 0.0
    nc_target = 0.0
    nc_packing_tasks = 0
    for line in non_print_lines:
        line_id = str(line.get("accurate_line_id") or "")
        qc = tasks.get_task(db, production.qc_task_key(accurate_po_id, line_id))
        if qc is None:
            continue
        nc_packing_tasks += 1
        nc_packed += float(qc.get("qty_confirmed") or 0)
        nc_target += float(qc.get("qty_target") or line.get("qty") or 0)
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
            (
                NODE_NOT_APPLICABLE
                if not has_print and nc_packing_tasks == 0
                else NODE_DONE
                if (
                    (not has_print or (printed_total and packed_total >= printed_total))
                    and (
                        nc_packing_tasks == 0
                        or (nc_target > 0 and nc_packed + 1e-9 >= nc_target)
                    )
                )
                else NODE_RUNNING
                if (packed_total or nc_packed)
                else NODE_WAITING
            ),
            (
                na
                if not has_print and nc_packing_tasks == 0
                else (
                    f"Packing Cetak {packed_total:g}/{printed_total:g}"
                    + (
                        f"; Non-Cetak {nc_packed:g}/{nc_target:g}"
                        if nc_packing_tasks
                        else ""
                    )
                    if has_print
                    else f"Packing Non-Cetak {nc_packed:g} dari {nc_target:g}."
                )
            ),
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
    statuses = {
        str(n.get("id")): str(n.get("status") or NODE_WAITING) for n in nodes
    }
    active_stages = active_stage_membership_from_facts(
        db,
        statuses=statuses,
        has_print=has_print,
        accurate_po_id=accurate_po_id,
        print_lines=print_lines,
        allocations_by_line=allocations_by_line,
        non_print_lines=non_print_lines,
        po_doc=po_doc,
    )
    flow = {
        "accurate_po_id": accurate_po_id,
        "po_no": po_doc.get("po_no"),
        "has_print_items": has_print,
        "nodes": nodes,
        "parallel_pairs": [
            ["flow-design", "flow-payment"],
            ["flow-screener", "flow-persiapan"],
        ],
        "active_stages": active_stages,
        "source": "mongodb",
    }
    return flow


def node_label(node_id: str) -> str:
    if node_id == FLOW_ALL_ID:
        return FLOW_ALL_LABEL
    if node_id == FLOW_SELESAI_ID:
        return FLOW_SELESAI_LABEL
    for nid, label in PRODUCTION_FLOW_NODES:
        if nid == node_id:
            return label
    return node_id


def production_flow_stage_ids() -> list[str]:
    """Urutan card UI: Semua + 15 node + Selesai."""
    return (
        [FLOW_ALL_ID]
        + [nid for nid, _ in PRODUCTION_FLOW_NODES]
        + [FLOW_SELESAI_ID]
    )


def production_flow_stage_nodes() -> list[dict[str, str]]:
    return (
        [{"id": FLOW_ALL_ID, "label": FLOW_ALL_LABEL}]
        + [{"id": nid, "label": label} for nid, label in PRODUCTION_FLOW_NODES]
        + [{"id": FLOW_SELESAI_ID, "label": FLOW_SELESAI_LABEL}]
    )


def empty_stage_counts() -> dict[str, int]:
    return {nid: 0 for nid in production_flow_stage_ids()}


def list_production_flow_stage_counts(
    db: Database,
    *,
    q: str | None = None,
    urgent: bool | None = None,
    accurate_po_id: str | None = None,
    branch_ids: list[str] | set[str] | None = None,
    branch_scope: bool = False,
) -> dict[str, Any]:
    """Hitung PO unik per tahap aktif — satu scan accepted (bukan N× by-stage).

    - ``flow-all``: setiap PO accepted unik +1 (tanpa duplikasi paralel).
    - node workflow / ``flow-selesai``: +1 bila ``node_id`` ∈ ``active_stages``.
    Filter ``q`` / ``urgent`` / branch sama dengan by-stage.
    """
    query = accepted_po_query(
        branch_ids=branch_ids,
        branch_scope=branch_scope,
        q=q,
        urgent=urgent,
    )
    po_filter = str(accurate_po_id or "").strip()
    if po_filter:
        query = {"$and": [query, {"accurate_id": po_filter}]}

    counts = empty_stage_counts()
    known = set(counts)
    scanned = 0
    cursor = po_collection(db).find(query, {"_id": 0}).sort(
        [("updated_at", -1), ("accurate_id", -1)]
    )
    for doc in cursor:
        scanned += 1
        flow = production_flow_state(db, doc)
        membership = list(flow.get("active_stages") or [])
        counts[FLOW_ALL_ID] += 1
        seen_stage: set[str] = set()
        for nid in membership:
            if nid not in known or nid == FLOW_ALL_ID:
                continue
            if nid in seen_stage:
                continue
            seen_stage.add(nid)
            counts[nid] += 1

    return {
        "counts": counts,
        "nodes": production_flow_stage_nodes(),
        "scanned": scanned,
        "accepted_only": True,
        "source": "mongodb",
        "supportedFilters": ["q", "urgent"],
    }


def list_production_flow_by_stage(
    db: Database,
    *,
    node_id: str,
    page: int = 1,
    page_size: int = 50,
    q: str | None = None,
    urgent: bool | None = None,
    accurate_po_id: str | None = None,
    branch_ids: list[str] | set[str] | None = None,
    branch_scope: bool = False,
    serialize_po: Any = None,
) -> dict[str, Any]:
    """PO accepted aktif pada ``node_id`` — pagination penuh tanpa scan-cap.

    ``flow-all`` = seluruh PO accepted unik (filter agregat, bukan membership).
    Iterasi cursor deterministik atas seluruh accepted (+filter) dalam branch scope.
    Membership dihitung server-side; frontend tidak memfilter populasi.
    """
    valid_ids = (
        {nid for nid, _ in PRODUCTION_FLOW_NODES}
        | {FLOW_SELESAI_ID, FLOW_ALL_ID}
    )
    if node_id not in valid_ids:
        raise ValueError(f"node_id tidak dikenal: {node_id}")

    query = accepted_po_query(
        branch_ids=branch_ids,
        branch_scope=branch_scope,
        q=q,
        urgent=urgent,
    )
    po_filter = str(accurate_po_id or "").strip()
    if po_filter:
        query = {"$and": [query, {"accurate_id": po_filter}]}
    coll = po_collection(db)
    # Sort deterministik: updated_at desc, accurate_id desc (tie-break).
    cursor = coll.find(query, {"_id": 0}).sort(
        [("updated_at", -1), ("accurate_id", -1)]
    )

    matched: list[dict[str, Any]] = []
    scanned = 0
    for doc in cursor:
        scanned += 1
        flow = production_flow_state(db, doc)
        membership = list(flow.get("active_stages") or [])
        if node_id != FLOW_ALL_ID and node_id not in membership:
            continue
        node = next(
            (n for n in (flow.get("nodes") or []) if n.get("id") == node_id),
            None,
        )
        if node_id == FLOW_ALL_ID:
            stage_status = NODE_DONE if FLOW_SELESAI_ID in membership else NODE_WAITING
            stage_detail = (
                "PO diterima — semua tahap aktif."
                if membership
                else "PO diterima."
            )
            stage_label = FLOW_ALL_LABEL
            mon_label = (
                MONITOR_LABEL_SELESAI
                if FLOW_SELESAI_ID in membership
                else MONITOR_LABEL_AKTIF
            )
        elif node_id == FLOW_SELESAI_ID:
            stage_status = NODE_DONE
            stage_detail = "Seluruh tahap applicable selesai."
            stage_label = FLOW_SELESAI_LABEL
            mon_label = monitoring_label(node_id=node_id, node_status=stage_status)
        else:
            stage_status = str((node or {}).get("status") or NODE_WAITING)
            stage_detail = str((node or {}).get("detail") or "")
            stage_label = str((node or {}).get("label") or node_label(node_id))
            mon_label = monitoring_label(node_id=node_id, node_status=stage_status)

        po_payload = serialize_po(doc, db) if callable(serialize_po) else {
            "accurate_id": doc.get("accurate_id"),
            "po_no": doc.get("po_no"),
            "header": doc.get("header") if isinstance(doc.get("header"), dict) else {},
            "acceptance": {
                "status": "accepted",
            },
        }
        matched.append(
            {
                "po": po_payload,
                "accurate_po_id": str(doc.get("accurate_id") or ""),
                "po_no": doc.get("po_no"),
                "node_id": node_id,
                "stage_label": stage_label,
                "stage_status": stage_status,
                "stage_detail": stage_detail,
                "monitoring_label": mon_label,
                "active_stages": membership,
                "has_print_items": bool(flow.get("has_print_items")),
                "acceptance": po_payload.get("acceptance")
                if isinstance(po_payload, dict)
                else {"status": "accepted"},
            }
        )

    total = len(matched)
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    start = (page - 1) * page_size
    items = matched[start : start + page_size]
    return {
        "node_id": node_id,
        "stage_label": node_label(node_id),
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "scanned": scanned,
        "scan_capped": False,
        "batch_size": PRODUCTION_FLOW_BATCH_SIZE,
        "source": "mongodb",
        "accepted_only": True,
        "supportedFilters": ["q", "urgent"],
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
