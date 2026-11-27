"""M9.3E — manual invoice admin review + append-only audit trail."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize_invoice_receipt import normalize_sales_invoice
from app.repositories import invoice_admin_review_repository as admin_reviews
from app.repositories import invoice_repository as invoices
from app.repositories import payment_repository as payments
from app.services.audit_log import ensure_audit_indexes
from app.services.invoice_admin_review_service import (
    OUTCOME_CLOSED_WITH_EVIDENCE,
    OUTCOME_ESCALATED,
    OUTCOME_SOURCE_INCOMPLETE,
    OUTCOME_SOURCE_VERIFIED,
    OUTCOME_WAITING_ACCURATE_CORRECTION,
    SOURCE_INCOMPLETE_DETAIL,
    SOURCE_SRT_EXPENSE_ONLY,
    AdminReviewConflictError,
    classify_source_completeness,
    compute_source_fingerprint,
    submit_admin_review,
)
from app.services.invoice_finance_classification import FINANCE_VALID, classify_invoice_finance
from app.services.invoice_multi_so_allocation import ALLOC_FULLY, allocate_multi_so_invoice
from tests.scheduling_operator_fixtures import upsert_test_user

PASSWORD = "password-kuat-123"


def _incomplete_inv(inv_id: int = 70001, *, branch_id: int | None = 50) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": inv_id,
        "number": f"INV/2026/11/04/{inv_id}",
        "transDate": "04/11/2026",
        "statusName": "Lunas",
        "totalAmount": 500000,
        "subTotal": 500000,
        "customerId": 8254,
        "detailItem": [],
        "currency": {"code": "IDR"},
    }
    if branch_id is not None:
        payload["branchId"] = branch_id
    return normalize_sales_invoice(payload)


def _srt_expense(inv_id: int = 70002) -> dict[str, Any]:
    return normalize_sales_invoice(
        {
            "id": inv_id,
            "number": "SRT.2026.11.00001",
            "transDate": "03/11/2026",
            "statusName": "Lunas",
            "totalAmount": 0,
            "subTotal": 0,
            "customerId": 48351,
            "branchId": 50,
            "detailItem": [],
            "detailExpense": [{"id": 16103, "accountId": 64, "salesOrderId": None}],
        }
    )


def _standalone_valid() -> dict[str, Any]:
    return normalize_sales_invoice(
        {
            "id": 70003,
            "number": "INV/2023/06/01/000001",
            "transDate": "01/06/2023",
            "statusName": "Lunas",
            "totalAmount": 1000,
            "subTotal": 1000,
            "customerId": 1,
            "branchId": 50,
            "detailItem": [
                {
                    "id": 1,
                    "salesOrderId": None,
                    "itemId": 1,
                    "quantity": 1,
                    "totalPrice": 1000,
                }
            ],
        }
    )


def _seed_owner(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _login(client: TestClient, email: str) -> dict[str, str]:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"X-CSRF-Token": res.json()["csrf_token"]}


def _prep(db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    payments.ensure_payment_indexes(db)
    admin_reviews.ensure_admin_review_indexes(db)
    ensure_audit_indexes(db)


def test_source_incomplete_inv_classified() -> None:
    doc = _incomplete_inv()
    src = classify_source_completeness(doc)
    assert src["source_completeness"] == SOURCE_INCOMPLETE_DETAIL
    assert doc["review_required"] is True


def test_srt_expense_only_classified_separately() -> None:
    doc = _srt_expense()
    assert doc["document_kind"] == "SRT"
    assert doc["detail_expense_count"] == 1
    src = classify_source_completeness(doc)
    assert src["source_completeness"] == SOURCE_SRT_EXPENSE_ONLY


def test_authorized_submit_and_history(db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    doc = _incomplete_inv(70101)
    invoices.upsert_invoice(db, doc)
    fp = compute_source_fingerprint(doc)
    owner = db["users"].find_one({"email_normalized": "owner@eternal.local"})
    assert owner is not None
    result = submit_admin_review(
        db,
        owner,
        accurate_invoice_id="70101",
        outcome=OUTCOME_SOURCE_VERIFIED,
        reason="Diverifikasi manual terhadap Accurate print",
        evidence_ref="ticket-ABC",
        expected_source_fingerprint=fp,
        expected_admin_version=0,
        idempotency_key="m93e-submit-70101-v1",
    )
    assert result["idempotent_replay"] is False
    assert result["payment_gate_eligible"] is False
    assert result["detail"]["admin_review_status"] == OUTCOME_SOURCE_VERIFIED
    events = admin_reviews.list_events(db, "70101")
    assert len(events) == 1
    # System review untouched
    after = invoices.get_invoice(db, "70101")
    assert after is not None
    assert after["review_required"] is True


def test_idempotent_replay_no_duplicate_events(db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    doc = _incomplete_inv(70102)
    invoices.upsert_invoice(db, doc)
    owner = db["users"].find_one({"email_normalized": "owner@eternal.local"})
    fp = compute_source_fingerprint(doc)
    kwargs = dict(
        accurate_invoice_id="70102",
        outcome=OUTCOME_WAITING_ACCURATE_CORRECTION,
        reason="Menunggu koreksi Accurate detailItem",
        evidence_ref=None,
        expected_source_fingerprint=fp,
        expected_admin_version=0,
        idempotency_key="m93e-idem-70102",
    )
    a = submit_admin_review(db, owner, **kwargs)
    b = submit_admin_review(db, owner, **kwargs)
    assert b["idempotent_replay"] is True
    assert len(admin_reviews.list_events(db, "70102")) == 1
    assert a["event"]["idempotency_key"] == b["event"]["idempotency_key"]


def test_version_conflict(db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    doc = _incomplete_inv(70103)
    invoices.upsert_invoice(db, doc)
    owner = db["users"].find_one({"email_normalized": "owner@eternal.local"})
    fp = compute_source_fingerprint(doc)
    submit_admin_review(
        db,
        owner,
        accurate_invoice_id="70103",
        outcome=OUTCOME_ESCALATED,
        reason="Eskalasi ke finance lead",
        evidence_ref="esc-1",
        expected_source_fingerprint=fp,
        expected_admin_version=0,
        idempotency_key="m93e-conflict-a",
    )  # version becomes 1
    try:
        submit_admin_review(
            db,
            owner,
            accurate_invoice_id="70103",
            outcome=OUTCOME_CLOSED_WITH_EVIDENCE,
            reason="Tutup dengan bukti",
            evidence_ref="esc-2",
            expected_source_fingerprint=fp,
            expected_admin_version=0,  # stale version
            idempotency_key="m93e-conflict-b",
        )
        assert False, "expected conflict"
    except AdminReviewConflictError:
        pass
    assert len(admin_reviews.list_events(db, "70103")) == 1


def test_source_revision_marks_stale_and_keeps_history(db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    doc = _incomplete_inv(70104)
    invoices.upsert_invoice(db, doc)
    owner = db["users"].find_one({"email_normalized": "owner@eternal.local"})
    fp1 = compute_source_fingerprint(doc)
    submit_admin_review(
        db,
        owner,
        accurate_invoice_id="70104",
        outcome=OUTCOME_SOURCE_VERIFIED,
        reason="Verified against print snapshot",
        evidence_ref="ev-1",
        expected_source_fingerprint=fp1,
        expected_admin_version=0,
        idempotency_key="m93e-stale-1",
    )
    # Sync revises source (still empty lines but different total) — admin history kept.
    revised = _incomplete_inv(70104)
    revised["total_amount"] = 600000.0
    invoices.upsert_invoice(db, revised)
    assert len(admin_reviews.list_events(db, "70104")) == 1
    from app.services.invoice_admin_review_service import get_admin_review_detail

    detail = get_admin_review_detail(db, owner, "70104")
    assert detail["admin_decision_stale"] is True
    assert detail["admin_review_status"] == OUTCOME_SOURCE_VERIFIED
    # Stale fingerprint blocks reuse without reload
    try:
        submit_admin_review(
            db,
            owner,
            accurate_invoice_id="70104",
            outcome=OUTCOME_CLOSED_WITH_EVIDENCE,
            reason="Close after correction",
            evidence_ref="ev-2",
            expected_source_fingerprint=fp1,
            expected_admin_version=1,
            idempotency_key="m93e-stale-2",
        )
        assert False, "expected stale fingerprint conflict"
    except AdminReviewConflictError:
        pass


def test_sync_cannot_wipe_admin_events(db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    doc = _incomplete_inv(70105)
    invoices.upsert_invoice(db, doc)
    owner = db["users"].find_one({"email_normalized": "owner@eternal.local"})
    submit_admin_review(
        db,
        owner,
        accurate_invoice_id="70105",
        outcome=OUTCOME_SOURCE_INCOMPLETE,
        reason="Detail masih kosong di Accurate",
        evidence_ref=None,
        expected_source_fingerprint=compute_source_fingerprint(doc),
        expected_admin_version=0,
        idempotency_key="m93e-sync-1",
    )
    for _ in range(3):
        invoices.upsert_invoice(db, _incomplete_inv(70105))
    assert len(admin_reviews.list_events(db, "70105")) == 1
    assert admin_reviews.get_state(db, "70105")["outcome"] == OUTCOME_SOURCE_INCOMPLETE


def test_admin_verified_does_not_open_payment_gate(db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    doc = _incomplete_inv(70106)
    invoices.upsert_invoice(db, doc)
    owner = db["users"].find_one({"email_normalized": "owner@eternal.local"})
    result = submit_admin_review(
        db,
        owner,
        accurate_invoice_id="70106",
        outcome=OUTCOME_SOURCE_VERIFIED,
        reason="Source verified administratively",
        evidence_ref="x",
        expected_source_fingerprint=compute_source_fingerprint(doc),
        expected_admin_version=0,
        idempotency_key="m93e-gate-1",
    )
    assert result["payment_gate_eligible"] is False
    assert result["detail"]["payment_gate_eligible"] is False
    assert payments.reconcile_po_payment(db, "NO-PO")["decision"] == "closed"
    after = invoices.get_invoice(db, "70106")
    assert after["status"] == "Lunas"  # source status unchanged semantics
    assert after["review_required"] is True


def test_standalone_and_multiso_regressions() -> None:
    assert classify_invoice_finance(_standalone_valid())["finance_class"] == FINANCE_VALID
    multi = normalize_sales_invoice(
        {
            "id": 70107,
            "number": "INV/2026/11/02/000017",
            "transDate": "02/11/2026",
            "statusName": "Lunas",
            "totalAmount": 1000,
            "subTotal": 1000,
            "taxable": False,
            "cashDiscount": 0,
            "tax1Amount": 0,
            "customerId": 1,
            "branchId": 50,
            "detailItem": [
                {
                    "id": 1,
                    "salesOrderId": 10,
                    "salesOrderDetailId": 1,
                    "itemId": 1,
                    "quantity": 1,
                    "totalPrice": 400,
                },
                {
                    "id": 2,
                    "salesOrderId": 20,
                    "salesOrderDetailId": 2,
                    "itemId": 2,
                    "quantity": 1,
                    "totalPrice": 600,
                },
            ],
        }
    )
    assert allocate_multi_so_invoice(multi)["allocation_status"] == ALLOC_FULLY


def test_api_submit_history_acl(app_client: TestClient, db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    invoices.upsert_invoice(db, _incomplete_inv(70108))
    headers = _login(app_client, "owner@eternal.local")
    detail = app_client.get("/api/finance/invoice-reviews/70108", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["source_completeness"] == SOURCE_INCOMPLETE_DETAIL
    fp = body["source_fingerprint"]
    post = app_client.post(
        "/api/finance/invoice-reviews/70108",
        headers=headers,
        json={
            "outcome": OUTCOME_SOURCE_VERIFIED,
            "reason": "OK verified",
            "evidence_ref": "note-1",
            "expected_source_fingerprint": fp,
            "expected_admin_version": 0,
            "idempotency_key": "m93e-api-70108",
        },
    )
    assert post.status_code == 200, post.text
    hist = app_client.get(
        "/api/finance/invoice-reviews/70108/history", headers=headers
    )
    assert hist.status_code == 200
    assert hist.json()["count"] == 1

    upsert_test_user(
        db,
        "ops@eternal.local",
        operational_role="designer",
        display_name="Ops",
        password=PASSWORD,
        effective_module_permissions=["paint.module.view"],
        branch_ids=["50"],
    )
    ops_headers = _login(app_client, "ops@eternal.local")
    assert (
        app_client.get("/api/finance/invoice-reviews/70108", headers=ops_headers).status_code
        == 403
    )


def test_api_wrong_branch_and_missing_branch(app_client: TestClient, db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    upsert_test_user(
        db,
        "fin50@eternal.local",
        operational_role="admin",
        display_name="Fin",
        password=PASSWORD,
        effective_module_permissions=["reports.view"],
        branch_ids=["50"],
    )
    invoices.upsert_invoice(db, _incomplete_inv(70109, branch_id=150))
    invoices.upsert_invoice(db, _incomplete_inv(70110, branch_id=None))
    headers = _login(app_client, "fin50@eternal.local")
    assert app_client.get("/api/finance/invoice-reviews/70109", headers=headers).status_code == 404
    assert app_client.get("/api/finance/invoice-reviews/70110", headers=headers).status_code == 404


def test_invalid_outcome_400(app_client: TestClient, db: Database) -> None:
    _prep(db)
    _seed_owner(db)
    doc = _incomplete_inv(70111)
    invoices.upsert_invoice(db, doc)
    headers = _login(app_client, "owner@eternal.local")
    fp = compute_source_fingerprint(doc)
    bad = app_client.post(
        "/api/finance/invoice-reviews/70111",
        headers=headers,
        json={
            "outcome": "NOT_A_REAL_OUTCOME",
            "reason": "something",
            "expected_source_fingerprint": fp,
            "expected_admin_version": 0,
            "idempotency_key": "m93e-bad-outcome",
        },
    )
    assert bad.status_code == 400
