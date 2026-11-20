"""Negative + positive object-scope authorization (isolated Mongo only)."""

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
from app.services.task_engine import can_act
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
WAREHOUSE = {
    "email_normalized": "gudang@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "branch_ids": [BRANCH],
    # Loading memakai grant pengiriman.confirm (custom Master / test).
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
    # KP/gudang boleh Loading tanpa menjadi kurir (grant eksplisit di Master).
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
    note = deliveries.upsert_delivery_note(db, _sj_payload(sj_id))
    deliveries.delivery_notes_collection(db).update_one(
        {"accurate_sj_id": sj_id},
        {"$set": {"branch_code": BRANCH, **extra}},
    )
    return deliveries.get_delivery_note(db, sj_id) or note


def test_p0_operator_b_denied_on_alloc_a(db: Database) -> None:
    _seed(db)
    alloc = _alloc(db, "opa@eternal.local", 80)
    _prep_screen(db, alloc, 80)
    with pytest.raises(ObjectAuthError):
        prod.upload_sample_draft(
            db,
            OP_B,
            allocation_id=alloc,
            filename="x.png",
            content_type="image/png",
            content=PNG,
        )
    # Positive: owner of allocation
    up = prod.upload_sample_draft(
        db,
        OP_A,
        allocation_id=alloc,
        filename="ok.png",
        content_type="image/png",
        content=PNG,
    )
    assert up["sample"]["version"] == 1


def test_p0_operator_b_denied_print_on_alloc_a(db: Database) -> None:
    _seed(db)
    alloc = _alloc(db, "opa@eternal.local", 90)
    _prep_screen(db, alloc, 90)
    draft_and_confirm(db, OP_A, alloc)
    prod.decide_sample(
        db, HEAD, allocation_id=alloc, version=1, approved=True
    )
    with pytest.raises(ObjectAuthError):
        prod.confirm_print(
            db,
            OP_B,
            allocation_id=alloc,
            qty=10,
            idempotency_key="print-b-denied",
        )
    ok = prod.confirm_print(
        db,
        OP_A,
        allocation_id=alloc,
        qty=10,
        idempotency_key="print-a-ok",
    )
    assert ok["duplicate"] is False


def test_p0_driver_b_denied_on_sj_of_driver_a(db: Database) -> None:
    _seed(db)
    sj_id = "SJ-OBJ-D1"
    _upsert_sj(db, sj_id)
    delivery.confirm_loading(
        db, DRV_A, accurate_sj_id=sj_id, idempotency_key="load-d1"
    )
    note = deliveries.get_delivery_note(db, sj_id)
    assert note.get("assigned_driver_email") == "drva@eternal.local"
    with pytest.raises(ObjectAuthError):
        delivery.confirm_delivery_success(
            db,
            DRV_B,
            accurate_sj_id=sj_id,
            recipient_name="X",
            photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
            signature=("s.png", "image/png", PNG),
        )
    event = delivery.confirm_delivery_success(
        db,
        DRV_A,
        accurate_sj_id=sj_id,
        recipient_name="Penerima",
        photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
        signature=("s.png", "image/png", PNG),
    )
    assert event.get("status") == deliveries.DELIVERY_SUCCESS


def test_loading_warehouse_preserves_assigned_driver(db: Database) -> None:
    """Loading oleh KP/gudang; pengiriman oleh kurir yang sudah ditugaskan."""
    _seed(db)
    sj_id = "SJ-OBJ-WH"
    _upsert_sj(db, sj_id, assigned_driver_email="drva@eternal.local")
    delivery.confirm_loading(
        db, WAREHOUSE, accurate_sj_id=sj_id, idempotency_key="load-wh-1"
    )
    note = deliveries.get_delivery_note(db, sj_id)
    loading = deliveries.get_loading_event(db, sj_id)
    assert loading["actor_email"] == "gudang@eternal.local"
    assert note.get("assigned_driver_email") == "drva@eternal.local"
    assert note.get("assigned_driver_email") != loading["actor_email"]

    with pytest.raises(ObjectAuthError):
        delivery.confirm_delivery_success(
            db,
            DRV_B,
            accurate_sj_id=sj_id,
            recipient_name="X",
            photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
            signature=("s.png", "image/png", PNG),
        )
    ok = delivery.confirm_delivery_success(
        db,
        DRV_A,
        accurate_sj_id=sj_id,
        recipient_name="Penerima",
        photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
        signature=("s.png", "image/png", PNG),
    )
    assert ok.get("status") == deliveries.DELIVERY_SUCCESS

    audits = list_audit_events(db, action="delivery.loading.confirm", limit=5)["items"]
    assert audits
    detail = audits[0].get("detail") or {}
    assert detail.get("loading_actor_email") == "gudang@eternal.local"
    assert detail.get("assigned_driver_email") == "drva@eternal.local"
    assert detail.get("assignment_source") == "preserved"


def test_loading_warehouse_explicit_assign_then_delivery(db: Database) -> None:
    """Sumber otoritatif: assigned_driver_email eksplisit saat Loading (bukan aktor)."""
    _seed(db)
    sj_id = "SJ-OBJ-WH-REQ"
    _upsert_sj(db, sj_id)
    delivery.confirm_loading(
        db,
        WAREHOUSE,
        accurate_sj_id=sj_id,
        idempotency_key="load-wh-req",
        assigned_driver_email="drva@eternal.local",
    )
    note = deliveries.get_delivery_note(db, sj_id)
    loading = deliveries.get_loading_event(db, sj_id)
    assert loading["actor_email"] == "gudang@eternal.local"
    assert note.get("assigned_driver_email") == "drva@eternal.local"
    delivery.confirm_delivery_success(
        db,
        DRV_A,
        accurate_sj_id=sj_id,
        recipient_name="Penerima",
        photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
        signature=("s.png", "image/png", PNG),
    )


def test_loading_warehouse_without_driver_does_not_invent_assignment(
    db: Database,
) -> None:
    _seed(db)
    sj_id = "SJ-OBJ-WH-NONE"
    _upsert_sj(db, sj_id)
    delivery.confirm_loading(
        db, WAREHOUSE, accurate_sj_id=sj_id, idempotency_key="load-wh-none"
    )
    note = deliveries.get_delivery_note(db, sj_id)
    loading = deliveries.get_loading_event(db, sj_id)
    assert loading["actor_email"] == "gudang@eternal.local"
    assert not (note.get("assigned_driver_email") or note.get("driver_email"))
    with pytest.raises(ObjectAuthError):
        delivery.confirm_delivery_success(
            db,
            DRV_A,
            accurate_sj_id=sj_id,
            recipient_name="X",
            photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
            signature=("s.png", "image/png", PNG),
        )


def test_loading_does_not_overwrite_existing_driver(db: Database) -> None:
    _seed(db)
    sj_id = "SJ-OBJ-KEEP"
    _upsert_sj(db, sj_id, assigned_driver_email="drva@eternal.local")
    with pytest.raises(ObjectAuthError):
        delivery.confirm_loading(
            db,
            WAREHOUSE,
            accurate_sj_id=sj_id,
            idempotency_key="load-overwrite-denied",
            assigned_driver_email="drvb@eternal.local",
        )
    # Loading dengan request sama / tanpa request tetap preserve.
    delivery.confirm_loading(
        db, WAREHOUSE, accurate_sj_id=sj_id, idempotency_key="load-keep"
    )
    note = deliveries.get_delivery_note(db, sj_id)
    assert note.get("assigned_driver_email") == "drva@eternal.local"


def test_sj_revision_reload_keeps_driver_and_loading_actor_separate(
    db: Database,
) -> None:
    _seed(db)
    sj_id = "SJ-OBJ-REV"
    _upsert_sj(db, sj_id, assigned_driver_email="drva@eternal.local")
    delivery.confirm_loading(
        db, WAREHOUSE, accurate_sj_id=sj_id, idempotency_key="load-rev-1"
    )
    delivery.cancel_loading(
        db, WAREHOUSE, accurate_sj_id=sj_id, reason="revisi SJ opsi B"
    )
    note_mid = deliveries.get_delivery_note(db, sj_id)
    assert note_mid.get("assigned_driver_email") == "drva@eternal.local"
    delivery.confirm_loading(
        db, WAREHOUSE, accurate_sj_id=sj_id, idempotency_key="load-rev-2"
    )
    note = deliveries.get_delivery_note(db, sj_id)
    loading = deliveries.get_loading_event(db, sj_id)
    assert note.get("assigned_driver_email") == "drva@eternal.local"
    assert loading["actor_email"] == "gudang@eternal.local"
    assert loading["sequence"] == 2


def test_p0_cross_branch_mutate_denied(db: Database) -> None:
    _seed(db)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID}, {"$set": {"branch_code": "SMG"}}
    )
    helper_smg = {
        **HELPER,
        "branch_ids": ["SMG"],
        "effective_module_permissions": [],
    }
    screener_smg = {
        **SCREENER,
        "branch_ids": ["SMG"],
        "effective_module_permissions": [],
    }
    op_smg = {
        **OP_A,
        "branch_ids": ["SMG"],
        "effective_module_permissions": [],
    }
    head_smg = {
        **HEAD,
        "branch_ids": ["SMG"],
        "effective_module_permissions": [],
    }
    alloc = _alloc_for(db, head_smg, "opa@eternal.local", 70)
    state = prod.helper_quota_state(
        db, accurate_po_id=PO_ID, accurate_line_id=LINE
    )
    need = 70 - float(state.get("qty_available") or 0)
    if need > 0:
        prod.confirm_preparation(
            db,
            helper_smg,
            accurate_po_id=PO_ID,
            accurate_line_id=LINE,
            qty=need,
            idempotency_key="obj-prep-branch",
        )
    prod.confirm_screen(
        db,
        screener_smg,
        allocation_id=alloc,
        qty=10,
        idempotency_key="obj-screen-branch",
    )
    jog = {
        **OP_A,
        "branch_ids": ["JOG"],
        "effective_module_permissions": [],
    }
    with pytest.raises(ObjectAuthError):
        prod.upload_sample_draft(
            db,
            jog,
            allocation_id=alloc,
            filename="x.png",
            content_type="image/png",
            content=PNG,
        )
    up = prod.upload_sample_draft(
        db,
        op_smg,
        allocation_id=alloc,
        filename="ok.png",
        content_type="image/png",
        content=PNG,
    )
    assert up["sample"]["version"] == 1


