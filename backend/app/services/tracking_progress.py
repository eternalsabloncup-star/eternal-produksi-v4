"""Hitung progres Live Tracking — pisahkan milestone / produksi / pengiriman.

Tidak mengklaim % produksi dari jumlah milestone.
Tidak double-count SJ parsial / revisi / soft-delete (adapter sudah filter).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.tracking_milestones import (
    CUSTOMER_MILESTONES,
    MilestoneSpec,
    MilestoneState,
)
from app.services.tracking_workflow_adapter import (
    as_utc,
    gate_state,
    task_aggregate,
)


def po_has_print_items(po_doc: dict[str, Any]) -> bool:
    header = po_doc.get("header") or {}
    items = header.get("items") or []
    for item in items:
        if item.get("process_kind") == "Cetak":
            return True
    return False


def qty_po_total(po_doc: dict[str, Any]) -> float:
    header = po_doc.get("header") or {}
    items = header.get("items") or []
    return float(sum(float(i.get("qty") or 0) for i in items))


def _ts(value: Any) -> str | None:
    dt = as_utc(value)
    return dt.isoformat() if dt else None


def _state_from_task(
    agg: dict[str, Any],
    *,
    source_available: bool,
) -> tuple[MilestoneState, str, str | None]:
    if not source_available:
        return "pending", "Sumber tugas operasional belum tersedia.", None
    if not agg.get("present"):
        return "pending", "Belum ada bukti tugas untuk tahap ini.", None
    status = agg.get("status")
    ts = _ts(agg.get("updated_at"))
    if status == "selesai":
        return "completed", "Tugas terkait selesai berdasarkan ledger konfirmasi.", ts
    if status == "sebagian":
        return (
            "in_progress",
            f"Progres Qty {agg.get('qty_confirmed')}/{agg.get('qty_target')}.",
            ts,
        )
    return "pending", "Tugas ada tetapi belum dikonfirmasi.", ts


def evaluate_milestones(
    po_doc: dict[str, Any],
    workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    has_print = po_has_print_items(po_doc)
    avail = workflow.get("availability") or {}
    tasks_by_type: dict[str, list] = workflow.get("tasks_by_type") or {}
    gates = workflow.get("gates")
    results: list[dict[str, Any]] = []

    for spec in CUSTOMER_MILESTONES:
        state, detail, ts, gaps = _evaluate_one(
            spec,
            po_doc=po_doc,
            has_print=has_print,
            avail=avail,
            tasks_by_type=tasks_by_type,
            gates=gates,
            workflow=workflow,
        )
        results.append(
            {
                "id": spec.id,
                "seq": spec.seq,
                "label": spec.label,
                "state": state,
                "detail": detail,
                "timestamp": ts,
                "sources": list(spec.backend_sources),
                "gaps": gaps,
                "relevant": state != "not_applicable",
            }
        )
    return results


def _evaluate_one(
    spec: MilestoneSpec,
    *,
    po_doc: dict[str, Any],
    has_print: bool,
    avail: dict[str, Any],
    tasks_by_type: dict[str, list],
    gates: dict[str, Any] | None,
    workflow: dict[str, Any],
) -> tuple[MilestoneState, str, str | None, list[str]]:
    gaps: list[str] = []
    if spec.gap_note:
        gaps.append(spec.gap_note)

    if spec.print_only and not has_print:
        return (
            "not_applicable",
            "PO tanpa item Cetak — tahap desain/cetak tidak berlaku.",
            None,
            gaps,
        )

    mid = spec.id

    if mid == "order_received":
        created = po_doc.get("created_at") or po_doc.get("source_synced_at")
        return (
            "completed",
            "PO tercatat di Mongo V4 dari Accurate.",
            _ts(created),
            gaps,
        )

    if mid == "order_verified":
        # Tidak ada kontrak verifikasi — jangan mengarang Completed.
        return (
            "pending",
            "Menunggu event verifikasi pesanan yang sah.",
            None,
            gaps,
        )

    if mid == "design_prep":
        subs = workflow.get("design_submissions") or []
        design_tasks = task_aggregate(tasks_by_type.get("design") or [])
        if subs:
            latest = subs[-1]
            return (
                "completed"
                if latest.get("confirmed_at")
                else "in_progress",
                "Submission desain tercatat."
                if latest.get("confirmed_at")
                else "Desain sedang disiapkan.",
                _ts(latest.get("confirmed_at") or latest.get("created_at")),
                gaps,
            )
        if avail.get("tasks") or avail.get("design"):
            st, detail, ts = _state_from_task(design_tasks, source_available=True)
            return st, detail, ts, gaps
        return "pending", "Belum ada bukti persiapan desain.", None, gaps

    if mid == "design_approved":
        g = gate_state(gates, "design")
        approvals = workflow.get("design_approvals") or []
        if g == "open" or approvals:
            ts = None
            if approvals:
                ts = _ts(approvals[-1].get("approved_at") or approvals[-1].get("created_at"))
            elif gates and isinstance(gates.get("design"), dict):
                ts = _ts(gates["design"].get("updated_at"))
            return "completed", "Gate desain terbuka / approval customer tercatat.", ts, gaps
        if g == "review":
            return "attention_required", "Desain dalam review.", None, gaps
        if not avail.get("gates") and not avail.get("design"):
            return "pending", "Sumber gate/approval desain belum tersedia.", None, gaps
        return "pending", "Menunggu persetujuan desain.", None, gaps

    if mid == "material_prep":
        agg = task_aggregate(tasks_by_type.get("persiapan-cup") or [])
        st, detail, ts = _state_from_task(agg, source_available=bool(avail.get("tasks")))
        return st, detail, ts, gaps

    if mid == "production_scheduled":
        schedules = workflow.get("schedules") or []
        agg = task_aggregate(tasks_by_type.get("penjadwalan") or [])
        if schedules:
            return (
                "completed",
                "Jadwal produksi tercatat.",
                _ts(schedules[-1].get("updated_at") or schedules[-1].get("created_at")),
                gaps,
            )
        st, detail, ts = _state_from_task(
            agg, source_available=bool(avail.get("tasks") or avail.get("schedules"))
        )
        return st, detail, ts, gaps

    if mid == "print_prep":
        agg = task_aggregate(tasks_by_type.get("screen") or [])
        st, detail, ts = _state_from_task(agg, source_available=bool(avail.get("tasks")))
        return st, detail, ts, gaps

    if mid == "printing":
        agg = task_aggregate(tasks_by_type.get("cetak") or [])
        st, detail, ts = _state_from_task(agg, source_available=bool(avail.get("tasks")))
        return st, detail, ts, gaps

    if mid == "quality_check":
        agg = task_aggregate(tasks_by_type.get("qc-packing") or [])
        st, detail, ts = _state_from_task(agg, source_available=bool(avail.get("tasks")))
        return st, detail, ts, gaps

    if mid == "production_complete":
        cetak = task_aggregate(tasks_by_type.get("cetak") or [])
        qc = task_aggregate(tasks_by_type.get("qc-packing") or [])
        if not avail.get("tasks"):
            return "pending", "Ledger produksi belum tersedia.", None, gaps
        # Non-cetak: selesaikan berdasarkan qc/persiapan bila ada.
        if not has_print:
            if qc.get("present") and qc.get("status") == "selesai":
                return "completed", "Produksi non-cetak selesai (QC/packing).", _ts(qc.get("updated_at")), gaps
            st, detail, ts = _state_from_task(qc, source_available=True)
            return st, detail, ts, gaps
        if cetak.get("present") and cetak.get("status") == "selesai":
            if qc.get("present") and qc.get("status") != "selesai" and qc.get("status") == "sebagian":
                return "in_progress", "Cetak selesai; QC masih berjalan.", _ts(qc.get("updated_at")), gaps
            if not qc.get("present") or qc.get("status") == "selesai":
                return "completed", "Qty cetak terkonfirmasi selesai.", _ts(cetak.get("updated_at")), gaps
        if cetak.get("present") and cetak.get("status") == "sebagian":
            return "in_progress", "Produksi cetak masih berjalan.", _ts(cetak.get("updated_at")), gaps
        return "pending", "Belum ada bukti penyelesaian produksi.", None, gaps

    if mid == "shipping_prep":
        notes = workflow.get("delivery_notes") or []
        if notes:
            return (
                "completed",
                f"{len(notes)} surat jalan tercatat.",
                _ts(notes[-1].get("updated_at") or notes[-1].get("created_at")),
                gaps,
            )
        if not avail.get("delivery_notes"):
            return "pending", "Modul surat jalan belum tersedia.", None, gaps
        return "pending", "Belum ada surat jalan untuk PO ini.", None, gaps

    if mid == "ready_to_ship":
        notes = workflow.get("delivery_notes") or []
        ready = [n for n in notes if n.get("ready_to_ship") or n.get("status") in ("siap_kirim", "ready")]
        if ready:
            return "completed", "Barang ditandai siap dikirim.", _ts(ready[-1].get("updated_at")), gaps
        if not avail.get("delivery_notes"):
            return "pending", "Event siap kirim belum tersedia.", None, gaps
        if notes:
            return "in_progress", "SJ ada; belum ditandai siap kirim.", None, gaps
        return "pending", "Menunggu penanda siap kirim.", None, gaps

    if mid == "in_transit":
        agg = task_aggregate(tasks_by_type.get("pengiriman") or [])
        notes = workflow.get("delivery_notes") or []
        transit = [n for n in notes if n.get("status") in ("in_transit", "dalam_pengiriman", "shipped")]
        if transit:
            return "in_progress", "Pengiriman sedang berjalan.", _ts(transit[-1].get("updated_at")), gaps
        st, detail, ts = _state_from_task(
            agg, source_available=bool(avail.get("tasks") or avail.get("delivery_notes"))
        )
        return st, detail, ts, gaps

    if mid == "delivered":
        evidence = workflow.get("delivery_evidence") or []
        notes = workflow.get("delivery_notes") or []
        delivered_notes = [n for n in notes if n.get("status") in ("delivered", "diterima")]
        if evidence or delivered_notes:
            src = evidence[-1] if evidence else delivered_notes[-1]
            return (
                "completed",
                "Bukti penerimaan / status diterima tercatat.",
                _ts(src.get("confirmed_at") or src.get("updated_at") or src.get("created_at")),
                gaps,
            )
        if not avail.get("delivery_evidence") and not avail.get("delivery_notes"):
            return "pending", "Bukti pengiriman belum tersedia di backend.", None, gaps
        return "pending", "Belum ada konfirmasi barang diterima.", None, gaps

    if mid == "order_complete":
        # Gate ketat: butuh qty delivered >= qty PO. Tanpa ledger pengiriman → jangan selesai.
        qty_total = qty_po_total(po_doc)
        shipped = _qty_shipped_validated(workflow)
        if shipped is None:
            return (
                "pending",
                "Gate final menunggu Qty terkirim tervalidasi.",
                None,
                gaps,
            )
        if qty_total > 0 and shipped + 1e-9 >= qty_total:
            return (
                "completed",
                f"Qty terkirim {shipped} memenuhi Qty PO {qty_total}.",
                _ts(datetime.now(timezone.utc)),
                gaps,
            )
        if shipped > 0:
            return (
                "in_progress",
                f"Qty terkirim {shipped} dari {qty_total} — belum lengkap.",
                None,
                gaps,
            )
        return "pending", "Kewajiban pengiriman belum terpenuhi.", None, gaps

    return "unknown", "Pemetaan milestone belum dikenali.", None, gaps


def _qty_shipped_validated(workflow: dict[str, Any]) -> float | None:
    """Jumlah Qty terkirim sah tanpa double-count revisi/soft-delete.

    Prefer field ``qty_shipped_net`` pada delivery note bila ada; else jumlah
    ``qty`` pada note aktif. Return None jika sumber tidak ada.
    """
    avail = workflow.get("availability") or {}
    if not avail.get("delivery_notes"):
        return None
    notes = workflow.get("delivery_notes") or []
    if not notes:
        return 0.0
    total = 0.0
    seen_keys: set[str] = set()
    for note in notes:
        # Revisi: pakai identity stabil; skip superseded.
        if note.get("superseded_by") or note.get("is_superseded"):
            continue
        key = str(
            note.get("accurate_delivery_note_id")
            or note.get("sj_no")
            or note.get("id")
            or id(note)
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if "qty_shipped_net" in note:
            total += float(note.get("qty_shipped_net") or 0)
        else:
            total += float(note.get("qty") or note.get("qty_total") or 0)
    return total


def compute_progress(
    milestones: list[dict[str, Any]],
    po_doc: dict[str, Any],
    workflow: dict[str, Any],
) -> dict[str, Any]:
    relevant = [m for m in milestones if m.get("relevant")]
    completed = [m for m in relevant if m.get("state") == "completed"]
    milestone_ratio = (
        (len(completed) / len(relevant)) if relevant else 0.0
    )

    qty_total = qty_po_total(po_doc)
    # Qty produksi tervalidasi dari task cetak (Cetak) atau qc (Non-Cetak).
    tasks_by_type = workflow.get("tasks_by_type") or {}
    has_print = po_has_print_items(po_doc)
    if has_print:
        prod_agg = task_aggregate(tasks_by_type.get("cetak") or [])
    else:
        prod_agg = task_aggregate(tasks_by_type.get("qc-packing") or [])
    qty_produced = float(prod_agg.get("qty_confirmed") or 0) if prod_agg.get("present") else None
    qc_agg = task_aggregate(tasks_by_type.get("qc-packing") or [])
    qty_qc = float(qc_agg.get("qty_confirmed") or 0) if qc_agg.get("present") else None

    qty_shipped = _qty_shipped_validated(workflow)
    qty_remaining = None
    if qty_shipped is not None and qty_total is not None:
        qty_remaining = max(0.0, qty_total - qty_shipped)

    production_ratio = None
    if qty_produced is not None and qty_total > 0:
        production_ratio = min(1.0, qty_produced / qty_total)

    shipping_ratio = None
    if qty_shipped is not None and qty_total > 0:
        shipping_ratio = min(1.0, qty_shipped / qty_total)

    active = None
    for m in milestones:
        if m["state"] in ("in_progress", "attention_required"):
            active = m
            break
    if active is None:
        for m in milestones:
            if m["state"] == "pending" and m.get("relevant"):
                active = m
                break

    order_complete = any(
        m["id"] == "order_complete" and m["state"] == "completed" for m in milestones
    )

    return {
        "milestone": {
            "completed": len(completed),
            "relevant": len(relevant),
            "ratio": round(milestone_ratio, 4),
            "label": f"{len(completed)}/{len(relevant)}",
        },
        "production": {
            "qty_po": qty_total,
            "qty_produced": qty_produced,
            "qty_qc_passed": qty_qc,
            "ratio": round(production_ratio, 4) if production_ratio is not None else None,
            "source": "operational_tasks" if qty_produced is not None else None,
        },
        "shipping": {
            "qty_shipped": qty_shipped,
            "qty_remaining": qty_remaining,
            "ratio": round(shipping_ratio, 4) if shipping_ratio is not None else None,
            "source": "delivery_notes" if qty_shipped is not None else None,
        },
        "active_milestone": active,
        "order_complete": order_complete,
        # Jangan klaim selesai bila gate final belum terpenuhi.
        "completion_blocked_reason": None
        if order_complete
        else "Gate final belum terpenuhi atau Qty belum lengkap.",
    }
