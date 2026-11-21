"""Revisi Qty alokasi: floor Qty cetak, kapasitas PO, HTTP 409 code."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.domain.operational import TaskRef, build_task_key
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import schedule_repository as schedules
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import scheduling_service

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_A = "106318"
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}


def _seed_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted
    from tests.scheduling_operator_fixtures import ensure_default_scheduling_operators

    mark_po_accepted(db)
    ensure_default_scheduling_operators(db)
    return get_by_accurate_id(db, PO_ID)


def _open_gates(db: Database) -> None:
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji"
        )


def _confirm_print(db: Database, allocation_id: str, qty: float) -> None:
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


def _seed_http(db: Database) -> None:
    _seed_po(db)
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    from tests.scheduling_operator_fixtures import upsert_test_user

    upsert_test_user(
        db,
        "kp@eternal.local",
        operational_role="production-head",
        display_name="Kepala Produksi",
        password=PASSWORD,
    )
    upsert_test_user(
        db,
        "operator@eternal.local",
        operational_role="operator",
        display_name="Operator",
        password=PASSWORD,
    )


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def test_revise_floor_and_capacity_service(db: Database) -> None:
    """PO line A qty=1000 — A=400 B=400, cetak A=200."""
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[
            {"operator_email": "op-a@eternal.local", "qty": 400},
            {"operator_email": "op-b@eternal.local", "qty": 400},
        ],
        idempotency_key="revise-floor-split",
    )
    alloc_a = created["allocations"][0]["allocation_id"]
    _confirm_print(db, alloc_a, 200)

    try:
        scheduling_service.revise_allocation_qty(
            db,
            HEAD,
            po_doc=po_doc,
            allocation_id=alloc_a,
            qty=100,
            reason="terlalu kecil",
        )
        assert False, "harusnya ditolak"
    except scheduling_service.AllocationBelowConfirmedPrintError as exc:
        assert exc.code == "ALLOCATION_BELOW_CONFIRMED_PRINT"

    scheduling_service.revise_allocation_qty(
        db,
        HEAD,
        po_doc=po_doc,
        allocation_id=alloc_a,
        qty=200,
        reason="turun ke cetak",
    )
    scheduling_service.revise_allocation_qty(
        db,
        HEAD,
        po_doc=po_doc,
        allocation_id=alloc_a,
        qty=300,
        reason="naik sebagian",
    )

    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    qty_po = state["qty_target"]
    alloc_b_qty = next(
        a["qty"] for a in state["allocations"] if a["allocation_id"] != alloc_a
    )
    over = qty_po - alloc_b_qty + 1
    try:
        scheduling_service.revise_allocation_qty(
            db,
            HEAD,
            po_doc=po_doc,
            allocation_id=alloc_a,
            qty=over,
            reason="melebihi PO",
        )
        assert False, "harusnya kapasitas ditolak"
    except scheduling_service.ScheduleQuantityError:
        pass

    assert (
        scheduling_service.printed_qty_for_allocation(
            db,
            accurate_po_id=PO_ID,
            accurate_line_id=LINE_A,
            allocation_id=alloc_a,
        )
        == 200
    )
    hist = schedules.allocation_history(db, alloc_a)
    assert any(h["action"] == "update_qty" and h["qty_after"] == 300 for h in hist)


def test_revise_below_print_http_409_code(app_client: TestClient, db: Database) -> None:
    _seed_http(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")
    created = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 800}],
            "idempotency_key": "http-revise-floor-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200, created.text
    alloc_id = created.json()["allocations"][0]["allocation_id"]
    _confirm_print(db, alloc_id, 300)

    bad = app_client.put(
        f"/api/scheduling/allocations/{alloc_id}/qty",
        json={"qty": 100, "reason": "di bawah cetak"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 409, bad.text
    body = bad.json()["detail"]
    assert isinstance(body, dict)
    assert body["code"] == "ALLOCATION_BELOW_CONFIRMED_PRINT"
    assert "tidak boleh lebih kecil" in body["detail"]

    ok = app_client.put(
        f"/api/scheduling/allocations/{alloc_id}/qty",
        json={"qty": 400, "reason": "naik di atas cetak"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["qty"] == 400

    op_csrf = _login(app_client, "operator@eternal.local")
    denied = app_client.put(
        f"/api/scheduling/allocations/{alloc_id}/qty",
        json={"qty": 450, "reason": "operator coba"},
        headers={"X-CSRF-Token": op_csrf},
    )
    assert denied.status_code == 403


def test_line_state_exposes_revise_bounds(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 700}],
        idempotency_key="revise-bounds-1",
    )
    alloc_id = created["allocations"][0]["allocation_id"]
    _confirm_print(db, alloc_id, 200)
    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    row = state["allocations"][0]
    assert row["qty_revise_min"] == 200
    assert row["can_revise"] is True
    assert row["qty_revise_max"] == state["qty_target"]


def test_revise_rejects_zero_when_print_confirmed(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 500}],
        idempotency_key="revise-zero-print",
    )
    alloc_id = created["allocations"][0]["allocation_id"]
    _confirm_print(db, alloc_id, 100)
    with pytest.raises(scheduling_service.AllocationBelowConfirmedPrintError):
        scheduling_service.revise_allocation_qty(
            db,
            HEAD,
            po_doc=po_doc,
            allocation_id=alloc_id,
            qty=0,
            reason="nol",
        )


def test_revise_rejects_below_screen_confirmed(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 600}],
        idempotency_key="revise-screen-floor",
    )
    alloc_id = created["allocations"][0]["allocation_id"]
    ref = TaskRef(
        accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc_id
    )
    master.seed_master_defaults(db)
    tasks.upsert_task(
        db,
        task_type="screen",
        scope="po_line_allocation",
        assigned_role="screener",
        ref=ref,
        qty_target=10_000,
        label="Screen",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("screen", "po_line_allocation", ref),
        qty=350,
        actor_email="screener@eternal.local",
        actor_role="screener",
        idempotency_key=f"screen-{alloc_id}-350",
    )
    with pytest.raises(scheduling_service.AllocationDownstreamConflictError):
        scheduling_service.revise_allocation_qty(
            db,
            HEAD,
            po_doc=po_doc,
            allocation_id=alloc_id,
            qty=300,
            reason="di bawah screen",
        )
    # Di atas screen → OK; ledger screen tidak berubah.
    scheduling_service.revise_allocation_qty(
        db,
        HEAD,
        po_doc=po_doc,
        allocation_id=alloc_id,
        qty=400,
        reason="di atas screen",
    )
    from app.services.production_service import screen_confirmed_qty

    alloc = schedules.get_allocation(db, alloc_id)
    assert alloc is not None
    assert screen_confirmed_qty(db, alloc) == 350
    assert alloc["qty"] == 400


def test_update_allocation_qty_cas(db: Database) -> None:
    po_doc = _seed_po(db)
    _open_gates(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 400}],
        idempotency_key="revise-cas-1",
    )
    alloc_id = created["allocations"][0]["allocation_id"]
    miss = schedules.update_allocation_qty(
        db,
        allocation_id=alloc_id,
        qty=300,
        actor_email="kp@eternal.local",
        reason="cas miss",
        expected_qty=999,
    )
    assert miss is None
    hit = schedules.update_allocation_qty(
        db,
        allocation_id=alloc_id,
        qty=300,
        actor_email="kp@eternal.local",
        reason="cas hit",
        expected_qty=400,
    )
    assert hit is not None
    assert hit["qty"] == 300
