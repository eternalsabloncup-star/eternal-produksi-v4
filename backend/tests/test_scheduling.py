"""Checkpoint C: penjadwalan, alokasi operator, revisi, penjadwalan ulang."""

from __future__ import annotations

import copy

import pytest
from pymongo.database import Database

from app.domain.operational import TaskRef, build_task_key
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import schedule_repository as schedules
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import po_tasks, scheduling_service

PO_ID = "95106"
LINE_A = "106318"
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}
OPERATOR_USER = {
    "email_normalized": "operator@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": ["access.all_branches"],
}
OWNER = {"email_normalized": "owner@eternal.local", "role": "super_admin"}


def _two_print_lines_payload() -> dict:
    """Dua item line dengan kode barang sama — tidak boleh digabung."""
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["detailItem"][1]["charField1"] = "Cetak"
    payload["detailItem"][1]["item"] = dict(payload["detailItem"][0]["item"])
    payload["detailItem"][1]["quantity"] = 400
    return payload


def _seed_po(db: Database, payload=None) -> dict:
    upsert_sales_order(db, normalize_sales_order(payload or SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted
    mark_po_accepted(db)
    return get_by_accurate_id(db, PO_ID)


def _open_gates(db: Database) -> None:
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="uji"
    )
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="uji"
    )


def _confirm_print(db: Database, allocation_id: str, qty: float) -> None:
    """Simulasi Qty cetak terkonfirmasi (alur penuh menyusul di Checkpoint D)."""
    master.seed_master_defaults(db)
    ref = TaskRef(
        accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=allocation_id
    )
    tasks.upsert_task(
        db,
        task_type="cetak",
        scope="po_line_allocation",
        assigned_role="operator",
        ref=ref,
        qty_target=qty + 10_000,
        label="Cetak",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("cetak", "po_line_allocation", ref),
        qty=qty,
        actor_email="operator@eternal.local",
        actor_role="operator",
        idempotency_key=f"print-{allocation_id}-{qty}",
    )


def test_scheduling_rejected_when_gates_incomplete(db: Database) -> None:
    po_doc = _seed_po(db)
    po_tasks.derive_tasks_for_po(db, po_doc)
    with pytest.raises(scheduling_service.ScheduleGateError):
        scheduling_service.create_allocations(
            db,
            HEAD,
            po_doc=po_doc,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": "op1@eternal.local", "qty": 100}],
            idempotency_key="sched-gate-closed-1",
        )
    assert schedules.list_allocations(db, accurate_line_id=LINE_A) == []

    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="uji"
    )
    with pytest.raises(scheduling_service.ScheduleGateError):
        scheduling_service.create_allocations(
            db,
            HEAD,
            po_doc=po_doc,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": "op1@eternal.local", "qty": 100}],
            idempotency_key="sched-gate-payment-1",
        )


def test_allocation_split_between_two_operators(db: Database) -> None:
    po_doc = _seed_po(db)
    po_tasks.derive_tasks_for_po(db, po_doc)
    _open_gates(db)

    result = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[
            {"operator_email": "op1@eternal.local", "qty": 600},
            {"operator_email": "op2@eternal.local", "qty": 400},
        ],
        idempotency_key="sched-split-1",
    )
    assert len(result["allocations"]) == 2
    ids = [a["allocation_id"] for a in result["allocations"]]
    assert len(set(ids)) == 2
    assert schedules.active_allocated_qty(db, LINE_A) == 1000

    # Persiapan Helper per item line + Screen & tugas sampel per alokasi.
    assert f"task:persiapan-cup:po:{PO_ID}:line:{LINE_A}" in result["follow_up_task_keys"]
    screens = tasks.list_tasks(db, task_type="screen")
    assert len(screens) == 2
    assert {s["ref"]["allocation_id"] for s in screens} == set(ids)
    sample_tasks = tasks.list_tasks(db, task_type="konfirmasi-sample")
    assert {s["ref"]["allocation_id"] for s in sample_tasks} == set(ids)
    assert all(s["assigned_role"] == "operator" for s in sample_tasks)


def test_same_item_code_two_lines_stay_separate(db: Database) -> None:
    po_doc = _seed_po(db, _two_print_lines_payload())
    _open_gates(db)
    scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 100}],
        idempotency_key="sched-line-a",
    )
    scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id="106319",
        allocations=[{"operator_email": "op1@eternal.local", "qty": 100}],
        idempotency_key="sched-line-b",
    )
    assert schedules.active_allocated_qty(db, LINE_A) == 100
    assert schedules.active_allocated_qty(db, "106319") == 100
    assert len(schedules.list_allocations(db, accurate_line_id=LINE_A)) == 1


