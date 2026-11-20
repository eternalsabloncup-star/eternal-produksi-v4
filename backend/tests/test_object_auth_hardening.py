"""Owner business contract — object auth / ops.full / payment bypass (isolated Mongo)."""

from __future__ import annotations

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import payment_service
from app.services import production_service as prod
from app.services import scheduling_service
from app.services.audit_log import ensure_audit_indexes, list_audit_events
from app.services.object_auth import ObjectAuthError, assert_branch_access
from app.services.task_engine import can_act, can_approve_payment_bypass
from tests.po_accept_helpers import mark_po_accepted
from tests.sample_flow_helpers import draft_and_confirm

PO_ID = "95106"
LINE = "106318"
BRANCH = "HQ"
PNG = b"\x89PNG\r\n\x1a\nobj-auth"
JPG = b"\xff\xd8\xff\xe0obj"

HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "branch_ids": [BRANCH],
}
HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "branch_ids": [BRANCH],
}
SCREENER = {
    "email_normalized": "screener@eternal.local",
    "role": "user",
    "operational_role": "screener",
    "branch_ids": [BRANCH],
}
OP_A = {
    "email_normalized": "opa@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "branch_ids": [BRANCH],
}
OP_B = {
    "email_normalized": "opb@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "branch_ids": [BRANCH],
}
DRV_A = {
    "email_normalized": "drva@eternal.local",
    "role": "user",
    "operational_role": "driver",
    "branch_ids": [BRANCH],
}
DRV_B = {
    "email_normalized": "drvb@eternal.local",
    "role": "user",
    "operational_role": "driver",
    "branch_ids": [BRANCH],
}
OPS_FULL = {
    "email_normalized": "opsfull@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["operations.full"],
}
ALL_BRANCHES_ONLY = {
    "email_normalized": "allbr@eternal.local",
    "role": "user",
    "operational_role": "driver",
    "effective_module_permissions": ["access.all_branches"],
}
SA = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
ADMIN = {
    "email_normalized": "admin@eternal.local",
    "role": "admin",
    "effective_module_permissions": [
        "operations.full",
        "reports.view",
        "access.all_branches",
    ],
}
ADMIN_NO_OPS = {
    "email_normalized": "admin.noops@eternal.local",
    "role": "user",
    "effective_module_permissions": ["reports.view", "access.all_branches"],
}


def _seed(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID}, {"$set": {"branch_code": BRANCH}}
    )
    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="obj"
        )
    master.seed_master_defaults(db)
    ensure_audit_indexes(db)
    master.set_role_actions(
        db,
        role="helper",
        task_type="pengiriman",
        actions=["view", "confirm", "cancel", "manage_evidence"],
    )
    master.set_role_actions(
        db,
        role="production-head",
        task_type="pengiriman",
        actions=["view", "confirm", "cancel", "manage_evidence"],
    )
    return get_by_accurate_id(db, PO_ID)


def _alloc(db: Database, email: str, qty: float = 100.0) -> str:
    po = get_by_accurate_id(db, PO_ID)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE,
        allocations=[{"operator_email": email, "qty": qty}],
        idempotency_key=f"obj-alloc-{email}-{qty}",
    )
    return created["allocations"][0]["allocation_id"]


def _prep_screen(db: Database, alloc_id: str, qty: float) -> None:
    state = prod.helper_quota_state(
        db, accurate_po_id=PO_ID, accurate_line_id=LINE
    )
    need = qty - float(state.get("qty_available") or 0)
    if need > 0:
        prod.confirm_preparation(
            db,
            HELPER,
            accurate_po_id=PO_ID,
            accurate_line_id=LINE,
            qty=need,
            idempotency_key=f"obj-prep-{alloc_id}",
        )
    alloc = __import__(
        "app.repositories.schedule_repository", fromlist=["get_allocation"]
    ).get_allocation(db, alloc_id)
    if prod.screen_confirmed_qty(db, alloc) <= 0:
        prod.confirm_screen(
            db,
            SCREENER,
            allocation_id=alloc_id,
            qty=min(10.0, qty),
            idempotency_key=f"obj-screen-{alloc_id}",
        )


def _sj_payload(sj_id: str, line_id: str = "106319", qty: float = 10.0) -> dict:
    return {
        "id": sj_id,
        "number": f"SJ/{sj_id}",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/OBJ",
        "transDate": "2026-11-20",
        "toAddress": "ADDR",
        "detailItem": [
            {
                "id": f"{sj_id}-L1",
                "salesOrderDetailId": line_id,
                "itemNo": "BRG",
                "detailName": "Item",
                "quantity": qty,
                "itemUnitName": "PCS",
            }
        ],
    }


