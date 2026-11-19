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
    """Alokasi sudah punya hasil cetak terkonfirmasi."""


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
    for alloc in allocations:
        alloc["qty_printed"] = printed_qty_for_allocation(
            db,
            accurate_po_id=accurate_po_id,
            accurate_line_id=accurate_line_id,
            allocation_id=alloc["allocation_id"],
        )
    allocated = schedules.active_allocated_qty(db, accurate_line_id)
    qty_target = float(line.get("qty") or 0)
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
    sched_date = schedule_date or date.today().isoformat()
    created: list[dict[str, Any]] = []
    for entry in allocations:
        operator = str(entry.get("operator_email") or "").strip()
        qty = float(entry.get("qty") or 0)
        if not operator:
            raise ScheduleQuantityError("Setiap alokasi wajib memiliki operator.")
        if qty <= 0:
            raise ScheduleQuantityError("Qty alokasi harus lebih besar dari nol.")
        allocation_id = build_allocation_id(
            accurate_line_id, schedules.next_sequence(db, accurate_line_id)
        )
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
            label="Upload/Konfirmasi Sample",
            context={"operator_email": alloc["operator_email"], "qty_alokasi": alloc["qty"]},
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
    """Qty alokasi tanpa hasil cetak terkonfirmasi boleh diubah; histori disimpan."""
    require_action(db, user, task_type="penjadwalan", action="edit")
    if qty < 0:
        raise ScheduleQuantityError("Qty alokasi tidak boleh negatif.")
    if not reason.strip():
        raise ValueError("Alasan revisi wajib diisi.")

    alloc = schedules.get_allocation(db, allocation_id)
    if alloc is None:
        raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}")

    accurate_po_id = str(alloc["accurate_po_id"])
    line_id = str(alloc["accurate_line_id"])
    printed = printed_qty_for_allocation(
        db,
        accurate_po_id=accurate_po_id,
        accurate_line_id=line_id,
        allocation_id=allocation_id,
    )
    if printed > 0:
        raise AllocationLockedError(
            f"Alokasi {allocation_id} sudah memiliki Qty cetak terkonfirmasi {printed:g}."
        )

    line = _line_from_po(po_doc, line_id)
    qty_target = float(line.get("qty") or 0)
    others = schedules.active_allocated_qty(db, line_id) - float(alloc["qty"])
    if others + qty > qty_target:
        raise ScheduleQuantityError(
            f"Total alokasi {others + qty:g} melampaui Qty item line {qty_target:g}."
        )

    actor = str(user.get("email_normalized") or "")
    updated = schedules.update_allocation_qty(
        db, allocation_id=allocation_id, qty=qty, actor_email=actor, reason=reason.strip()
    )
    _mark_scheduling_task(db, accurate_po_id, line_id, qty)
    append_audit_event(
        db,
        action="schedule.allocation.revise",
        actor_email=actor,
        target_email=None,
        detail={
            "allocation_id": allocation_id,
            "qty_before": alloc["qty"],
            "qty_after": qty,
            "reason": reason.strip(),
        },
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
