"""Checkpoint B: derivasi tugas, Design + approval customer, Gate Payment."""

from __future__ import annotations

import copy

import pytest
from pymongo.database import Database

from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import design_repository as design
from app.repositories import gate_repository as gates
from app.repositories import payment_repository as payments
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id
from app.services import design_service, payment_service, po_tasks
from app.services.po_sync import sync_sales_order_payload
from app.services.task_engine import TaskPermissionError

PO_ID = "95106"
DESIGNER = {
    "email_normalized": "designer@eternal.local",
    "role": "user",
    "operational_role": "designer",
    "effective_module_permissions": ["access.all_branches"],
}
OWNER = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
OPERATOR = {
    "email_normalized": "operator@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": ["access.all_branches"],
}


def _sync_po(db: Database, payload=None) -> dict:
    sync_sales_order_payload(db, payload or SAMPLE_SALES_ORDER_STRUCTURE)
    from tests.po_accept_helpers import mark_po_accepted
    mark_po_accepted(db)
    return get_by_accurate_id(db, PO_ID)


def _all_non_print_payload() -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    for line in payload["detailItem"]:
        line["charField1"] = "Tidak Cetak"
    return payload


def test_design_task_is_one_per_po_with_print_items(db: Database) -> None:
    po_doc = _sync_po(db)
    result = po_tasks.derive_tasks_for_po(db, po_doc)
    design_tasks = tasks.list_tasks(db, task_type="design")
    assert len(design_tasks) == 1
    assert design_tasks[0]["task_key"] == f"task:design:po:{PO_ID}"
    assert design_tasks[0]["assigned_role"] == "designer"
    assert design_tasks[0]["context"]["print_line_count"] == 1
    assert result["scheduling_unlocked"] is False


def test_derivation_is_idempotent(db: Database) -> None:
    po_doc = _sync_po(db)
    po_tasks.derive_tasks_for_po(db, po_doc)
    po_tasks.derive_tasks_for_po(db, po_doc)
    po_tasks.derive_tasks_for_po(db, po_doc)
    assert len(tasks.list_tasks(db, task_type="design")) == 1


def test_all_non_print_po_skips_design_and_opens_gate(db: Database) -> None:
    po_doc = _sync_po(db, _all_non_print_payload())
    result = po_tasks.derive_tasks_for_po(db, po_doc)
    assert tasks.list_tasks(db, task_type="design") == []
    assert "design:semua-item-non-cetak" in result["skipped"]
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_OPEN


def test_scheduling_blocked_until_both_gates_open(db: Database) -> None:
    po_doc = _sync_po(db)
    po_tasks.derive_tasks_for_po(db, po_doc)
    assert tasks.list_tasks(db, task_type="penjadwalan") == []

    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="uji"
    )
    po_tasks.derive_tasks_for_po(db, po_doc)
    assert tasks.list_tasks(db, task_type="penjadwalan") == []

    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="uji"
    )
    po_tasks.derive_tasks_for_po(db, po_doc)
    sched = tasks.list_tasks(db, task_type="penjadwalan")
    assert len(sched) == 1
    assert sched[0]["ref"]["accurate_line_id"] == "106318"
    assert sched[0]["qty_target"] == 1000


def test_design_flow_upload_confirm_link_and_single_signature(db: Database) -> None:
    po_doc = _sync_po(db)
    po_tasks.derive_tasks_for_po(db, po_doc)

    submitted = design_service.submit_design(
        db,
        DESIGNER,
        accurate_po_id=PO_ID,
        po_no=str(po_doc["po_no"]),
        filename="design.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.4 desain uji",
    )
    assert submitted["submission"]["version"] == 1
    assert submitted["submission"]["confirmed_at"] is None

    # Link hanya boleh dibuat setelah Designer konfirmasi.
    with pytest.raises(design_service.DesignStateError):
        design_service.create_customer_link(db, DESIGNER, accurate_po_id=PO_ID)

    design_service.confirm_design(db, DESIGNER, accurate_po_id=PO_ID, version=1)
    link = design_service.create_customer_link(db, DESIGNER, accurate_po_id=PO_ID)
    assert link["token"]
    assert "token_hash" not in link["link"]

    context = design_service.public_approval_context(db, link["token"], po_doc)
    assert context["design_version"] == 1
    assert len(context["items"]) == 1
    assert context["already_approved"] is False

    result = design_service.approve_design(
        db,
        raw_token=link["token"],
        signer_name="Pelanggan Uji",
        signature_png=b"\x89PNG signature",
    )
    assert result["duplicate"] is False
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_OPEN
    assert tasks.get_task(db, f"task:design:po:{PO_ID}")["status"] == tasks.TASK_STATUS_DONE