def _alloc_for(db: Database, head: dict, email: str, qty: float) -> str:
    po = get_by_accurate_id(db, PO_ID)
    created = scheduling_service.create_allocations(
        db,
        head,
        po_doc=po,
        accurate_line_id=LINE,
        allocations=[{"operator_email": email, "qty": qty}],
        idempotency_key=f"obj-alloc-br-{email}-{qty}",
    )
    return created["allocations"][0]["allocation_id"]


def test_p1_user_without_branch_ids_denied_even_on_legacy_object(db: Database) -> None:
    _seed(db)
    bare = {
        "email_normalized": "bare@eternal.local",
        "role": "user",
        "operational_role": "operator",
        "branch_ids": [],
    }
    with pytest.raises(ObjectAuthError):
        assert_branch_access(bare, scope_object={})
    with pytest.raises(ObjectAuthError):
        assert_branch_access(bare, scope_object={"branch_code": BRANCH})
    # Objek tanpa branch_code juga ditolak untuk akun terbatas ber-branch.
    scoped = {**OP_A}
    with pytest.raises(ObjectAuthError):
        assert_branch_access(scoped, scope_object={})


def test_p1_unassigned_driver_fail_closed(db: Database) -> None:
    _seed(db)
    sj_id = "SJ-OBJ-UNASSIGNED"
    _upsert_sj(db, sj_id)
    deliveries.insert_loading_event(
        db,
        accurate_sj_id=sj_id,
        accurate_po_id=PO_ID,
        sj_no=f"SJ/{sj_id}",
        items=[
            {
                "sj_line_id": f"{sj_id}-L1",
                "accurate_line_id": "106319",
                "item_code": "BRG",
                "qty": 10,
                "unit": "PCS",
                "process_kind": "Non-Cetak",
            }
        ],
        actor_email="warehouse@eternal.local",
        idempotency_key="orphan-load",
        sj_version=1,
        sequence=1,
    )
    with pytest.raises(ObjectAuthError):
        delivery.confirm_delivery_success(
            db,
            DRV_A,
            accurate_sj_id=sj_id,
            recipient_name="X",
            photos=[("a.jpg", "image/jpeg", JPG), ("b.jpg", "image/jpeg", JPG)],
            signature=("s.png", "image/png", PNG),
        )


