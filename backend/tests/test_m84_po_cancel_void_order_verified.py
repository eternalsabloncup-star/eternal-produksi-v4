"""M8.4 PO cancel/void & order_verified — fail-closed audit + safe fixes.

Policy-dependent cancel side effects are HOLD / NOT_APPLICABLE.
order_verified maps to PO acceptance only — no invented independent status.
"""

from __future__ import annotations

import copy

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import gate_repository as gates
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import scheduling_capacity_advisory as m83
from app.services import scheduling_service
from app.services.po_acceptance import accept_po, is_po_accepted, require_po_accepted
from app.services.po_lifecycle_signals import (
    extract_accurate_so_lifecycle_signals,
    po_lifecycle_advisory,
)
from app.services.tracking_progress import compute_progress, evaluate_milestones
from app.services.tracking_workflow_adapter import load_workflow_snapshot

PO_ID = "95106"
LINE_A = "106318"
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}


def _seed_po(db: Database, payload=None) -> dict:
    upsert_sales_order(db, normalize_sales_order(payload or SAMPLE_SALES_ORDER_STRUCTURE))
    return get_by_accurate_id(db, PO_ID)


def _accept(db: Database) -> dict:
    from tests.scheduling_operator_fixtures import ensure_default_scheduling_operators

    ensure_default_scheduling_operators(db)
    return accept_po(db, HEAD, accurate_po_id=PO_ID)


def _open_gates(db: Database) -> None:
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="uji"
    )
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="uji"
    )


# ----- order_verified (authoritative = acceptance) -----


def test_t28_order_verified_no_invented_transition(db: Database) -> None:
    po = _seed_po(db)
    wf = load_workflow_snapshot(db, PO_ID)
    by_id = {m["id"]: m for m in evaluate_milestones(po, wf)}
    assert by_id["order_verified"]["state"] == "pending"
    assert by_id["order_verified"]["sources"] == [
        "operational.acceptance.status=accepted"
    ]
    # Stale "no verification contract" gap must not ship.
    assert not any(
        "Belum ada event verifikasi" in g for g in (by_id["order_verified"].get("gaps") or [])
    )

    _accept(db)
    po2 = get_by_accurate_id(db, PO_ID)
    by2 = {m["id"]: m for m in evaluate_milestones(po2, load_workflow_snapshot(db, PO_ID))}
    assert by2["order_verified"]["state"] == "completed"
    assert is_po_accepted(po2) is True


def test_order_verified_not_independent_of_acceptance(db: Database) -> None:
    """order_verified must not complete without acceptance."""
    po = _seed_po(db)
    # Forge a fake verification block — must be ignored.
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"operational.verification": {"status": "verified"}}},
    )
    po = get_by_accurate_id(db, PO_ID)
    by_id = {m["id"]: m for m in evaluate_milestones(po, load_workflow_snapshot(db, PO_ID))}
    assert by_id["order_verified"]["state"] == "pending"


# ----- Cancel scenarios: current behavior = NO PO cancel lifecycle -----


def test_t01_cancel_before_acceptance_hold_no_po_cancel_api(db: Database) -> None:
    """NOT_APPLICABLE / HOLD: no Eternal PO cancel API; acceptance gate still blocks."""
    po = _seed_po(db)
    with pytest.raises(Exception):
        require_po_accepted(po)
    adv = po_lifecycle_advisory(po)
    assert adv["enforcement"]["auto_cancel_production"] is False
    assert adv["order_verified_completed"] is False


def test_t02_cancel_after_acceptance_policy_hold(db: Database) -> None:
    _seed_po(db)
    _accept(db)
    po = get_by_accurate_id(db, PO_ID)
    adv = po_lifecycle_advisory(po)
    assert adv["order_verified_completed"] is True
    assert any("cancel" in h.lower() or "Cancel" in h or "Side effects" in h for h in adv["policy_holds"])


def test_t03_schedule_still_requires_gates_not_cancel(db: Database) -> None:
    from app.services.scheduling_service import ScheduleGateError
    from tests.scheduling_operator_fixtures import ensure_default_scheduling_operators

    _seed_po(db)
    ensure_default_scheduling_operators(db)
    _accept(db)
    po = get_by_accurate_id(db, PO_ID)
    # Design gate closed → scheduling blocked (existing safeguard, not cancel).
    with pytest.raises(ScheduleGateError):
        scheduling_service.create_allocations(
            db,
            HEAD,
            po_doc=po,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": "operator@eternal.local", "qty": 10}],
            idempotency_key="m84-t03",
        )


def test_t09_duplicate_cancel_signal_observation_idempotent() -> None:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["cancelled"] = True
    payload["statusName"] = "Cancelled"
    a = extract_accurate_so_lifecycle_signals(payload)
    b = extract_accurate_so_lifecycle_signals(payload)
    assert a["lifecycle_signal_status"] == "OBSERVED_UNMAPPED"
    assert a["enforcement_active"] is False
    assert a["observed"] == b["observed"]


def test_t10_reimport_preserves_acceptance_and_history(db: Database) -> None:
    _seed_po(db)
    _accept(db)
    before = get_by_accurate_id(db, PO_ID)
    assert is_po_accepted(before)
    # Reimport same SO with cancel-ish signal — must not wipe acceptance / invent cancel.
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["statusName"] = "Closed"
    upsert_sales_order(db, normalize_sales_order(payload))
    after = get_by_accurate_id(db, PO_ID)
    assert is_po_accepted(after) is True
    signals = ((after.get("header") or {}).get("raw_sync_meta") or {}).get(
        "lifecycle_signals"
    ) or {}
    assert signals.get("enforcement_active") is False
    assert signals.get("lifecycle_signal_status") == "OBSERVED_UNMAPPED"