def test_repeat_approval_does_not_duplicate(db: Database) -> None:
    po_doc = _sync_po(db)
    design_service.submit_design(
        db,
        DESIGNER,
        accurate_po_id=PO_ID,
        po_no=str(po_doc["po_no"]),
        filename="design.pdf",
        content_type="application/pdf",
        content=b"desain",
    )
    design_service.confirm_design(db, DESIGNER, accurate_po_id=PO_ID, version=1)
    link = design_service.create_customer_link(db, DESIGNER, accurate_po_id=PO_ID)

    design_service.approve_design(
        db, raw_token=link["token"], signer_name="A", signature_png=b"sig"
    )
    again = design_service.approve_design(
        db, raw_token=link["token"], signer_name="B", signature_png=b"sig2"
    )
    assert again["duplicate"] is True
    assert db["design_approvals"].count_documents({"accurate_po_id": PO_ID}) == 1


def test_design_revision_keeps_history(db: Database) -> None:
    po_doc = _sync_po(db)
    for i in (1, 2):
        design_service.submit_design(
            db,
            DESIGNER,
            accurate_po_id=PO_ID,
            po_no=str(po_doc["po_no"]),
            filename=f"design-v{i}.pdf",
            content_type="application/pdf",
            content=f"desain {i}".encode(),
        )
    versions = [s["version"] for s in design.list_submissions(db, PO_ID)]
    assert versions == [1, 2]
    assert design.latest_submission(db, PO_ID)["filename"] == "design-v2.pdf"


def test_upload_requires_designer_permission(db: Database) -> None:
    po_doc = _sync_po(db)
    with pytest.raises(TaskPermissionError):
        design_service.submit_design(
            db,
            OPERATOR,
            accurate_po_id=PO_ID,
            po_no=str(po_doc["po_no"]),
            filename="x.pdf",
            content_type="application/pdf",
            content=b"x",
        )


def test_invalid_token_rejected(db: Database) -> None:
    _sync_po(db)
    with pytest.raises(LookupError):
        design_service.approve_design(
            db, raw_token="token-palsu", signer_name="X", signature_png=b"sig"
        )


def test_payment_normal_case_opens_gate_without_threshold(db: Database) -> None:
    po_doc = _sync_po(db)
    result = payment_service.ingest_receipts(
        db,
        OWNER,
        accurate_po_id=PO_ID,
        po_no=str(po_doc["po_no"]),
        receipts=[
            {"id": "RC-1", "invoice_no": "INV-1", "amount": 1000, "receipt_date": "18/11/2026"}
        ],
    )
    assert result["gates"]["payment"]["state"] == gates.GATE_OPEN
    # Nilai jauh di bawah 50% tetap membuka gate (aturan terkunci).
    assert payments.reconcile_po_payment(db, PO_ID)["total_amount"] == 1000


def test_payment_ingest_is_idempotent(db: Database) -> None:
    po_doc = _sync_po(db)
    payload = [{"id": "RC-9", "invoice_no": "INV-9", "amount": 500}]
    payment_service.ingest_receipts(
        db, OWNER, accurate_po_id=PO_ID, po_no=str(po_doc["po_no"]), receipts=payload
    )
    payment_service.ingest_receipts(
        db, OWNER, accurate_po_id=PO_ID, po_no=str(po_doc["po_no"]), receipts=payload
    )
    assert len(payments.list_receipts(db, PO_ID)) == 1