def _upsert_sj(db: Database, sj_id: str, **extra: object) -> dict:
    deliveries.upsert_delivery_note(db, _sj_payload(sj_id))
    deliveries.delivery_notes_collection(db).update_one(
        {"accurate_sj_id": sj_id},
        {"$set": {"branch_code": BRANCH, **extra}},
    )
    return deliveries.get_delivery_note(db, sj_id) or {}


def test_operator_with_confirm_may_process_other_operator_metadata(db: Database) -> None:
    _seed(db)
    alloc = _alloc(db, "opa@eternal.local", 80)
    _prep_screen(db, alloc, 80)
    up = prod.upload_sample_draft(
        db,
        OP_B,
        allocation_id=alloc,
        filename="ok.png",
        content_type="image/png",
        content=PNG,
    )
    assert up["sample"]["version"] == 1


def test_loading_does_not_stamp_driver(db: Database) -> None:
    _seed(db)
    sj_id = "SJ-OBJ-LOAD"
    _upsert_sj(db, sj_id)
    delivery.confirm_loading(
        db, HELPER, accurate_sj_id=sj_id, idempotency_key="load-helper-1"
    )
    note = deliveries.get_delivery_note(db, sj_id)
    loading = deliveries.get_loading_event(db, sj_id)
    assert loading["actor_email"] == "helper@eternal.local"
    assert not (note.get("assigned_driver_email") or note.get("driver_email"))
    audits = list_audit_events(db, action="delivery.loading.confirm", limit=5)["items"]
    assert audits[0]["detail"]["loading_actor_email"] == "helper@eternal.local"


def test_any_eligible_courier_confirms_without_assignment(db: Database) -> None:
    _seed(db)
    sj_id = "SJ-OBJ-NO-ASSIGN"
    _upsert_sj(db, sj_id)
    delivery.confirm_loading(
        db, HELPER, accurate_sj_id=sj_id, idempotency_key="load-na-1"
    )
    deliveries.delivery_notes_collection(db).update_one(
        {"accurate_sj_id": sj_id},
        {"$set": {"assigned_driver_email": "someone.else@eternal.local"}},
    )
    event = delivery.confirm_delivery_success(
        db,
        DRV_B,
        accurate_sj_id=sj_id,
        recipient_name="Penerima",
        photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
        signature=("s.png", "image/png", PNG),
    )
    assert event.get("status") == deliveries.DELIVERY_SUCCESS
    assert event.get("courier_email") == "drvb@eternal.local"


def test_courier_without_permission_denied(db: Database) -> None:
    _seed(db)
    sj_id = "SJ-OBJ-NOP"
    _upsert_sj(db, sj_id)
    delivery.confirm_loading(
        db, HELPER, accurate_sj_id=sj_id, idempotency_key="load-nop"
    )
    from app.services.task_engine import TaskPermissionError

    bare = {
        "email_normalized": "viewer@eternal.local",
        "role": "user",
        "operational_role": "designer",
        "branch_ids": [BRANCH],
    }
    with pytest.raises(TaskPermissionError):
        delivery.confirm_delivery_success(
            db,
            bare,
            accurate_sj_id=sj_id,
            recipient_name="X",
            photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
            signature=("s.png", "image/png", PNG),
        )


def test_cross_branch_limited_user_denied(db: Database) -> None:
    _seed(db)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID}, {"$set": {"branch_code": "SMG"}}
    )
    jog = {
        **OP_A,
        "branch_ids": ["JOG"],
        "effective_module_permissions": [],
    }
    alloc = _alloc(db, "opa@eternal.local", 50)
    with pytest.raises(ObjectAuthError):
        prod.upload_sample_draft(
            db,
            jog,
            allocation_id=alloc,
            filename="x.png",
            content_type="image/png",
            content=PNG,
        )