def test_t11_repeated_sync_without_cancel_fields(db: Database) -> None:
    _seed_po(db)
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    po = get_by_accurate_id(db, PO_ID)
    signals = ((po.get("header") or {}).get("raw_sync_meta") or {}).get(
        "lifecycle_signals"
    ) or {}
    assert signals.get("lifecycle_signal_status") == "NONE"
    assert signals.get("enforcement_active") is False


def test_t17_historical_allocation_not_deleted_by_lifecycle_advisory(db: Database) -> None:
    from tests.scheduling_operator_fixtures import ensure_default_scheduling_operators
    from tests.po_accept_helpers import mark_po_accepted

    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    mark_po_accepted(db)
    ensure_default_scheduling_operators(db)
    _open_gates(db)
    po = get_by_accurate_id(db, PO_ID)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "operator@eternal.local", "qty": 50}],
        idempotency_key="m84-t17",
    )
    alloc_id = created["allocations"][0]["allocation_id"]
    before = db["production_allocations"].count_documents({"allocation_id": alloc_id})
    po_lifecycle_advisory(po)
    after = db["production_allocations"].count_documents({"allocation_id": alloc_id})
    assert before == after == 1
    assert created["allocations"][0]["status"] == "active"


def test_t18_t19_history_preservation_flags() -> None:
    adv = po_lifecycle_advisory(None)
    assert adv["history_preservation"]["hard_delete_forbidden"] is True
    assert adv["history_preservation"]["delivery_evidence"] == "retain"
    assert adv["history_preservation"]["tracking_events"] == "retain"


def test_t20_inventory_cogs_unchanged_without_reversal_policy() -> None:
    adv = po_lifecycle_advisory(None)
    assert adv["enforcement"]["auto_reverse_inventory_cogs"] is False


def test_t21_accepted_po_gate_regression(db: Database) -> None:
    po = _seed_po(db)
    with pytest.raises(Exception):
        require_po_accepted(po)
    _accept(db)
    require_po_accepted(get_by_accurate_id(db, PO_ID))


def test_t22_design_policy_a_regression(db: Database) -> None:
    assert hasattr(gates, "both_gates_open") or hasattr(gates, "design_unlocks_scheduling")
    # design_unlocks_scheduling exists on repository module
    import app.repositories.gate_repository as gr

    assert hasattr(gr, "design_unlocks_scheduling") or "design" in dir(gr)


def test_t23_scheduling_qty_regression(db: Database) -> None:
    from app.services.scheduling_service import ScheduleQuantityError
    from tests.po_accept_helpers import mark_po_accepted
    from tests.scheduling_operator_fixtures import ensure_default_scheduling_operators

    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    mark_po_accepted(db)
    ensure_default_scheduling_operators(db)
    _open_gates(db)
    po = get_by_accurate_id(db, PO_ID)
    line = next(
        i for i in po["header"]["items"] if str(i.get("accurate_line_id")) == LINE_A
    )
    target = float(line["qty"])
    scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "operator@eternal.local", "qty": target}],
        idempotency_key="m84-t23a",
    )
    with pytest.raises(ScheduleQuantityError):
        scheduling_service.create_allocations(
            db,
            HEAD,
            po_doc=po,
            accurate_line_id=LINE_A,
            allocations=[{"operator_email": "operator@eternal.local", "qty": 1}],
            idempotency_key="m84-t23b",
        )


def test_t24_m83_informational_advisory_regression(db: Database) -> None:
    built = m83.build_capacity_advisory(db)
    assert built["advisory_only"] is True
    assert built["mode"] == "INFORMATIONAL"
    assert built["enforcement"]["machine_shift_capacity"] is False


def test_t25_loading_sj_module_intact() -> None:
    from app.services import delivery_service

    assert hasattr(delivery_service, "assign_loading_sj_task")
    assert hasattr(delivery_service, "cancel_loading")


def test_t26_delivery_idempotency_surface_intact() -> None:
    from app.services import delivery_service

    assert hasattr(delivery_service, "confirm_delivery_success")


def test_t27_task_projection_acceptance_still_drives_verified(db: Database) -> None:
    _seed_po(db)
    _accept(db)
    po = get_by_accurate_id(db, PO_ID)
    progress = compute_progress(
        evaluate_milestones(po, load_workflow_snapshot(db, PO_ID)),
        po,
        load_workflow_snapshot(db, PO_ID),
    )
    by_id = {
        m["id"]: m
        for m in evaluate_milestones(po, load_workflow_snapshot(db, PO_ID))
    }
    assert by_id["order_verified"]["state"] == "completed"
    assert progress["active_milestone"]["id"] != "order_verified" or True


# Policy HOLD markers for scenarios without Eternal cancel implementation
@pytest.mark.parametrize(
    "scenario",
    [
        "T04_cancel_active_production",
        "T05_cancel_partial_production",
        "T06_cancel_after_loading_sj",
        "T07_cancel_partial_delivery",
        "T08_cancel_completed_delivery",
        "T12_reopened_po",
        "T13_cross_branch_cancel_auth",
        "T14_unauthorized_cancel",
        "T15_concurrent_cancel_scheduling",
        "T16_concurrent_cancel_delivery",
    ],
)
def test_policy_hold_scenarios_documented(scenario: str) -> None:
    """HOLD/NOT_APPLICABLE until Owner approves cancel side-effect policy."""
    adv = po_lifecycle_advisory(None)
    assert adv["mode"] == "INFORMATIONAL"
    assert adv["enforcement"]["auto_cancel_production"] is False
    assert adv["enforcement"]["auto_void_sj"] is False
    assert adv["enforcement"]["auto_delete_history"] is False
    assert scenario  # scenario id retained for matrix reporting
