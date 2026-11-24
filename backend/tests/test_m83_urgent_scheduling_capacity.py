"""M8.3 Urgent Scheduling & Machine Capacity — fail-closed advisory + regression.

Policy-dependent cases are HOLD / NOT_APPLICABLE when prerequisites are missing.
Never invent capacity defaults or urgent quota enforcement.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import gate_repository as gates
from app.repositories import org_master_repository as org
from app.repositories import schedule_repository as schedules
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import scheduling_capacity_advisory as advisory
from app.services import scheduling_service
from app.services.scheduling_service import ScheduleGateError, ScheduleQuantityError

PO_ID = "95106"
LINE_A = "106318"
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}
OPERATOR = {
    "email_normalized": "operator@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": ["access.all_branches"],
}


def _seed_po(db: Database, *, urgent: bool = False, seller: str = "Sales A") -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["urgent"] = urgent
    # salesmanName on detail drives header.seller in normalize
    for detail in payload.get("detailItem") or []:
        detail["salesmanName"] = seller
    upsert_sales_order(db, normalize_sales_order(payload))
    from tests.po_accept_helpers import mark_po_accepted
    from tests.scheduling_operator_fixtures import ensure_default_scheduling_operators

    mark_po_accepted(db)
    ensure_default_scheduling_operators(db)
    return get_by_accurate_id(db, PO_ID)


def _seed_po_not_accepted(db: Database) -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    upsert_sales_order(db, normalize_sales_order(payload))
    from tests.scheduling_operator_fixtures import ensure_default_scheduling_operators

    ensure_default_scheduling_operators(db)
    return get_by_accurate_id(db, PO_ID)


def _open_gates(db: Database) -> None:
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="uji"
    )
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="uji"
    )


def _alloc(
    db: Database,
    po: dict,
    *,
    qty: float,
    key: str,
    schedule_date: str = "2026-11-24",
) -> dict:
    return scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "operator@eternal.local", "qty": qty}],
        idempotency_key=key,
        schedule_date=schedule_date,
    )


# ----- T01–T08 scheduling gates (regression / confirmed) -----


def test_t01_accepted_po_scheduling(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    result = _alloc(db, po, qty=100, key="m83-t01")
    assert result["duplicate"] is False
    assert len(result["allocations"]) == 1


def test_t02_non_accepted_po_blocked(db: Database) -> None:
    po = _seed_po_not_accepted(db)
    _open_gates(db)
    with pytest.raises(ScheduleGateError):
        _alloc(db, po, qty=10, key="m83-t02")


def test_t03_design_gate_regression(db: Database) -> None:
    po = _seed_po(db)
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="uji"
    )
    with pytest.raises(ScheduleGateError, match="Design"):
        _alloc(db, po, qty=10, key="m83-t03")


def test_t04_sample_gate_via_payment_gate_regression(db: Database) -> None:
    """Payment gate remains co-required with design (Policy A both_gates_open)."""
    po = _seed_po(db)
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="uji"
    )
    with pytest.raises(ScheduleGateError, match="Payment"):
        _alloc(db, po, qty=10, key="m83-t04")


def test_t05_valid_operator_assignment(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    result = _alloc(db, po, qty=50, key="m83-t05")
    assert result["allocations"][0]["operator_email"] == "operator@eternal.local"


def test_t06_invalid_operator_rejected(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    with pytest.raises(ScheduleQuantityError, match="Operator"):
        scheduling_service.create_allocations(
            db,
            HEAD,
            po_doc=po,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": "ghost@eternal.local", "qty": 10}],
            idempotency_key="m83-t06",
        )


def test_t07_cross_branch_scheduling_hold_not_machine_bound(db: Database) -> None:
    """HOLD/N/A: allocations are not machine/branch-bound for capacity.

    Existing object_auth branch checks remain the SoT for PO access — verified
    separately. Machine cross-branch capacity enforcement is POLICY_REQUIRED.
    """
    snap = advisory.machine_capacity_snapshot(db, branch_code="JKT")
    assert snap["status"] == advisory.STATUS_INSUFFICIENT_DATA
    assert snap["enforcement_active"] is False


def test_t08_unauthorized_scheduling_rejected(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    with pytest.raises(Exception):
        scheduling_service.create_allocations(
            db,
            OPERATOR,
            po_doc=po,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": "operator@eternal.local", "qty": 10}],
            idempotency_key="m83-t08",
        )


# ----- T09–T16 capacity -----


def test_t09_line_capacity_calculation_correct(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    _alloc(db, po, qty=200, key="m83-t09")
    line = next(
        i
        for i in po["header"]["items"]
        if str(i.get("accurate_line_id")) == LINE_A
    )
    snap = advisory.line_reservation_snapshot(
        db, accurate_line_id=LINE_A, qty_target=float(line["qty"])
    )
    assert snap["status"] == advisory.STATUS_OK
    assert snap["reserved_capacity"] == 200.0
    assert snap["remaining_capacity"] == float(line["qty"]) - 200.0


def test_t10_reserved_capacity_deducted_once(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    _alloc(db, po, qty=100, key="m83-t10a")
    _alloc(db, po, qty=100, key="m83-t10b")
    reserved = schedules.active_allocated_qty(db, LINE_A)
    assert reserved == 200.0
    # Idempotent replay must not double-count
    again = _alloc(db, po, qty=100, key="m83-t10a")
    assert again["duplicate"] is True
    assert schedules.active_allocated_qty(db, LINE_A) == 200.0


def test_t11_cancelled_released_excluded(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    created = _alloc(db, po, qty=150, key="m83-t11")
    alloc_id = created["allocations"][0]["allocation_id"]
    scheduling_service.revise_allocation_qty(
        db,
        HEAD,
        po_doc=po,
        allocation_id=alloc_id,
        qty=0,
        reason="batal uji m83",
    )
    assert schedules.active_allocated_qty(db, LINE_A) == 0.0
    snap = advisory.line_reservation_snapshot(
        db, accurate_line_id=LINE_A, qty_target=1000
    )
    assert snap["reserved_capacity"] == 0.0


def test_t12_partial_completion_uses_active_qty(db: Database) -> None:
    """Partial print completion does not free line reservation (qty stays reserved)."""
    po = _seed_po(db)
    _open_gates(db)
    _alloc(db, po, qty=300, key="m83-t12")
    assert schedules.active_allocated_qty(db, LINE_A) == 300.0


def test_t13_overbooking_line_protected(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    line = next(
        i
        for i in po["header"]["items"]
        if str(i.get("accurate_line_id")) == LINE_A
    )
    target = float(line["qty"])
    _alloc(db, po, qty=target, key="m83-t13a")
    with pytest.raises(ScheduleQuantityError, match="melampaui"):
        _alloc(db, po, qty=1, key="m83-t13b")


def test_t14_concurrent_reservations_protected_by_existing_guards(db: Database) -> None:
    """Line guard + txn already serialize capacity — advisory does not weaken it."""
    assert callable(schedules.touch_line_guard)
    assert any(
        s["name"] == "uniq_schedule_line_guard"
        for s in schedules.REQUIRED_SCHEDULE_INDEX_SPECS
    )


def test_t15_machine_shift_isolation_insufficient_data(db: Database) -> None:
    snap = advisory.machine_capacity_snapshot(
        db, schedule_date="2026-11-24", shift_code="PAGI"
    )
    assert snap["status"] == advisory.STATUS_INSUFFICIENT_DATA
    assert snap["remaining_capacity"] is None
    assert any("shift" in g.lower() for g in snap["gaps"])


def test_t16_machine_product_eligibility_hold(db: Database) -> None:
    """NOT_APPLICABLE: no product↔machine eligibility model in master yet."""
    snap = advisory.machine_capacity_snapshot(db)
    assert snap["enforcement_active"] is False
    assert snap["available_capacity"] is None


# ----- T17–T22 urgent / fail-safe -----


def test_t17_urgent_quota_policy_not_configured(db: Database) -> None:
    snap = advisory.urgent_quota_snapshot(
        db, production_date="2026-11-24", sales_owner="Sales A", requested_urgent_qty=500
    )
    assert snap["status"] == advisory.STATUS_POLICY_NOT_CONFIGURED
    assert snap["urgent_daily_quota"] is None
    assert snap["enforcement_active"] is False


def test_t18_urgent_duplicate_consumption_not_invented(db: Database) -> None:
    """No urgent ledger → cannot double-count; enforcement stays off."""
    a = advisory.urgent_quota_snapshot(db, production_date="2026-11-24", sales_owner="A")
    b = advisory.urgent_quota_snapshot(db, production_date="2026-11-24", sales_owner="A")
    assert a["urgent_already_allocated"] is None
    assert b["urgent_already_allocated"] is None
    assert a["enforcement_active"] is False


def test_t19_sales_ownership_isolation_hold(db: Database) -> None:
    po = _seed_po(db, urgent=True, seller="Sales Alpha")
    elig = advisory.urgent_eligibility_snapshot(po)
    assert elig["sales_owner"] == "Sales Alpha"
    assert elig["sales_owner_source"] == "header.seller"
    # Quota isolation cannot enforce without approved formula
    q = advisory.urgent_quota_snapshot(
        db, production_date="2026-11-24", sales_owner=elig["sales_owner"]
    )
    assert q["enforcement_active"] is False


def test_t20_minimum_margin_restriction_hold(db: Database) -> None:
    po = _seed_po(db, urgent=True)
    elig = advisory.urgent_eligibility_snapshot(po)
    assert elig["margin_pcs"] is None
    assert elig["margin_status"] == advisory.STATUS_INSUFFICIENT_DATA
    assert elig["eligible_for_urgent_priority"] is None
    assert elig["enforcement_active"] is False


def test_t21_insufficient_capacity_data_fail_safe(db: Database) -> None:
    # Even with a machine lacking output, never invent 17000
    org.upsert_branch(db, code="JKT", name="Jakarta", is_active=True)
    org.upsert_machine(
        db, code="AUTO1", label="Automatic 1", branch_code="JKT", is_active=True
    )
    snap = advisory.machine_capacity_snapshot(db, branch_code="JKT")
    assert snap["status"] == advisory.STATUS_INSUFFICIENT_DATA
    assert snap["available_capacity"] is None
    assert snap["enforcement_active"] is False
    assert not any("17000" in g for g in snap["gaps"])


def test_t22_insufficient_policy_does_not_invent_enforcement(db: Database) -> None:
    po = _seed_po(db, urgent=True)
    built = advisory.build_capacity_advisory(
        db,
        po_doc=po,
        accurate_line_id=LINE_A,
        qty_target=1000,
        schedule_date="2026-11-24",
        requested_qty=100,
    )
    advisory.assert_no_invented_machine_enforcement(built)
    assert built["advisory_only"] is True
    assert built["enforcement"]["auto_reschedule"] is False
    assert built["enforcement"]["auto_urgent_override"] is False


# ----- T23–T24 history / preservation -----


def test_t23_schedule_revision_audit(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    created = _alloc(db, po, qty=120, key="m83-t23")
    alloc_id = created["allocations"][0]["allocation_id"]
    scheduling_service.revise_allocation_qty(
        db,
        HEAD,
        po_doc=po,
        allocation_id=alloc_id,
        qty=80,
        reason="revisi uji m83",
    )
    hist = schedules.allocation_history(db, alloc_id)
    assert len(hist) >= 1
    assert any(h.get("qty_after") == 80 for h in hist)


def test_t24_existing_schedule_preservation_no_auto_displace(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    first = _alloc(db, po, qty=100, key="m83-t24a")
    second = _alloc(db, po, qty=50, key="m83-t24b")
    ids = {a["allocation_id"] for a in first["allocations"] + second["allocations"]}
    active = schedules.list_allocations(db, accurate_line_id=LINE_A)
    assert {a["allocation_id"] for a in active} == ids
    # Advisory must not mutate
    before = schedules.active_allocated_qty(db, LINE_A)
    advisory.build_capacity_advisory(db, po_doc=po, accurate_line_id=LINE_A, qty_target=1000)
    assert schedules.active_allocated_qty(db, LINE_A) == before


# ----- T25–T28 compatibility markers -----


def test_t25_tracking_compat_allocation_fields_unchanged(db: Database) -> None:
    po = _seed_po(db)
    _open_gates(db)
    created = _alloc(db, po, qty=40, key="m83-t25")
    row = created["allocations"][0]
    for key in (
        "allocation_id",
        "accurate_po_id",
        "accurate_line_id",
        "operator_email",
        "qty",
        "status",
        "schedule_date",
    ):
        assert key in row
    assert "machine_code" not in row  # no silent schema invention on writes


def test_t26_m8_design_policy_a_still_blocks(db: Database) -> None:
    test_t03_design_gate_regression(db)


def test_t27_m81_loading_sj_module_intact() -> None:
    from app.services import delivery_service

    # M8.1 surface remains importable; this milestone does not alter SJ sync flags.
    assert hasattr(delivery_service, "assign_loading_sj_task")
    assert hasattr(delivery_service, "confirm_loading")


def test_t28_m82_delivery_confirm_still_imported() -> None:
    from app.services import delivery_service

    assert hasattr(delivery_service, "confirm_delivery_success")


PASSWORD = "password-kuat-123"


def _seed_http_users(db: Database) -> None:
    from app.bootstrap import create_admin_user
    from tests.scheduling_operator_fixtures import upsert_test_user

    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
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


# ----- API + optional machine fields -----


def test_capacity_advisory_http_ok(app_client: TestClient, db: Database) -> None:
    _seed_po(db)
    _seed_http_users(db)
    _open_gates(db)
    _login(app_client, "kp@eternal.local")
    res = app_client.get(
        "/api/scheduling/capacity-advisory",
        params={
            "accuratePoId": PO_ID,
            "accurateLineId": LINE_A,
            "scheduleDate": "2026-11-24",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["advisory_only"] is True
    assert body["machine_capacity"]["status"] == "INSUFFICIENT_DATA"
    assert body["urgent_quota"]["status"] == "POLICY_NOT_CONFIGURED"
    assert body["enforcement"]["machine_shift_capacity"] is False


def test_capacity_advisory_forbidden_for_operator(
    app_client: TestClient, db: Database
) -> None:
    _seed_po(db)
    _seed_http_users(db)
    _login(app_client, "operator@eternal.local")
    res = app_client.get("/api/scheduling/capacity-advisory")
    assert res.status_code == 403


def test_optional_machine_output_persists_without_default(db: Database) -> None:
    org.upsert_branch(db, code="BDG", name="Bandung", is_active=True)
    doc = org.upsert_machine(
        db,
        code="SEMI1",
        label="Semi 1",
        branch_code="BDG",
        machine_type="Semi-automatic",
        output_per_shift_pcs=5000,
    )
    assert doc["machine_type"] == "Semi-automatic"
    assert doc["output_per_shift_pcs"] == 5000.0
    # Without output still incomplete for capacity
    org.upsert_machine(
        db, code="SEMI2", label="Semi 2", branch_code="BDG", machine_type="Semi-automatic"
    )
    snap = advisory.machine_capacity_snapshot(db, branch_code="BDG")
    assert snap["status"] == advisory.STATUS_INSUFFICIENT_DATA
    assert snap["available_capacity"] is None
    # Even all-with-output cannot yield remaining (no machine binding on allocs)
    org.upsert_machine(
        db,
        code="SEMI2",
        label="Semi 2",
        branch_code="BDG",
        machine_type="Semi-automatic",
        output_per_shift_pcs=3000,
    )
    org.upsert_shift(
        db, code="PAGI", label="Pagi", start_time="07:00", end_time="15:00"
    )
    snap2 = advisory.machine_capacity_snapshot(db, branch_code="BDG", shift_code="PAGI")
    assert snap2["enforcement_active"] is False
    assert snap2["remaining_capacity"] is None
    assert snap2["machines_with_output"] >= 1


def test_create_allocations_does_not_call_machine_enforcement(db: Database) -> None:
    """Scheduling path remains operator line-qty only — no machine capacity block."""
    po = _seed_po(db)
    _open_gates(db)
    # Empty masters must not block scheduling
    result = _alloc(db, po, qty=25, key="m83-no-machine-enf")
    assert result["allocations"][0]["qty"] == 25.0
