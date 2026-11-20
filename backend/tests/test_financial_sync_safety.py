"""Final financial sync safety — invoice/receipt idempotency & void."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize_invoice_receipt import (
    normalize_sales_invoice,
    normalize_sales_receipt,
    parse_accurate_bool,
    parse_money,
)
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import invoice_repository as invoices
from app.repositories import payment_repository as payments
from app.repositories.po_repository import upsert_sales_order
from app.services.accurate_auto_sync import invoice_job, receipt_job
from app.services.accurate_auto_sync.locks import acquire_job_lock, release_job_lock
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services import payment_service


@pytest.fixture()
def fin_db(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    invoices.ensure_invoice_indexes(db)
    payments.ensure_payment_indexes(db)
    return db


def _seed_po(db: Database, so_id: int = 95106) -> None:
    so = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    so["id"] = so_id
    so["number"] = f"PO/TEST/{so_id}"
    so["charField3"] = "K"
    so["charField5"] = "Non - Buffer"
    upsert_sales_order(db, normalize_sales_order(so))


def _invoice_payload(inv_id: int = 120402, so_id: int = 95106, total: float = 1000.0) -> dict:
    return {
        "id": inv_id,
        "number": f"SI/{inv_id}",
        "transDate": "20/11/2026",
        "status": "OUTSTANDING",
        "totalAmount": total,
        "subTotal": total,
        "salesOrder": False,
        "detailItem": [
            {
                "id": 1,
                "salesOrderId": so_id,
                "salesOrderDetailId": 10,
                "itemId": 100,
                "quantity": 10,
            }
        ],
    }


def _receipt_payload(
    rid: int = 120203,
    *,
    invoice_id: int = 120402,
    amount: Any = 1000.0,
    total: Any | None = None,
    void_cheq: Any = False,
    omit_payment_amount: bool = False,
    omit_void_cheq: bool = False,
) -> dict:
    alloc: dict[str, Any] = {
        "id": 50,
        "invoiceId": invoice_id,
        "invoice": {"id": invoice_id, "number": f"SI/{invoice_id}"},
    }
    if not omit_payment_amount:
        alloc["paymentAmount"] = amount
    doc: dict[str, Any] = {
        "id": rid,
        "number": f"SR/{rid}",
        "transDate": "20/11/2026",
        "totalPayment": total if total is not None else amount,
        "detailInvoice": [alloc],
    }
    if not omit_void_cheq:
        doc["voidCheq"] = void_cheq
    return doc


class FakeClient:
    def __init__(self, invoices: dict[str, dict], receipts: dict[str, dict]):
        self.invoices = invoices
        self.receipts = receipts
        self.detail_calls = 0

    def fetch_sales_invoice_list(self, *, params=None):
        return [{"id": int(k)} for k in self.invoices]

    def fetch_sales_invoice_detail(self, accurate_id):
        self.detail_calls += 1
        return copy.deepcopy(self.invoices[str(accurate_id)])

    def fetch_sales_receipt_list(self, *, params=None):
        return [{"id": int(k)} for k in self.receipts]

    def fetch_sales_receipt_detail(self, accurate_id):
        self.detail_calls += 1
        return copy.deepcopy(self.receipts[str(accurate_id)])


def test_parse_money_rejects_invalid() -> None:
    assert parse_money(None)[0] is None
    assert parse_money("abc")[0] is None
    assert parse_money(float("nan"))[0] is None
    assert parse_money(float("inf"))[0] is None
    ok, err = parse_money("12.50")
    assert err is None and ok is not None


def test_invoice_unchanged_zero_write(
    fin_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    client = FakeClient({"120402": _invoice_payload()}, {})
    r1 = invoice_job.run_invoice_auto_sync(fin_db, client=client)
    assert r1["counters"]["created"] == 1
    before = fin_db[invoices.INVOICES_COLLECTION].find_one({"accurate_invoice_id": "120402"})
    r2 = invoice_job.run_invoice_auto_sync(fin_db, client=client)
    assert r2["counters"]["unchanged"] == 1
    after = fin_db[invoices.INVOICES_COLLECTION].find_one({"accurate_invoice_id": "120402"})
    assert before["synced_at"] == after["synced_at"]
    assert before["updated_at"] == after["updated_at"]


def test_receipt_identical_cycles_zero_effect(
    fin_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    _seed_po(fin_db)
    client = FakeClient(
        {"120402": _invoice_payload()},
        {"120203": _receipt_payload()},
    )
    invoice_job.run_invoice_auto_sync(fin_db, client=client)
    r1 = receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert r1["gate_mutations"] >= 1
    audits1 = fin_db["audit_logs"].count_documents({"action": "payment.receipts.ingest"})
    hist1 = fin_db["po_gate_history"].count_documents(
        {"accurate_po_id": "95106", "gate": "payment"}
    )
    synced = fin_db[payments.RECEIPTS_COLLECTION].find_one({"accurate_receipt_id": "120203"})[
        "synced_at"
    ]

    r2 = receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert r2["counters"]["unchanged"] == 1
    assert r2["writes"] == 0
    assert r2["gate_mutations"] == 0
    audits2 = fin_db["audit_logs"].count_documents({"action": "payment.receipts.ingest"})
    hist2 = fin_db["po_gate_history"].count_documents(
        {"accurate_po_id": "95106", "gate": "payment"}
    )
    assert audits2 == audits1
    assert hist2 == hist1
    assert (
        fin_db[payments.RECEIPTS_COLLECTION].find_one({"accurate_receipt_id": "120203"})[
            "synced_at"
        ]
        == synced
    )


def test_invalid_amount_and_total_mismatch_review() -> None:
    bad = normalize_sales_receipt(_receipt_payload(amount="not-a-number"))
    assert bad["review_required"] is True
    omit = normalize_sales_receipt(_receipt_payload(omit_payment_amount=True, total=100))
    assert omit["review_required"] is True
    mismatch = normalize_sales_receipt(_receipt_payload(amount=100, total=200))
    assert mismatch["review_required"] is True
    assert "tidak cocok" in (mismatch["review_reason"] or "")


def test_no_total_payment_fallback_for_allocation() -> None:
    doc = normalize_sales_receipt(_receipt_payload(omit_payment_amount=True, total=999))
    assert doc["allocations"][0]["payment_amount"] is None
    assert doc["review_required"] is True


def test_void_receipt_closes_open_gate(
    fin_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    _seed_po(fin_db)
    receipts = {"120203": _receipt_payload()}
    client = FakeClient({"120402": _invoice_payload()}, receipts)
    invoice_job.run_invoice_auto_sync(fin_db, client=client)
    receipt_job.run_receipt_auto_sync(fin_db, client=client)
    gate = payment_service.evaluate_payment_gate(fin_db, "95106")
    assert gate["payment"]["state"] == "open"

    receipts["120203"] = _receipt_payload(void_cheq=True, amount=-1000, total=-1000)
    r = receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert r["counters"]["review_required"] == 1
    gate2 = payment_service.evaluate_payment_gate(fin_db, "95106")
    assert gate2["payment"]["state"] == "review"


def test_amount_change_updates_and_reevaluates(
    fin_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    _seed_po(fin_db)
    receipts = {"120203": _receipt_payload(amount=500, total=500)}
    client = FakeClient({"120402": _invoice_payload()}, receipts)
    invoice_job.run_invoice_auto_sync(fin_db, client=client)
    receipt_job.run_receipt_auto_sync(fin_db, client=client)
    receipts["120203"] = _receipt_payload(amount=800, total=800)
    r = receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert r["counters"]["updated"] == 1
    assert r["writes"] == 1
    stored = payments.list_receipts(fin_db, "95106")[0]
    assert stored["amount"] == 800.0


def test_invoice_normalize_invalid_total_review() -> None:
    payload = _invoice_payload()
    payload["totalAmount"] = "xx"
    doc = normalize_sales_invoice(payload)
    assert doc["review_required"] is True
    assert doc["total_amount"] is None


@pytest.mark.parametrize(
    "raw,expected,err",
    [
        (False, False, None),
        (True, True, None),
        ("false", False, None),
        ("true", True, None),
        ("0", False, None),
        ("1", True, None),
        (0, False, None),
        (1, True, None),
        (None, None, None),
        ("maybe", None, "boolean ambigu"),
    ],
)
def test_parse_accurate_bool_void_cheq(raw: Any, expected: bool | None, err: str | None) -> None:
    parsed, reason = parse_accurate_bool(raw)
    assert parsed is expected
    if err is None:
        assert reason is None
    else:
        assert reason is not None and err in reason


def test_void_cheq_string_false_not_void() -> None:
    """Regression: bool('false')==True di Python — harus tetap non-void."""
    doc = normalize_sales_receipt(_receipt_payload(void_cheq="false"))
    assert doc["void_cheq"] is False
    assert doc["review_required"] is False


def test_void_cheq_string_true_review() -> None:
    doc = normalize_sales_receipt(_receipt_payload(void_cheq="true"))
    assert doc["void_cheq"] is True
    assert doc["review_required"] is True


def test_void_cheq_missing_not_void() -> None:
    doc = normalize_sales_receipt(_receipt_payload(omit_void_cheq=True))
    assert doc["void_cheq"] is False
    assert doc["review_required"] is False


def test_void_cheq_unknown_review() -> None:
    doc = normalize_sales_receipt(_receipt_payload(void_cheq="unknown"))
    assert doc["review_required"] is True
    assert doc["void_cheq"] is True


def test_void_false_to_true_reevaluates_gate(
    fin_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    _seed_po(fin_db)
    receipts = {"120203": _receipt_payload(void_cheq="false")}
    client = FakeClient({"120402": _invoice_payload()}, receipts)
    invoice_job.run_invoice_auto_sync(fin_db, client=client)
    receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert payment_service.evaluate_payment_gate(fin_db, "95106")["payment"]["state"] == "open"

    receipts["120203"] = _receipt_payload(void_cheq="true")
    r = receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert r["counters"]["review_required"] == 1
    assert r["gate_mutations"] >= 1
    assert payment_service.evaluate_payment_gate(fin_db, "95106")["payment"]["state"] == "review"


def test_void_true_to_false_reevaluates(
    fin_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    _seed_po(fin_db)
    # Seed valid receipt dulu agar void menempel ke PO (bukan UNMAPPED).
    receipts = {"120203": _receipt_payload(void_cheq=False)}
    client = FakeClient({"120402": _invoice_payload()}, receipts)
    invoice_job.run_invoice_auto_sync(fin_db, client=client)
    receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert payment_service.evaluate_payment_gate(fin_db, "95106")["payment"]["state"] == "open"

    receipts["120203"] = _receipt_payload(void_cheq=True)
    receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert payment_service.evaluate_payment_gate(fin_db, "95106")["payment"]["state"] == "review"

    receipts["120203"] = _receipt_payload(void_cheq=False)
    r = receipt_job.run_receipt_auto_sync(fin_db, client=client)
    assert r["writes"] >= 1
    assert payment_service.evaluate_payment_gate(fin_db, "95106")["payment"]["state"] == "open"
