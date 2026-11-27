"""M9.3C — standalone finance classification (derived, fail-closed)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize_invoice_receipt import (
    invoice_material_equal,
    normalize_sales_invoice,
    normalize_sales_receipt,
)
from app.repositories import invoice_repository as invoices
from app.repositories import payment_repository as payments
from app.services.invoice_finance_classification import (
    FINANCE_EXCLUDED_SRT_EXPENSE,
    FINANCE_INCOMPLETE_DETAIL,
    FINANCE_NOT_STANDALONE,
    FINANCE_UNKNOWN,
    FINANCE_VALID,
    PO_ASSOCIATION_MULTI,
    PO_ASSOCIATION_NONE,
    PO_ASSOCIATION_SINGLE,
    classify_invoice_finance,
)
from tests.scheduling_operator_fixtures import upsert_test_user

PASSWORD = "password-kuat-123"


def _standalone_payload(
    inv_id: int = 50001,
    *,
    number: str = "INV/2026/11/01/000099",
    customer_id: int | None = 7701,
    branch_id: int | str | None = 50,
    with_lines: bool = True,
    so_id: int | None = None,
    second_so: int | None = None,
    expense: bool = False,
    total: float = 1500.0,
) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    if with_lines:
        lines.append(
            {
                "id": 1,
                "salesOrderId": so_id,
                "salesOrderDetailId": 10 if so_id else None,
                "itemId": 100,
                "quantity": 10,
            }
        )
        if second_so is not None:
            lines.append(
                {
                    "id": 2,
                    "salesOrderId": second_so,
                    "salesOrderDetailId": 11,
                    "itemId": 101,
                    "quantity": 5,
                }
            )
    payload: dict[str, Any] = {
        "id": inv_id,
        "number": number,
        "transDate": "01/11/2026",
        "statusName": "Lunas",
        "totalAmount": total,
        "subTotal": total,
        "salesOrder": False,
        "detailItem": lines,
        "currency": {"code": "IDR"},
    }
    if customer_id is not None:
        payload["customerId"] = customer_id
    if branch_id is not None:
        payload["branchId"] = branch_id
    if expense:
        payload["detailExpense"] = [{"id": 9, "accountId": 64, "salesOrderId": None}]
    return payload


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


def test_verified_standalone_is_finance_valid() -> None:
    doc = normalize_sales_invoice(_standalone_payload())
    result = classify_invoice_finance(doc)
    assert result["finance_class"] == FINANCE_VALID
    assert result["po_association"] == PO_ASSOCIATION_NONE
    assert result["payment_gate_eligible"] is False
    assert result["payment_gate_label"] == "Tidak Berlaku"
    assert result["finance_label"] == "Valid"
    assert result["po_association_label"] == "Tanpa SO"
    assert doc["review_required"] is True
    assert doc["accurate_po_ids"] == []
    assert doc["primary_accurate_po_id"] == ""


def test_finance_valid_does_not_imply_paid_or_synthetic_po() -> None:
    doc = normalize_sales_invoice(_standalone_payload(total=2500.0))
    doc["status"] = "OUTSTANDING"
    result = classify_invoice_finance(doc)
    assert result["finance_class"] == FINANCE_VALID
    assert result["status"] == "OUTSTANDING"
    assert doc["accurate_po_ids"] == []
    assert not doc["primary_accurate_po_id"]


def test_null_so_without_lines_is_incomplete_not_valid() -> None:
    doc = normalize_sales_invoice(_standalone_payload(with_lines=False))
    result = classify_invoice_finance(doc)
    assert result["finance_class"] == FINANCE_INCOMPLETE_DETAIL
    assert result["payment_gate_eligible"] is False


def test_empty_detail_inv_not_automatically_standalone() -> None:
    doc = normalize_sales_invoice(
        _standalone_payload(number="INV/2026/11/04/000001", with_lines=False, total=500000)
    )
    assert doc["document_kind"] == "INV"
    assert classify_invoice_finance(doc)["finance_class"] == FINANCE_INCOMPLETE_DETAIL


def test_srt_expense_only_excluded() -> None:
    doc = normalize_sales_invoice(
        _standalone_payload(
            number="SRT.2026.11.00001",
            with_lines=False,
            expense=True,
            total=0.0,
        )
    )
    assert doc["document_kind"] == "SRT"
    assert doc["has_detail_expense"] is True
    result = classify_invoice_finance(doc)
    assert result["finance_class"] == FINANCE_EXCLUDED_SRT_EXPENSE
    assert result["payment_gate_eligible"] is False


def test_multi_so_not_standalone() -> None:
    doc = normalize_sales_invoice(_standalone_payload(so_id=101, second_so=202))
    result = classify_invoice_finance(doc)
    assert result["finance_class"] == FINANCE_NOT_STANDALONE
    assert result["po_association"] == PO_ASSOCIATION_MULTI
    assert doc["review_required"] is True
    assert doc["primary_accurate_po_id"] == ""


def test_single_so_not_standalone() -> None:
    doc = normalize_sales_invoice(_standalone_payload(so_id=303))
    result = classify_invoice_finance(doc)
    assert result["finance_class"] == FINANCE_NOT_STANDALONE
    assert result["po_association"] == PO_ASSOCIATION_SINGLE
    assert doc["primary_accurate_po_id"] == "303"


def test_missing_customer_fail_closed() -> None:
    doc = normalize_sales_invoice(_standalone_payload(customer_id=None))
    assert doc.get("customer_id") is None
    assert classify_invoice_finance(doc)["finance_class"] == FINANCE_UNKNOWN


def test_missing_branch_fail_closed() -> None:
    doc = normalize_sales_invoice(_standalone_payload(branch_id=None))
    assert doc.get("branch_id") is None
    assert classify_invoice_finance(doc)["finance_class"] == FINANCE_UNKNOWN


def test_original_review_preserved_on_classify(db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    doc = normalize_sales_invoice(_standalone_payload(inv_id=50011))
    assert doc["review_required"] is True
    reason = doc["review_reason"]
    invoices.upsert_invoice(db, doc)
    before = invoices.get_invoice(db, "50011")
    _ = classify_invoice_finance(before)
    after = invoices.get_invoice(db, "50011")
    assert after is not None
    assert after["review_required"] is True
    assert after["review_reason"] == reason


def test_sync_revision_recomputes_deterministically() -> None:
    a = normalize_sales_invoice(_standalone_payload(inv_id=50021))
    b = normalize_sales_invoice(_standalone_payload(inv_id=50021))
    assert invoice_material_equal(a, b)
    assert classify_invoice_finance(a) == classify_invoice_finance(b)


def test_upsert_does_not_duplicate_identity(db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    doc = normalize_sales_invoice(_standalone_payload(inv_id=50031))
    invoices.upsert_invoice(db, doc)
    invoices.upsert_invoice(db, doc)
    count = db[invoices.INVOICES_COLLECTION].count_documents(
        {"accurate_invoice_id": "50031"}
    )
    assert count == 1


def test_finance_valid_does_not_open_payment_gate(db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    payments.ensure_payment_indexes(db)
    doc = normalize_sales_invoice(_standalone_payload(inv_id=50041))
    invoices.upsert_invoice(db, doc)
    classified = classify_invoice_finance(doc)
    assert classified["finance_class"] == FINANCE_VALID
    assert classified["payment_gate_eligible"] is False
    decision = payments.reconcile_po_payment(db, "NO-SUCH-PO")
    assert decision["decision"] == "closed"


def test_multi_invoice_receipt_hold_unchanged() -> None:
    normalized = normalize_sales_receipt(
        {
            "id": 9001,
            "number": "SR/9001",
            "transDate": "01/11/2026",
            "totalPayment": 100.0,
            "detailInvoice": [
                {
                    "id": 1,
                    "invoiceId": 1,
                    "paymentAmount": 50.0,
                    "invoice": {"id": 1, "number": "A"},
                },
                {
                    "id": 2,
                    "invoiceId": 2,
                    "paymentAmount": 50.0,
                    "invoice": {"id": 2, "number": "B"},
                },
            ],
        }
    )
    assert normalized["review_required"] is True
    reason = (normalized.get("review_reason") or "").lower()
    assert "lebih dari satu faktur" in reason or "multi-faktur" in reason


def test_finance_api_list_and_get_standalone(app_client: TestClient, db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    _seed_owner(db)
    doc = normalize_sales_invoice(_standalone_payload(inv_id=50061, branch_id=50))
    invoices.upsert_invoice(db, doc)
    headers = _login(app_client, "owner@eternal.local")
    listed = app_client.get(
        "/api/finance/invoices?finance_class=FINANCE_VALID",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["derived"] is True
    assert body["payment_gate_unchanged"] is True
    ids = {row["accurate_invoice_id"] for row in body["items"]}
    assert "50061" in ids
    row = next(r for r in body["items"] if r["accurate_invoice_id"] == "50061")
    assert row["finance_class"] == FINANCE_VALID
    assert row["po_association"] == PO_ASSOCIATION_NONE
    assert row["review_required"] is True
    assert row["payment_gate_eligible"] is False
    assert "approval" not in body

    one = app_client.get("/api/finance/invoices/50061", headers=headers)
    assert one.status_code == 200
    assert one.json()["finance_class"] == FINANCE_VALID


def test_finance_api_unauthorized_role_forbidden(
    app_client: TestClient, db: Database
) -> None:
    invoices.ensure_invoice_indexes(db)
    _seed_owner(db)
    upsert_test_user(
        db,
        "ops-only@eternal.local",
        operational_role="designer",
        display_name="Designer",
        password=PASSWORD,
        # Non-empty list required: [] is falsy and fixture defaults to all_branches.
        effective_module_permissions=["paint.module.view"],
        branch_ids=["50"],
    )
    doc = normalize_sales_invoice(_standalone_payload(inv_id=50065, branch_id=50))
    invoices.upsert_invoice(db, doc)
    headers = _login(app_client, "ops-only@eternal.local")
    resp = app_client.get("/api/finance/invoices/50065", headers=headers)
    assert resp.status_code == 403


def test_finance_api_branch_acl_denies_other_branch(
    app_client: TestClient, db: Database
) -> None:
    invoices.ensure_invoice_indexes(db)
    _seed_owner(db)
    upsert_test_user(
        db,
        "finance-50@eternal.local",
        operational_role="admin",
        display_name="Finance 50",
        password=PASSWORD,
        effective_module_permissions=["reports.view"],
        branch_ids=["50"],
    )
    doc = normalize_sales_invoice(_standalone_payload(inv_id=50071, branch_id=150))
    invoices.upsert_invoice(db, doc)
    headers = _login(app_client, "finance-50@eternal.local")
    resp = app_client.get("/api/finance/invoices/50071", headers=headers)
    assert resp.status_code == 404


def test_finance_api_missing_branch_not_exposed_cross_branch(
    app_client: TestClient, db: Database
) -> None:
    invoices.ensure_invoice_indexes(db)
    _seed_owner(db)
    upsert_test_user(
        db,
        "finance-50b@eternal.local",
        operational_role="admin",
        display_name="Finance 50b",
        password=PASSWORD,
        effective_module_permissions=["reports.view"],
        branch_ids=["50"],
    )
    doc = normalize_sales_invoice(_standalone_payload(inv_id=50081, branch_id=None))
    invoices.upsert_invoice(db, doc)
    headers = _login(app_client, "finance-50b@eternal.local")
    resp = app_client.get("/api/finance/invoices/50081", headers=headers)
    assert resp.status_code == 404
    listed = app_client.get("/api/finance/invoices", headers=headers)
    assert listed.status_code == 200
    assert "50081" not in {
        row["accurate_invoice_id"] for row in listed.json()["items"]
    }