def test_p1_all_branches_does_not_grant_actions(db: Database) -> None:
    _seed(db)
    assert can_act(db, ADMIN, task_type="cetak", action="confirm") is False
    assert can_act(db, ADMIN, task_type="pengiriman", action="confirm") is False


def test_p1_04_super_admin_bypass_payment(db: Database) -> None:
    _seed(db)
    doc = payment_service.bypass_payment_gate(
        db, SA, accurate_po_id=PO_ID, reason="Owner uji bypass"
    )
    payment = gates.get_gates(db, PO_ID)["payment"]
    assert payment["state"] == gates.GATE_OPEN
    assert (payment.get("detail") or doc.get("detail") or {}).get("bypass") is True or (
        "Bypass Payment" in str(payment.get("reason") or "")
    )

    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"operational.acceptance.status": "pending"}},
    )
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, SA, accurate_po_id=PO_ID, reason="harus ditolak"
        )


def test_admin_without_bypass_grant_denied(db: Database) -> None:
    _seed(db)
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, ADMIN, accurate_po_id=PO_ID, reason="admin no grant"
        )


def test_super_admin_cross_assignment_allowed(db: Database) -> None:
    _seed(db)
    alloc = _alloc(db, "opa@eternal.local", 60)
    _prep_screen(db, alloc, 60)
    up = prod.upload_sample_draft(
        db,
        SA,
        allocation_id=alloc,
        filename="sa.png",
        content_type="image/png",
        content=PNG,
    )
    assert up["sample"]["version"] == 1