def test_ambiguous_payment_goes_to_review_not_open(db: Database) -> None:
    po_doc = _sync_po(db)
    cases = [
        ([{"id": "RC-N", "invoice_no": "INV-1", "amount": -250}], "negatif"),
        ([{"id": "RC-M", "invoice_no": "", "amount": 100}], "faktur"),
        (
            [
                {"id": "RC-A", "invoice_no": "INV-1", "amount": 100},
                {"id": "RC-B", "invoice_no": "INV-2", "amount": 200},
            ],
            "multi-faktur",
        ),
    ]
    for receipts, _label in cases:
        db["accurate_payment_receipts"].delete_many({})
        db["po_gates"].delete_many({})
        payment_service.ingest_receipts(
            db,
            OWNER,
            accurate_po_id=PO_ID,
            po_no=str(po_doc["po_no"]),
            receipts=receipts,
        )
        gate = gates.get_gates(db, PO_ID)["payment"]
        assert gate["state"] == gates.GATE_REVIEW
        assert gate["reason"]
        assert gates.both_gates_open(db, PO_ID) is False


def test_bypass_requires_permission_and_is_audited(db: Database) -> None:
    _sync_po(db)
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, OPERATOR, accurate_po_id=PO_ID, reason="coba bypass"
        )
    # Super Admin lolos via can_act; bukan hardcode role Direktur/Holding.
    doc = payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="Disetujui Owner tertulis"
    )
    assert doc["payment"]["state"] == gates.GATE_OPEN
    assert db["audit_logs"].count_documents({"action": "payment.gate.bypass"}) == 1


def test_bypass_via_master_grant_not_hardcoded_role(db: Database) -> None:
    """RULE 04 — grant Master payment-bypass/approve membuka bypass untuk role operasional."""
    from app.repositories import master_repository as master

    _sync_po(db)
    head = {
        "email_normalized": "kp@eternal.local",
        "role": "user",
        "operational_role": "production-head",
        "effective_module_permissions": ["access.all_branches"],
}
    with pytest.raises(payment_service.PaymentPermissionError):
        payment_service.bypass_payment_gate(
            db, head, accurate_po_id=PO_ID, reason="tanpa grant"
        )
    master.set_role_actions(
        db,
        role="production-head",
        task_type="payment-bypass",
        actions=["approve"],
    )
    with pytest.raises(ValueError, match="Alasan"):
        payment_service.bypass_payment_gate(
            db, head, accurate_po_id=PO_ID, reason="   "
        )
    doc = payment_service.bypass_payment_gate(
        db, head, accurate_po_id=PO_ID, reason="Izin Master untuk KP"
    )
    assert doc["payment"]["state"] == gates.GATE_OPEN
    assert (doc["payment"].get("detail") or {}).get("bypass") is True
    assert db["audit_logs"].count_documents(
        {"action": "payment.gate.bypass", "actor_email": "kp@eternal.local"}
    ) == 1


def test_bypass_is_not_reverted_by_later_reconciliation(db: Database) -> None:
    po_doc = _sync_po(db)
    payment_service.bypass_payment_gate(
        db, OWNER, accurate_po_id=PO_ID, reason="Bypass resmi"
    )
    payment_service.ingest_receipts(
        db,
        OWNER,
        accurate_po_id=PO_ID,
        po_no=str(po_doc["po_no"]),
        receipts=[{"id": "RC-X", "invoice_no": "", "amount": 10}],
    )
    assert gates.get_gates(db, PO_ID)["payment"]["state"] == gates.GATE_OPEN


def test_gate_history_is_append_only(db: Database) -> None:
    _sync_po(db)
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_REVIEW, reason="r1"
    )
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="r2"
    )
    history = gates.gate_history(db, PO_ID)
    assert [h["state"] for h in history] == [gates.GATE_REVIEW, gates.GATE_OPEN]
