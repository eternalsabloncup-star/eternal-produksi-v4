"""Invoice + Receipt auto-sync — kontrak verified, gate fail-closed."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize_invoice_receipt import (
    normalize_sales_invoice,
    normalize_sales_receipt,
)
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import invoice_repository as invoices
from app.repositories import payment_repository as payments
from app.repositories.po_repository import upsert_sales_order
from app.services.accurate_auto_sync import invoice_job, receipt_job, runner
from app.services.accurate_auto_sync.status import (
    STATUS_COLLECTION,
    ensure_auto_sync_status_indexes,
)
from app.services import payment_service


@pytest.fixture()
def sync_db(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    invoices.ensure_invoice_indexes(db)
    payments.ensure_payment_indexes(db)
    return db


def _mongo_fp(db: Database) -> dict[str, Any]:
    return {
        "inv": list(db[invoices.INVOICES_COLLECTION].find({}, {"_id": 0})),
        "rcpt": list(db[payments.RECEIPTS_COLLECTION].find({}, {"_id": 0})),
        "status": list(db[STATUS_COLLECTION].find({}, {"_id": 0})),
        "locks": list(db["sync_locks"].find({}, {"_id": 1})),
    }


def _invoice_payload(
    inv_id: int = 120402,
    *,
    so_id: int = 95106,
    second_so: int | None = None,
    total: float = 1000.0,
) -> dict[str, Any]:
    lines = [
        {
            "id": 1,
            "salesOrderId": so_id,
            "salesOrderDetailId": 10,
            "itemId": 100,
            "quantity": 10,
        }
    ]
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
    return {
        "id": inv_id,
        "number": f"SI/{inv_id}",
        "transDate": "20/11/2026",
        "status": "OUTSTANDING",
        "totalAmount": total,
        "subTotal": total,
        "salesOrder": False,
        "detailItem": lines,
    }


def _receipt_payload(
    rid: int = 120203,
    *,
    invoice_id: int = 120402,
    invoice_no: str = "SI/120402",
    amount: float = 1000.0,
    second_invoice: int | None = None,
) -> dict[str, Any]:
    details = [
        {
            "id": 50,
            "invoiceId": invoice_id,
            "paymentAmount": amount,
            "invoice": {"id": invoice_id, "number": invoice_no},
        }
    ]
    if second_invoice is not None:
        details.append(
            {
                "id": 51,
                "invoiceId": second_invoice,
                "paymentAmount": 100.0,
                "invoice": {"id": second_invoice, "number": f"SI/{second_invoice}"},
            }
        )
    return {
        "id": rid,
        "number": f"SR/{rid}",
        "transDate": "20/11/2026",
        "totalPayment": amount + (100.0 if second_invoice else 0.0),
        "detailInvoice": details,
    }


class FakeDocClient:
    def __init__(self, *, invoices: dict[str, dict], receipts: dict[str, dict]):
        self.invoices = invoices
        self.receipts = receipts
        self.list_calls = 0
        self.detail_calls = 0

    def fetch_sales_invoice_list(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [{"id": int(k)} for k in self.invoices]

    def fetch_sales_invoice_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.detail_calls += 1
        return copy.deepcopy(self.invoices[str(accurate_id)])

    def fetch_sales_receipt_list(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [{"id": int(k)} for k in self.receipts]

    def fetch_sales_receipt_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.detail_calls += 1
        return copy.deepcopy(self.receipts[str(accurate_id)])


def test_normalize_invoice_uses_line_sales_order_id() -> None:
    doc = normalize_sales_invoice(_invoice_payload())
    assert doc["accurate_po_ids"] == ["95106"]
    assert doc["primary_accurate_po_id"] == "95106"
    assert doc["review_required"] is False


def test_normalize_invoice_multi_so_review() -> None:
    doc = normalize_sales_invoice(_invoice_payload(second_so=99999))
    assert doc["review_required"] is True
    assert len(doc["accurate_po_ids"]) == 2


def test_normalize_receipt_multi_invoice_review() -> None:
    doc = normalize_sales_receipt(_receipt_payload(second_invoice=120401))
    assert doc["review_required"] is True


def test_invoice_and_receipt_kill_switch_zero_write(
    sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    before = _mongo_fp(sync_db)
    client = FakeDocClient(
        invoices={"120402": _invoice_payload()},
        receipts={"120203": _receipt_payload()},
    )
    invoice_job.run_invoice_auto_sync(sync_db, client=client)
    receipt_job.run_receipt_auto_sync(sync_db, client=client)
    assert before == _mongo_fp(sync_db)
    assert client.list_calls == 0


def test_invoice_sync_then_receipt_opens_payment_gate(
    sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    # Seed PO 95106
    so = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    so["id"] = 95106
    so["number"] = "PO/TEST/95106"
    so["charField3"] = "K"
    so["charField5"] = "Non - Buffer"
    upsert_sales_order(sync_db, normalize_sales_order(so))

    client = FakeDocClient(
        invoices={"120402": _invoice_payload(so_id=95106)},
        receipts={"120203": _receipt_payload(invoice_id=120402, invoice_no="SI/120402")},
    )
    inv_result = invoice_job.run_invoice_auto_sync(sync_db, client=client)
    assert inv_result["ok"] is True
    assert inv_result["counters"]["created"] == 1

    rcpt_result = receipt_job.run_receipt_auto_sync(sync_db, client=client)
    assert rcpt_result["ok"] is True
    assert rcpt_result["gate_mutations"] == 1
    gate = payment_service.evaluate_payment_gate(sync_db, "95106")
    assert gate["payment"]["state"] == "open"


def test_receipt_without_invoice_snapshot_is_review(
    sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    client = FakeDocClient(
        invoices={},
        receipts={"120203": _receipt_payload()},
    )
    result = receipt_job.run_receipt_auto_sync(sync_db, client=client)
    assert result["counters"]["review_required"] == 1
    assert result["gate_mutations"] == 0
    stored = payments.list_receipts(sync_db, "UNMAPPED")
    assert stored and (stored[0].get("raw_ref") or {}).get("review_required") is True


def test_monitoring_includes_invoice_job(
    sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    snap = runner.monitoring_snapshot(sync_db)
    names = {j["job"] for j in snap["jobs"]}
    assert names == {"po", "invoice", "receipt", "sj"}
    assert snap["manual_sync_button"] is False
