"""M9.3D — verified Multi-SO invoice allocation (Decimal, fail-closed)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize_invoice_receipt import (
    normalize_sales_invoice,
    normalize_sales_receipt,
)
from app.repositories import invoice_repository as invoices
from app.repositories import payment_repository as payments
from app.repositories.po_repository import upsert_sales_order
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.services.invoice_finance_classification import (
    FINANCE_NOT_STANDALONE,
    FINANCE_VALID,
    classify_invoice_finance,
    finance_invoice_view,
)
from app.services.invoice_multi_so_allocation import (
    ALLOC_EXCLUDED,
    ALLOC_FULLY,
    ALLOC_NOT_MULTI,
    ALLOC_PARTIAL,
    ALLOC_UNRESOLVED,
    ALLOC_UNSUPPORTED_TAX_DISCOUNT,
    allocate_multi_so_invoice,
)
from tests.scheduling_operator_fixtures import upsert_test_user

PASSWORD = "password-kuat-123"

# Audited Accurate fixture IDs (validation only — not a runtime allowlist).
AUDITED_MULTI_SO_FIXTURES: dict[str, dict[str, Any]] = {
    "118766": {
        "number": "INV/2026/11/02/000017",
        "customerId": 30501,
        "branchId": 50,
        "total": "3872000.00",
        "lines": [
            (124132, 89867, 97935, "442500.00"),
            (124133, 89867, 97936, "160000.00"),
            (124134, 93905, 104310, "1500000.00"),
            (124135, 93905, 104311, "640000.00"),
            (124136, 94059, 104512, "255000.00"),
            (124137, 94059, 104513, "172500.00"),
            (124138, 94059, 104514, "702000.00"),
        ],
        "expected": {"89867": "602500.00", "93905": "2140000.00", "94059": "1129500.00"},
    },
    "118909": {
        "number": "INV/2026/11/06/000018",
        "customerId": 47700,
        "branchId": 50,
        "total": "4585000.00",
        "lines": [
            (125083, 93354, 103506, "1050000.00"),
            (125084, 93354, 103507, "400000.00"),
            (124428, 94102, 104722, "1635000.00"),
            (124429, 94102, 104723, "640000.00"),
            (124430, 94102, 104724, "860000.00"),
        ],
        "expected": {"93354": "1450000.00", "94102": "3135000.00"},
    },
    "118961": {
        "number": "INV/2026/11/06/000019",
        "customerId": 24950,
        "branchId": 50,
        "total": "2970000.00",
        "lines": [
            (124501, 93319, 103401, "990000.00"),
            (124502, 92201, 101101, "990000.00"),
            (124503, 94151, 104801, "990000.00"),
        ],
        "expected": {"93319": "990000.00", "92201": "990000.00", "94151": "990000.00"},
    },
    "119466": {
        "number": "INV/2026/11/11/000006",
        "customerId": 16450,
        "branchId": 50,
        "total": "3947500.00",
        "lines": [
            (124928, 93854, 105611, "750000.00"),
            (124929, 93854, 105569, "320000.00"),
            (124930, 93807, 105609, "750000.00"),
            (124931, 93807, 105505, "320000.00"),
            (124932, 93125, 103314, "1327500.00"),
            (124933, 93125, 105607, "480000.00"),
        ],
        "expected": {"93854": "1070000.00", "93807": "1070000.00", "93125": "1807500.00"},
    },
    "119751": {
        "number": "INV/2026/11/14/000026",
        "customerId": 32300,
        "branchId": 50,
        "total": "1053000.00",
        "lines": [
            (125301, 93084, 103236, "351000.00"),
            (125302, 91580, 100462, "351000.00"),
            (125303, 92300, 101552, "351000.00"),
        ],
        "expected": {"93084": "351000.00", "91580": "351000.00", "92300": "351000.00"},
    },
    "120618": {
        "number": "INV/2026/11/24/000006",
        "customerId": 30501,
        "branchId": 50,
        "total": "1022500.00",
        "lines": [
            (126236, 94027, 104785, "255000.00"),
            (126237, 94027, 104786, "172500.00"),
            (126238, 95704, 106972, "435000.00"),
            (126239, 95704, 106974, "160000.00"),
        ],
        "expected": {"94027": "427500.00", "95704": "595000.00"},
    },
}


def _payload_from_fixture(inv_id: str, *, taxable: bool = False, cash_discount: float = 0.0) -> dict[str, Any]:
    fx = AUDITED_MULTI_SO_FIXTURES[inv_id]
    detail = []
    for line_id, so, sod, total in fx["lines"]:
        detail.append(
            {
                "id": line_id,
                "salesOrderId": so,
                "salesOrderDetailId": sod,
                "itemId": 100,
                "quantity": 1,
                "unitPrice": float(Decimal(total)),
                "itemCashDiscount": 0,
                "totalPrice": float(Decimal(total)),
            }
        )
    return {
        "id": int(inv_id),
        "number": fx["number"],
        "transDate": "01/11/2026",
        "statusName": "Lunas",
        "totalAmount": float(Decimal(fx["total"])),
        "subTotal": float(Decimal(fx["total"])),
        "taxable": taxable,
        "cashDiscount": cash_discount,
        "tax1Amount": 0.0,
        "customerId": fx["customerId"],
        "branchId": fx["branchId"],
        "currency": {"code": "IDR"},
        "detailItem": detail,
        "salesOrder": False,
    }


def _seed_parents(db: Database, so_ids: list[str], *, branch_id: str = "50") -> None:
    import copy

    for sid in so_ids:
        raw = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
        raw["id"] = int(sid)
        raw["number"] = f"PO/{sid}"
        po = normalize_sales_order(raw)
        upsert_sales_order(db, po, branch_id_on_insert=branch_id)
        db["accurate_sales_orders"].update_one(
            {"accurate_id": str(sid)},
            {"$set": {"branch_id": branch_id, "customer_id": None}},
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


def test_two_so_deterministic_allocation() -> None:
    doc = normalize_sales_invoice(_payload_from_fixture("118909"))
    result = allocate_multi_so_invoice(doc, parent_so_docs=None)
    assert result["allocation_status"] == ALLOC_FULLY
    assert result["reconciliation_difference"] == "0.00"
    by = {r["accurate_so_id"]: r["allocated_amount"] for r in result["allocations_by_so"]}
    assert by == AUDITED_MULTI_SO_FIXTURES["118909"]["expected"]
    assert result["payment_allocation"] is False
    assert result["payment_gate_eligible"] is False


def test_three_or_more_so_grouping() -> None:
    doc = normalize_sales_invoice(_payload_from_fixture("118766"))
    result = allocate_multi_so_invoice(doc)
    assert len(result["allocations_by_so"]) == 3
    assert result["allocation_status"] == ALLOC_FULLY


def test_all_six_audited_fixtures_reconcile_zero() -> None:
    for inv_id, fx in AUDITED_MULTI_SO_FIXTURES.items():
        doc = normalize_sales_invoice(_payload_from_fixture(inv_id))
        result = allocate_multi_so_invoice(doc)
        assert result["allocation_status"] == ALLOC_FULLY, inv_id
        assert result["reconciliation_difference"] == "0.00", inv_id
        by = {r["accurate_so_id"]: r["allocated_amount"] for r in result["allocations_by_so"]}
        assert by == fx["expected"], inv_id


def test_allocation_idempotent_repeat() -> None:
    doc = normalize_sales_invoice(_payload_from_fixture("120618"))
    a = allocate_multi_so_invoice(doc)
    b = allocate_multi_so_invoice(doc)
    assert a == b


def test_source_revision_recalculates(db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    doc = normalize_sales_invoice(_payload_from_fixture("119751"))
    invoices.upsert_invoice(db, doc)
    first = allocate_multi_so_invoice(invoices.get_invoice(db, "119751"))
    # Revise one line total (source change) — must recompute, not double-count.
    payload = _payload_from_fixture("119751")
    payload["detailItem"][0]["totalPrice"] = 300000.0
    payload["totalAmount"] = 1002000.0
    payload["subTotal"] = 1002000.0
    revised = normalize_sales_invoice(payload)
    invoices.upsert_invoice(db, revised)
    second = allocate_multi_so_invoice(invoices.get_invoice(db, "119751"))
    assert second["allocations_by_so"] != first["allocations_by_so"]
    assert db[invoices.INVOICES_COLLECTION].count_documents({"accurate_invoice_id": "119751"}) == 1


def test_missing_so_on_line_partial_not_guessed() -> None:
    payload = _payload_from_fixture("120618")
    payload["detailItem"][0]["salesOrderId"] = None
    doc = normalize_sales_invoice(payload)
    # accurate_po_ids still multi from other lines
    result = allocate_multi_so_invoice(doc)
    assert result["allocation_status"] in {ALLOC_PARTIAL, ALLOC_UNRESOLVED}
    assert Decimal(result["unallocated_total"]) > 0
    # Remainder not dumped onto a primary SO
    assert doc["primary_accurate_po_id"] == ""


def test_missing_line_total_price_fail_closed() -> None:
    payload = _payload_from_fixture("120618")
    del payload["detailItem"][0]["totalPrice"]
    doc = normalize_sales_invoice(payload)
    # line total_price None → unresolved contribution
    result = allocate_multi_so_invoice(doc)
    assert result["allocation_status"] in {ALLOC_PARTIAL, ALLOC_UNRESOLVED}


def test_missing_parent_so_fail_closed(db: Database) -> None:
    doc = normalize_sales_invoice(_payload_from_fixture("120618"))
    # Provide empty parent map → missing parents
    result = allocate_multi_so_invoice(doc, parent_so_docs={})
    assert result["allocation_status"] == ALLOC_UNRESOLVED
    assert any("missing locally" in r for r in result["reasons"])


def test_branch_mismatch_fail_closed(db: Database) -> None:
    doc = normalize_sales_invoice(_payload_from_fixture("120618"))
    _seed_parents(db, ["94027", "95704"], branch_id="999")
    parents = {
        "94027": db["accurate_sales_orders"].find_one({"accurate_id": "94027"}),
        "95704": db["accurate_sales_orders"].find_one({"accurate_id": "95704"}),
    }
    result = allocate_multi_so_invoice(doc, parent_so_docs=parents)
    assert result["allocation_status"] == ALLOC_UNRESOLVED
    assert any("branch mismatch" in r for r in result["reasons"])


def test_decimal_precision_no_float_drift() -> None:
    doc = normalize_sales_invoice(_payload_from_fixture("118766"))
    result = allocate_multi_so_invoice(doc)
    assert result["allocated_total"] == "3872000.00"
    assert "." in result["allocated_total"]
    # Ensure string money not binary float repr
    assert "e" not in result["allocated_total"].lower()


def test_taxable_invoice_hold() -> None:
    doc = normalize_sales_invoice(_payload_from_fixture("120618", taxable=True))
    result = allocate_multi_so_invoice(doc)
    assert result["allocation_status"] == ALLOC_UNSUPPORTED_TAX_DISCOUNT
    assert result["payment_gate_eligible"] is False


def test_cash_discount_hold() -> None:
    doc = normalize_sales_invoice(_payload_from_fixture("120618", cash_discount=100.0))
    result = allocate_multi_so_invoice(doc)
    assert result["allocation_status"] == ALLOC_UNSUPPORTED_TAX_DISCOUNT


def test_reconciliation_mismatch_review() -> None:
    payload = _payload_from_fixture("120618")
    payload["totalAmount"] = 999999.0
    doc = normalize_sales_invoice(payload)
    result = allocate_multi_so_invoice(doc)
    assert result["allocation_status"] == ALLOC_UNRESOLVED
    assert result["reconciliation_difference"] != "0.00"


def test_standalone_m93c_unchanged() -> None:
    payload = {
        "id": 60001,
        "number": "INV/2026/11/01/000001",
        "transDate": "01/11/2026",
        "statusName": "OUTSTANDING",
        "totalAmount": 1000,
        "subTotal": 1000,
        "customerId": 1,
        "branchId": 50,
        "detailItem": [
            {"id": 1, "salesOrderId": None, "itemId": 1, "quantity": 1, "totalPrice": 1000}
        ],
    }
    doc = normalize_sales_invoice(payload)
    assert classify_invoice_finance(doc)["finance_class"] == FINANCE_VALID
    alloc = allocate_multi_so_invoice(doc)
    assert alloc["allocation_status"] == ALLOC_NOT_MULTI


def test_srt_excluded() -> None:
    doc = normalize_sales_invoice(
        {
            "id": 60002,
            "number": "SRT.2026.11.00001",
            "transDate": "01/11/2026",
            "statusName": "Lunas",
            "totalAmount": 0,
            "subTotal": 0,
            "customerId": 1,
            "branchId": 50,
            "detailItem": [],
            "detailExpense": [{"id": 1, "accountId": 64}],
        }
    )
    assert allocate_multi_so_invoice(doc)["allocation_status"] == ALLOC_EXCLUDED


def test_empty_detail_excluded() -> None:
    doc = normalize_sales_invoice(
        {
            "id": 60003,
            "number": "INV/2026/11/04/000001",
            "transDate": "01/11/2026",
            "statusName": "Lunas",
            "totalAmount": 500000,
            "subTotal": 500000,
            "customerId": 1,
            "branchId": 50,
            "detailItem": [],
        }
    )
    assert allocate_multi_so_invoice(doc)["allocation_status"] == ALLOC_EXCLUDED


def test_review_preserved_and_gate_closed(db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    payments.ensure_payment_indexes(db)
    doc = normalize_sales_invoice(_payload_from_fixture("118766"))
    assert doc["review_required"] is True
    reason = doc["review_reason"]
    _seed_parents(db, ["89867", "93905", "94059"], branch_id="50")
    invoices.upsert_invoice(db, doc)
    view = finance_invoice_view(invoices.get_invoice(db, "118766"), db=db)
    assert view["finance_class"] == FINANCE_NOT_STANDALONE
    assert view["allocation_status"] == ALLOC_FULLY
    assert view["payment_gate_eligible"] is False
    assert view["payment_allocation"] is False
    after = invoices.get_invoice(db, "118766")
    assert after["review_required"] is True
    assert after["review_reason"] == reason
    assert payments.reconcile_po_payment(db, "89867")["decision"] == "closed"


def test_multi_invoice_receipt_hold_intact() -> None:
    normalized = normalize_sales_receipt(
        {
            "id": 9101,
            "number": "SR/9101",
            "transDate": "01/11/2026",
            "totalPayment": 100.0,
            "detailInvoice": [
                {"id": 1, "invoiceId": 1, "paymentAmount": 50.0, "invoice": {"id": 1, "number": "A"}},
                {"id": 2, "invoiceId": 2, "paymentAmount": 50.0, "invoice": {"id": 2, "number": "B"}},
            ],
        }
    )
    assert normalized["review_required"] is True


def test_finance_api_multi_so_allocation(app_client: TestClient, db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    _seed_owner(db)
    doc = normalize_sales_invoice(_payload_from_fixture("120618"))
    _seed_parents(db, ["94027", "95704"], branch_id="50")
    invoices.upsert_invoice(db, doc)
    headers = _login(app_client, "owner@eternal.local")
    resp = app_client.get("/api/finance/invoices/120618", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["allocation_status"] == ALLOC_FULLY
    assert body["reconciliation_difference"] == "0.00"
    assert body["payment_gate_eligible"] is False
    assert body["payment_allocation"] is False
    assert body["review_required"] is True


def test_finance_api_unauthorized(app_client: TestClient, db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    _seed_owner(db)
    upsert_test_user(
        db,
        "ops@eternal.local",
        operational_role="designer",
        display_name="Ops",
        password=PASSWORD,
        effective_module_permissions=["paint.module.view"],
        branch_ids=["50"],
    )
    invoices.upsert_invoice(db, normalize_sales_invoice(_payload_from_fixture("120618")))
    headers = _login(app_client, "ops@eternal.local")
    assert app_client.get("/api/finance/invoices/120618", headers=headers).status_code == 403


def test_finance_api_wrong_branch(app_client: TestClient, db: Database) -> None:
    invoices.ensure_invoice_indexes(db)
    _seed_owner(db)
    upsert_test_user(
        db,
        "fin50@eternal.local",
        operational_role="admin",
        display_name="Fin50",
        password=PASSWORD,
        effective_module_permissions=["reports.view"],
        branch_ids=["50"],
    )
    doc = normalize_sales_invoice(_payload_from_fixture("120618"))
    doc["branch_id"] = "150"
    invoices.upsert_invoice(db, doc)
    headers = _login(app_client, "fin50@eternal.local")
    assert app_client.get("/api/finance/invoices/120618", headers=headers).status_code == 404
