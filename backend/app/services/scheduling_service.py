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


def line_schedule_state(
    db: Database, po_doc: dict[str, Any], accurate_line_id: str
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
    return {
        "accurate_po_id": accurate_po_id,
        "po_no": po_doc.get("po_no"),
        "accurate_line_id": accurate_line_id,
        "item_code": item_code,
        "item_name": line.get("item_name"),
        "process_kind": line.get("process_kind"),
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
    from app.services.po_repair_maintenance import refuse_if_po_repair_maintenance

    refuse_if_po_repair_maintenance(db)
    try:
        require_po_accepted(po_doc)
    except PoAcceptanceError as exc:
        raise ScheduleGateError(str(exc)) from exc

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
    created: list[dict[str, Any]] = []
    from app.repositories import settings_user_repository as users

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

        # Bila email ada di Master sebagai operator: wajib nama.
        # Email bebas (Jadwal Produksi lama / uji) tetap diterima tanpa invent ID.
        master_op = users.find_active_operator_by_email(db, operator)
        if master_op is not None and not master_op.get("display_name"):
            raise ScheduleQuantityError(
                "Operator di Master Pengguna belum memiliki nama tampilan."
            )
        if master_op is not None:
            operator = str(master_op["email"])

        allocation_id = build_allocation_id(
            accurate_line_id, schedules.next_sequence(db, accurate_line_id)
        )
        from app.services.object_auth import object_branch_code

        branch_code = object_branch_code(po_doc)
        created.append(
            schedules.insert_allocation(
                db,
                allocation_id=allocation_id,
                accurate_po_id=accurate_po_id,
                po_no=str(po_doc.get("po_no") or ""),
                accurate_line_id=accurate_line_id,
                item_code=str(line.get("item_code") or ""),
                operator_email=operator,
                qty=qty,
                schedule_date=sched_date,
                created_by=actor,
                print_color=print_color,
                screen_kind=screen_kind,
                branch_code=branch_code,
            )
        )

    follow_ups = _create_follow_up_tasks(db, po_doc, line, created)
    _mark_scheduling_task(db, accurate_po_id, accurate_line_id, requested)

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
            context={"operator_email": alloc["operator_email"], "qty_alokasi": alloc["qty"]},
        )
        keys.append(screen["task_key"])

        # Operator butuh tugas sendiri sebagai titik unggah sampel; prasyarat
        # (screen + kuota Helper) tetap divalidasi saat unggah.
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
        )
        keys.append(sample["task_key"])
    return keys


def _mark_scheduling_task(
    db: Database, accurate_po_id: str, accurate_line_id: str, qty: float
) -> None:
    key = build_task_key(
        "penjadwalan",
        "po_line",
        TaskRef(accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id),
    )
    task = tasks.get_task(db, key)
    if task is None:
        return
    allocated = schedules.active_allocated_qty(db, accurate_line_id)
    target = float(task.get("qty_target") or 0)
    status = (
        tasks.TASK_STATUS_DONE
        if target and allocated >= target
        else tasks.TASK_STATUS_PARTIAL
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": key},
        {"$set": {"qty_confirmed": allocated, "status": status}},
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
    from app.services.po_repair_maintenance import refuse_if_po_repair_maintenance

    refuse_if_po_repair_maintenance(db)
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
    from app.services.po_repair_maintenance import refuse_if_po_repair_maintenance

    refuse_if_po_repair_maintenance(db)
    require_action(db, user, task_type="penjadwalan-ulang", action="input")
    today = as_of or date.today().isoformat()
    accurate_po_id = str(po_doc.get("accurate_id") or "")
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    created: list[str] = []
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

        reschedule_role = master.require_task_type_assigned_role(db, "penjadwalan-ulang")
        task = tasks.upsert_task(
            db,
            task_type="penjadwalan-ulang",
            scope="po_line",
            assigned_role=reschedule_role,
            ref=TaskRef(
                accurate_po_id=accurate_po_id,
                po_no=str(po_doc.get("po_no") or ""),
                accurate_line_id=line_id,
                item_code=str(line.get("item_code") or ""),
                process_kind="Cetak",
            ),
            qty_target=shortfall,
            label="Penjadwalan Ulang",
            context={
                "as_of": today,
                "qty_scheduled": scheduled,
                "qty_printed": printed,
                "qty_shortfall": shortfall,
            },
        )
        created.append(task["task_key"])

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