def test_allocation_above_target_rejected(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    with pytest.raises(scheduling_service.ScheduleQuantityError):
        scheduling_service.create_allocations(
            db,
            HEAD,
            po_doc=po_doc,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": "op1@eternal.local", "qty": 1500}],
            idempotency_key="sched-over-1",
        )
    assert schedules.list_allocations(db, accurate_line_id=LINE_A) == []


def test_double_submit_does_not_duplicate_schedule(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    payload = {
        "po_doc": po_doc,
        "accurate_line_id": LINE_A,
        "allocations": [{"operator_email": "op1@eternal.local", "qty": 500}],
        "idempotency_key": "sched-double-click",
    }
    first = scheduling_service.create_allocations(db, HEAD, **payload)
    second = scheduling_service.create_allocations(db, HEAD, **payload)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert schedules.active_allocated_qty(db, LINE_A) == 500
    assert len(schedules.list_allocations(db, accurate_line_id=LINE_A)) == 1


def test_revision_keeps_history_and_frees_qty(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="sched-revise-1",
    )
    alloc_id = created["allocations"][0]["allocation_id"]

    scheduling_service.revise_allocation_qty(
        db,
        HEAD,
        po_doc=po_doc,
        allocation_id=alloc_id,
        qty=600,
        reason="Operator dialihkan ke PO lain",
    )
    history = schedules.allocation_history(db, alloc_id)
    assert [h["action"] for h in history] == ["create", "update_qty"]
    assert history[1]["qty_before"] == 1000
    assert history[1]["qty_after"] == 600
    assert schedules.active_allocated_qty(db, LINE_A) == 600

    # Qty yang dilepas dapat dialokasikan ulang ke operator lain.
    scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op2@eternal.local", "qty": 400}],
        idempotency_key="sched-revise-2",
    )
    assert schedules.active_allocated_qty(db, LINE_A) == 1000


def test_allocation_revise_rejects_below_confirmed_print(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 800}],
        idempotency_key="sched-lock-1",
    )
    alloc_id = created["allocations"][0]["allocation_id"]
    _confirm_print(db, alloc_id, 300)

    with pytest.raises(scheduling_service.AllocationBelowConfirmedPrintError):
        scheduling_service.revise_allocation_qty(
            db,
            HEAD,
            po_doc=po_doc,
            allocation_id=alloc_id,
            qty=100,
            reason="coba ubah di bawah cetak",
        )
    # Riwayat hasil cetak tetap utuh; Qty alokasi tidak berubah.
    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    assert state["qty_printed"] == 300
    assert state["allocations"][0]["qty"] == 800


def test_allocation_revise_allows_at_or_above_confirmed_print(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 800}],
        idempotency_key="sched-revise-print-ok",
    )
    alloc_id = created["allocations"][0]["allocation_id"]
    _confirm_print(db, alloc_id, 300)

    scheduling_service.revise_allocation_qty(
        db,
        HEAD,
        po_doc=po_doc,
        allocation_id=alloc_id,
        qty=300,
        reason="turun ke batas cetak",
    )
    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    assert state["allocations"][0]["qty"] == 300
    assert state["qty_printed"] == 300
    assert state["qty_allocated"] == 300
    assert state["qty_unallocated"] == state["qty_target"] - 300

    scheduling_service.revise_allocation_qty(
        db,
        HEAD,
        po_doc=po_doc,
        allocation_id=alloc_id,
        qty=500,
        reason="naik lagi di atas cetak",
    )
    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    assert state["allocations"][0]["qty"] == 500
    assert state["qty_printed"] == 300


def test_reschedule_created_once_for_shortfall(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="sched-resched-1",
        schedule_date="2026-11-17",
    )
    _confirm_print(db, created["allocations"][0]["allocation_id"], 400)

    first = scheduling_service.scan_reschedule(
        db, HEAD, po_doc=po_doc, as_of="2026-11-18"
    )
    second = scheduling_service.scan_reschedule(
        db, HEAD, po_doc=po_doc, as_of="2026-11-18"
    )
    assert first["created_task_keys"] == second["created_task_keys"]
    rows = tasks.list_tasks(db, task_type="penjadwalan-ulang")
    assert len(rows) == 1
    assert rows[0]["qty_target"] == 600
    assert rows[0]["context"]["qty_printed"] == 400
    assert rows[0]["assigned_role"] == "production-head"


def test_no_reschedule_when_print_complete(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 500}],
        idempotency_key="sched-complete-1",
        schedule_date="2026-11-17",
    )
    _confirm_print(db, created["allocations"][0]["allocation_id"], 500)
    result = scheduling_service.scan_reschedule(
        db, HEAD, po_doc=po_doc, as_of="2026-11-18"
    )
    assert result["created_task_keys"] == []
    assert tasks.list_tasks(db, task_type="penjadwalan-ulang") == []


def test_permission_enforced_for_scheduling(db: Database) -> None:
    from app.services.task_engine import TaskPermissionError

    po_doc = _seed_po(db)
    _open_gates(db)
    with pytest.raises(TaskPermissionError):
        scheduling_service.create_allocations(
            db,
            OPERATOR_USER,
            po_doc=po_doc,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": "op1@eternal.local", "qty": 10}],
            idempotency_key="sched-denied-1",
        )
    assert schedules.list_allocations(db, accurate_line_id=LINE_A) == []


def test_scheduling_writes_audit_trail(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 200}],
        idempotency_key="sched-audit-1",
    )
    events = list(db["audit_logs"].find({"action": "schedule.allocations.create"}))
    assert len(events) == 1
    assert events[0]["detail"]["qty_total"] == 200


def test_non_print_line_cannot_be_scheduled(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    with pytest.raises(scheduling_service.ScheduleQuantityError):
        scheduling_service.create_allocations(
            db,
            HEAD,
            po_doc=po_doc,
            accurate_line_id="106319",
            allocations=[{"operator_email": "op1@eternal.local", "qty": 10}],
            idempotency_key="sched-nonprint-1",
        )


def test_scheduling_task_progress_reflects_allocation(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    po_tasks.derive_tasks_for_po(db, po_doc)
    scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="sched-progress-1",
    )
    key = f"task:penjadwalan:po:{PO_ID}:line:{LINE_A}"
    task = tasks.get_task(db, key)
    assert task["qty_confirmed"] == 1000
    assert task["status"] == tasks.TASK_STATUS_DONE
