"""Auto-sync Accurate — kill switch, lock, PO job, receipt blocked, SJ off."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services.accurate_auto_sync import locks, po_job, receipt_job, runner, status
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED


class FakeAccurateClient:
    def __init__(self, *, list_rows: list[dict[str, Any]], details: dict[str, dict[str, Any]]):
        self.list_rows = list_rows
        self.details = details
        self.list_calls = 0
        self.detail_calls = 0

    def fetch_sales_order_list(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.list_calls += 1
        page = int((params or {}).get("sp.page") or 1)
        size = int((params or {}).get("sp.pageSize") or 20)
        start = (page - 1) * size
        return self.list_rows[start : start + size]

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.detail_calls += 1
        key = str(accurate_id)
        if key not in self.details:
            raise LookupError(key)
        return copy.deepcopy(self.details[key])


@pytest.fixture()
def auto_sync_db(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    return db


def _payload(so_id: int = 95106, qty_a: float = 1000, qty_b: float = 1000) -> dict[str, Any]:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = so_id
    payload["number"] = f"PO/TEST/{so_id}"
    payload["charField3"] = "K"
    payload["charField5"] = "Non - Buffer"
    payload["detailItem"][0]["salesmanName"] = "Sales Transaksi"
    payload["detailItem"][1]["salesmanName"] = "Sales Transaksi"
    payload["detailItem"][0]["quantity"] = qty_a
    payload["detailItem"][1]["quantity"] = qty_b
    return payload


def test_kill_switch_off_zero_accurate_and_zero_write(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    client = FakeAccurateClient(list_rows=[{"id": 95106}], details={"95106": _payload()})
    result = po_job.run_po_auto_sync(auto_sync_db, client=client)
    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["accurate_requests"] == 0
    assert result["writes"] == 0
    assert client.list_calls == 0
    assert client.detail_calls == 0
    assert get_by_accurate_id(auto_sync_db, "95106") is None


def test_po_auto_sync_creates_inbox_without_accepting(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    payload = _payload()
    client = FakeAccurateClient(
        list_rows=[{"id": 95106}],
        details={"95106": payload},
    )
    result = po_job.run_po_auto_sync(auto_sync_db, client=client)
    assert result["ok"] is True
    assert result["counters"]["created"] == 1
    doc = get_by_accurate_id(auto_sync_db, "95106")
    assert doc is not None
    assert doc["header"]["ship_zone"] == "K"
    assert doc["header"]["buffer_label"] == "Non - Buffer"
    assert doc["header"]["seller"] == "Sales Transaksi"
    assert sum(float(i["qty"]) for i in doc["header"]["items"]) == 2000
    assert (doc.get("operational") or {}).get("acceptance", {}).get("status") != ACCEPTANCE_ACCEPTED
    assert (doc.get("operational") or {}).get("inbox_status") == "PO Baru"


def test_po_auto_sync_idempotent_unchanged(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    payload = _payload()
    upsert_sales_order(auto_sync_db, normalize_sales_order(payload))
    client = FakeAccurateClient(list_rows=[{"id": 95106}], details={"95106": payload})
    result = po_job.run_po_auto_sync(auto_sync_db, client=client)
    assert result["counters"]["unchanged"] == 1
    assert result["counters"]["created"] == 0


def test_accepted_po_material_qty_change_goes_to_review(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    payload = _payload()
    upsert_sales_order(auto_sync_db, normalize_sales_order(payload))
    auto_sync_db["accurate_sales_orders"].update_one(
        {"accurate_id": "95106"},
        {
            "$set": {
                "operational.acceptance": {
                    "status": ACCEPTANCE_ACCEPTED,
                    "accepted_by": "kp@example.com",
                },
                "operational.inbox_status": "PO Diterima",
            }
        },
    )
    changed = _payload(qty_a=1500, qty_b=1000)
    client = FakeAccurateClient(list_rows=[{"id": 95106}], details={"95106": changed})
    result = po_job.run_po_auto_sync(auto_sync_db, client=client)
    assert result["counters"]["review_required"] == 1
    doc = get_by_accurate_id(auto_sync_db, "95106")
    assert doc is not None
    assert sum(float(i["qty"]) for i in doc["header"]["items"]) == 2000
    assert (doc.get("operational") or {}).get("acceptance", {}).get("status") == ACCEPTANCE_ACCEPTED
    assert (doc.get("operational") or {}).get("sync_review_required") is True


def test_lock_blocks_concurrent_job(auto_sync_db: Database) -> None:
    token = locks.acquire_job_lock(auto_sync_db, job="po")
    with pytest.raises(locks.AutoSyncLockError):
        locks.acquire_job_lock(auto_sync_db, job="po")
    assert locks.release_job_lock(auto_sync_db, job="po", owner_token=token) is True
    token2 = locks.acquire_job_lock(auto_sync_db, job="po")
    assert token2 != token
    locks.release_job_lock(auto_sync_db, job="po", owner_token=token2)


def test_receipt_job_blocked_even_when_enabled(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    result = receipt_job.run_receipt_auto_sync(auto_sync_db)
    assert result["blocked"] is True
    assert result["accurate_requests"] == 0
    assert result["writes"] == 0
    assert result["gate_mutations"] == 0
    assert result.get("error") == "receipt_contract_unverified"


def test_sj_job_off_zero_requests(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache
    from app.services.accurate_auto_sync import sj_job

    clear_settings_cache()
    result = sj_job.run_sj_auto_sync(auto_sync_db)
    assert result["enabled"] is False
    assert result["accurate_requests"] == 0
    assert result["writes"] == 0


def test_runner_all_with_flags_off(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    result = runner.run_auto_sync_job(auto_sync_db, "all")
    assert result["ok"] is True
    assert set(result["jobs"]) == {"po", "receipt", "sj"}


def test_monitoring_snapshot_has_no_manual_button(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    snap = runner.monitoring_snapshot(auto_sync_db)
    assert snap["manual_sync_button"] is False
    assert snap["interval_seconds"] == 60
    assert len(snap["jobs"]) == 3


def test_f12_zone_buffer_seller_regression_untouched() -> None:
    """Mapping zona PR #14 tetap valid setelah auto-sync module."""
    payload = _payload()
    po = normalize_sales_order(payload)
    assert po.ship_zone == "K"
    assert po.buffer_label == "Non - Buffer"
    assert po.seller == "Sales Transaksi"
    assert sum(float(i.qty) for i in po.items) == 2000
    assert len(po.items) == 2