def test_ops_full_cross_branch_and_assignment(db: Database) -> None:
    _seed(db)
    # Prep under HQ, then move PO/SJ to foreign branches for ops.full checks.
    alloc = _alloc(db, "opa@eternal.local", 55)
    _prep_screen(db, alloc, 55)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID}, {"$set": {"branch_code": "SMG"}}
    )
    up = prod.upload_sample_draft(
        db,
        OPS_FULL,
        allocation_id=alloc,
        filename="ops.png",
        content_type="image/png",
        content=PNG,
    )
    assert up["sample"]["version"] == 1

    sj_id = "SJ-OBJ-OPS"
    _upsert_sj(db, sj_id)
    deliveries.delivery_notes_collection(db).update_one(
        {"accurate_sj_id": sj_id}, {"$set": {"branch_code": "JOG"}}
    )
    delivery.confirm_loading(
        db, OPS_FULL, accurate_sj_id=sj_id, idempotency_key="load-ops"
    )
    ok = delivery.confirm_delivery_success(
        db,
        OPS_FULL,
        accurate_sj_id=sj_id,
        recipient_name="Ops",
        photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
        signature=("s.png", "image/png", PNG),
    )
    assert ok.get("status") == deliveries.DELIVERY_SUCCESS


def test_empty_branch_ids_denied(db: Database) -> None:
    _seed(db)
    bare = {
        "email_normalized": "bare@eternal.local",
        "role": "user",
        "operational_role": "operator",
        "branch_ids": [],
    }
    with pytest.raises(ObjectAuthError):
        assert_branch_access(bare, scope_object={"branch_code": BRANCH})
    with pytest.raises(ObjectAuthError):
        assert_branch_access(bare, scope_object={})


def test_all_branches_does_not_grant_actions_or_bypass(db: Database) -> None:
    _seed(db)
    assert can_act(db, ALL_BRANCHES_ONLY, task_type="cetak", action="confirm") is False
    assert can_approve_payment_bypass(db, ALL_BRANCHES_ONLY) is False
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, ALL_BRANCHES_ONLY, accurate_po_id=PO_ID, reason="no"
        )


def test_ops_full_payment_bypass_pass(db: Database) -> None:
    _seed(db)
    assert can_approve_payment_bypass(db, OPS_FULL) is True
    doc = payment_service.bypass_payment_gate(
        db, OPS_FULL, accurate_po_id=PO_ID, reason="Owner uji ops.full bypass"
    )
    payment = gates.get_gates(db, PO_ID)["payment"]
    assert payment["state"] == gates.GATE_OPEN
    assert doc.get("detail", {}).get("bypass") is True or "Bypass" in str(
        payment.get("reason") or ""
    )


def test_admin_with_ops_full_bypass_pass(db: Database) -> None:
    _seed(db)
    payment_service.bypass_payment_gate(
        db, ADMIN, accurate_po_id=PO_ID, reason="Admin ops.full"
    )
    assert gates.get_gates(db, PO_ID)["payment"]["state"] == gates.GATE_OPEN


def test_super_admin_bypass_via_effective_ops_full(db: Database) -> None:
    _seed(db)
    assert can_approve_payment_bypass(db, SA) is True
    payment_service.bypass_payment_gate(
        db, SA, accurate_po_id=PO_ID, reason="SA via ops.full"
    )


def test_bypass_denied_without_grant_or_ops_full(db: Database) -> None:
    _seed(db)
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, ADMIN_NO_OPS, accurate_po_id=PO_ID, reason="no grant"
        )


def test_bypass_denied_unaccepted_po(db: Database) -> None:
    _seed(db)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"operational.acceptance.status": "pending"}},
    )
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, OPS_FULL, accurate_po_id=PO_ID, reason="too early"
        )


def test_bypass_denied_empty_reason(db: Database) -> None:
    _seed(db)
    with pytest.raises(ValueError):
        payment_service.bypass_payment_gate(
            db, OPS_FULL, accurate_po_id=PO_ID, reason="   "
        )


def test_explicit_master_payment_bypass_grant(db: Database) -> None:
    _seed(db)
    master.set_role_actions(
        db,
        role="production-head",
        task_type="payment-bypass",
        actions=["approve"],
    )
    assert can_approve_payment_bypass(db, HEAD) is True
    payment_service.bypass_payment_gate(
        db, HEAD, accurate_po_id=PO_ID, reason="Master grant KP"
    )


def test_print_with_other_operator_metadata_and_gates(db: Database) -> None:
    _seed(db)
    alloc = _alloc(db, "opa@eternal.local", 90)
    _prep_screen(db, alloc, 90)
    draft_and_confirm(db, OP_A, alloc)
    prod.decide_sample(db, HEAD, allocation_id=alloc, version=1, approved=True)
    ok = prod.confirm_print(
        db,
        OP_B,
        allocation_id=alloc,
        qty=10,
        idempotency_key="print-cross-op",
    )
    assert ok["duplicate"] is False
