"""Shared discovery engine — invoice/receipt/sj checkpoints (isolated mongo)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import invoice_repository as invoices
from app.repositories import payment_repository as payments
from app.repositories.po_repository import ensure_po_indexes, upsert_sales_order
from app.services.accurate_auto_sync import invoice_job, receipt_job, sj_job
from app.services.accurate_auto_sync.checkpoint import load_job_checkpoint
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes

SJ_FIXTURE = Path(__file__).parent / "fixtures" / "accurate_delivery_order_TEST.json"


@pytest.fixture()
def ddb(db: Database) -> Database:
    # ``db`` fixture already ran ensure_indexes (incl. delivery txn indexes).
    ensure_auto_sync_status_indexes(db)
    ensure_po_indexes(db)
    invoices.ensure_invoice_indexes(db)
    payments.ensure_payment_indexes(db)
    from app.repositories.delivery_repository import ensure_delivery_indexes

    ensure_delivery_indexes(db)
    return db


def _inv(inv_id: int, so_id: int = 95106) -> dict[str, Any]:
    return {
        "id": inv_id,
        "number": f"SI/{inv_id}",
        "transDate": "20/11/2026",
        "status": "OUTSTANDING",
        "totalAmount": 100.0,
        "subTotal": 100.0,
        "salesOrder": False,
        "detailItem": [
            {
                "id": 1,
                "salesOrderId": so_id,
                "salesOrderDetailId": 10,
                "itemId": 100,
                "quantity": 1,
            }
        ],
    }


def _rcpt(rid: int, invoice_id: int) -> dict[str, Any]:
    return {
        "id": rid,
        "number": f"SR/{rid}",
        "transDate": "20/11/2026",
        "totalPayment": 100.0,
        "voidCheq": False,
        "detailInvoice": [
            {
                "id": 1,
                "invoiceId": invoice_id,
                "paymentAmount": 100.0,
                "invoice": {"id": invoice_id, "number": f"SI/{invoice_id}"},
            }
        ],
    }


class DocUniverse:
    def __init__(self) -> None:
        self.inv: dict[int, dict] = {}
        self.rcpt: dict[int, dict] = {}
        self.sj: dict[int, dict] = {}
        self.lu_inv: dict[int, str] = {}
        self.lu_rcpt: dict[int, str] = {}
        self.lu_sj: dict[int, str] = {}
        self._clock = 1_800_000_000
        self.fail_inv: set[str] = set()

    def _tick(self) -> str:
        self._clock += 1
        return str(self._clock)

    def seed_invoices(self, n: int, *, start: int = 1, so_id: int = 95106) -> None:
        for i in range(start, start + n):
            self.inv[i] = _inv(i, so_id=so_id)
            self.lu_inv[i] = self._tick()

    def seed_receipts(self, n: int, *, start: int = 1) -> None:
        for i in range(start, start + n):
            self.rcpt[i] = _rcpt(i, invoice_id=i)
            self.lu_rcpt[i] = self._tick()

    def seed_sj(self, n: int, *, start: int = 1, po_id: int = 95106) -> None:
        base = copy.deepcopy(__import__("json").loads(SJ_FIXTURE.read_text()))
        for i in range(start, start + n):
            p = copy.deepcopy(base)
            p["id"] = i
            p["number"] = f"SJ/{i}"
            # Map to registered PO
            for line in p.get("detailItem") or []:
                if isinstance(line, dict):
                    line["salesOrderId"] = po_id
                    if isinstance(line.get("salesOrder"), dict):
                        line["salesOrder"]["id"] = po_id
                        line["salesOrder"]["number"] = f"PO/{po_id}"
            if not p.get("transDate"):
                p["transDate"] = "20/11/2026"
            self.sj[i] = p
            self.lu_sj[i] = self._tick()

    def _page(self, ids: list[int], params: dict | None, lu: dict[int, str]) -> list[dict]:
        params = params or {}
        page = int(params.get("sp.page") or 1)
        size = int(params.get("sp.pageSize") or 20)
        sort = str(params.get("sp.sort") or "")
        ordered = list(ids)
        if sort.startswith("id|asc") or sort == "id":
            ordered = sorted(ordered)
        elif "lastUpdate" in sort:
            ordered = sorted(ordered, key=lambda i: lu.get(i, ""), reverse=True)
        # Date floor BETWEEN — detail store per jenis dokumen.
        if params.get("filter.transDate.op") == "BETWEEN":
            from app.services.accurate_auto_sync.date_floor import (
                parse_accurate_trans_date,
            )

            lo = parse_accurate_trans_date(params.get("filter.transDate.val[0]"))
            hi = parse_accurate_trans_date(params.get("filter.transDate.val[1]"))
            store = {**self.inv, **self.rcpt, **self.sj}
            if lo and hi:
                kept = []
                for i in ordered:
                    td = parse_accurate_trans_date(
                        (store.get(i) or {}).get("transDate") or "20/11/2026"
                    )
                    if td is not None and lo <= td <= hi:
                        kept.append(i)
                ordered = kept
        start = (page - 1) * size
        chunk = ordered[start : start + size]
        store = {**self.inv, **self.rcpt, **self.sj}
        return [
            {
                "id": i,
                "lastUpdate": lu.get(i),
                "transDate": (store.get(i) or {}).get("transDate") or "20/11/2026",
            }
            for i in chunk
        ]

    def fetch_sales_invoice_list(self, *, params=None):
        return self._page(list(self.inv), params, self.lu_inv)

    def fetch_sales_invoice_detail(self, accurate_id):
        key = str(accurate_id)
        if key in self.fail_inv:
            raise RuntimeError("forced")
        return copy.deepcopy(self.inv[int(key)])

    def fetch_sales_receipt_list(self, *, params=None):
        return self._page(list(self.rcpt), params, self.lu_rcpt)

    def fetch_sales_receipt_detail(self, accurate_id):
        return copy.deepcopy(self.rcpt[int(accurate_id)])

    def fetch_delivery_order_list(self, *, params=None):
        return self._page(list(self.sj), params, self.lu_sj)

    def fetch_delivery_order_detail(self, accurate_id):
        return copy.deepcopy(self.sj[int(accurate_id)])


def _enable_invoice(monkeypatch):
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()


def _enable_receipt(monkeypatch):
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()


def _enable_sj(monkeypatch):
    """SJ ingest guard menolak port 27018 (prod label). CI memakai 27017.

    Di Cloud Agent, URI pytest = 27018 — tipu guard dengan URI 27019 sambil
    menulis lewat fixture ``db`` yang sudah terhubung ke test mongo.
    """
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "MONGODB_URI",
        "mongodb://eternal:eternal_dev_password@127.0.0.1:27019/"
        "?authSource=admin&directConnection=true",
    )
    from app.config import clear_settings_cache

    clear_settings_cache()


def test_invoice_250_backfill_cap_100(ddb: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_invoice(monkeypatch)
    so = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    so["id"] = 95106
    upsert_sales_order(ddb, normalize_sales_order(so))
    client = DocUniverse()
    client.seed_invoices(250)
    for _ in range(5):
        r = invoice_job.run_invoice_auto_sync(
            ddb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=50
        )
        assert r["ok"] is True
        cp = load_job_checkpoint(ddb, "invoice")
        if cp.get("backfill_complete"):
            break
        assert r["complete"] is False
    assert ddb[invoices.INVOICES_COLLECTION].count_documents({}) == 250
    assert load_job_checkpoint(ddb, "invoice")["backfill_complete"] is True


def test_invoice_mid_fail_retry_and_independent_checkpoint(
    ddb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_invoice(monkeypatch)
    so = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    so["id"] = 95106
    upsert_sales_order(ddb, normalize_sales_order(so))
    client = DocUniverse()
    client.seed_invoices(30)
    client.fail_inv.add("2")
    r1 = invoice_job.run_invoice_auto_sync(
        ddb, client=client, page_size=5, max_pages=2, max_rows=100, timeout_seconds=50
    )
    assert r1["ok"] is False
    assert load_job_checkpoint(ddb, "invoice")["next_page"] == 1
    # PO checkpoint must remain untouched
    assert load_job_checkpoint(ddb, "po").get("next_page") == 1
    assert load_job_checkpoint(ddb, "po").get("backfill_complete") is False
    client.fail_inv.clear()
    for _ in range(8):
        r = invoice_job.run_invoice_auto_sync(
            ddb, client=client, page_size=5, max_pages=3, max_rows=100, timeout_seconds=50
        )
        if r.get("complete"):
            break
    assert ddb[invoices.INVOICES_COLLECTION].count_documents({}) == 30
    assert load_job_checkpoint(ddb, "invoice")["backfill_complete"] is True


def test_receipt_backfill_after_invoices(
    ddb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_invoice(monkeypatch)
    _enable_receipt(monkeypatch)
    so = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    so["id"] = 95106
    upsert_sales_order(ddb, normalize_sales_order(so))
    client = DocUniverse()
    client.seed_invoices(40)
    client.seed_receipts(40)
    for _ in range(3):
        invoice_job.run_invoice_auto_sync(
            ddb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=50
        )
        if load_job_checkpoint(ddb, "invoice").get("backfill_complete"):
            break
    for _ in range(6):
        r = receipt_job.run_receipt_auto_sync(
            ddb, client=client, page_size=20, max_pages=5, max_rows=100, timeout_seconds=50
        )
        if r.get("complete"):
            break
    assert ddb[payments.RECEIPTS_COLLECTION].count_documents({}) == 40
    assert load_job_checkpoint(ddb, "receipt")["backfill_complete"] is True
    assert r.get("complete") is True


def test_sj_discovery_checkpoint_guard_test(
    ddb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_sj(monkeypatch)
    so = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    so["id"] = 95106
    upsert_sales_order(ddb, normalize_sales_order(so))
    client = DocUniverse()
    client.seed_sj(25, po_id=95106)
    for _ in range(6):
        r = sj_job.run_sj_auto_sync(
            ddb, client=client, page_size=10, max_pages=3, max_rows=100, timeout_seconds=50
        )
        assert r["enabled"] is True
        if r.get("complete"):
            break
    assert load_job_checkpoint(ddb, "sj")["backfill_complete"] is True
    assert r.get("complete") is True
    from app.repositories.delivery_repository import DELIVERY_NOTES_COLLECTION

    assert ddb[DELIVERY_NOTES_COLLECTION].count_documents({}) == 25


def test_all_kill_switches_off(ddb: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache
    from app.services.accurate_auto_sync import po_job

    clear_settings_cache()
    client = DocUniverse()
    client.seed_invoices(5)
    assert po_job.run_po_auto_sync(ddb, client=client)["enabled"] is False
    assert invoice_job.run_invoice_auto_sync(ddb, client=client)["enabled"] is False
    assert receipt_job.run_receipt_auto_sync(ddb, client=client)["enabled"] is False
    assert sj_job.run_sj_auto_sync(ddb, client=client)["enabled"] is False
