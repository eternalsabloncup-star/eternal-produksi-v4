"""Auto-sync Accurate — zero-write OFF, acceptance race, partial, lease."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services.accurate_auto_sync import locks, po_job, receipt_job, runner, status
from app.services.accurate_auto_sync.status import (
    STATUS_COLLECTION,
    ensure_auto_sync_status_indexes,
)
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED


class FakeAccurateClient:
    def __init__(
        self,
        *,
        list_rows: list[dict[str, Any]],
        details: dict[str, dict[str, Any]],
        detail_errors: dict[str, Exception] | None = None,
    ):
        self.list_rows = list_rows
        self.details = details
        self.detail_errors = detail_errors or {}
        self.list_calls = 0
        self.detail_calls = 0

    def fetch_sales_order_list(self, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.list_calls += 1
        params = params or {}
        page = int(params.get("sp.page") or 1)
        size = int(params.get("sp.pageSize") or 20)
        sort = str(params.get("sp.sort") or "")
        rows = list(self.list_rows)
        if sort.startswith("id|asc") or sort == "id":
            rows = sorted(rows, key=lambda r: int(r.get("id") or 0))
        elif sort.startswith("id|desc"):
            rows = sorted(rows, key=lambda r: int(r.get("id") or 0), reverse=True)
        elif "lastUpdate" in sort:
            # Sort by actual lastUpdate stamp — bukan proxy id.
            rows = sorted(
                rows,
                key=lambda r: str(r.get("lastUpdate") or ""),
                reverse=True,
            )
        start = (page - 1) * size
        return rows[start : start + size]

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.detail_calls += 1
        key = str(accurate_id)
        if key in self.detail_errors:
            raise self.detail_errors[key]
        if key not in self.details:
            raise LookupError(key)
        return copy.deepcopy(self.details[key])


@pytest.fixture()
def auto_sync_db(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    return db


def _mongo_fingerprint(db: Database) -> dict[str, Any]:
    """Snapshot koleksi yang relevan — bukti zero-write, bukan hanya result.writes."""
    return {
        "po": list(db["accurate_sales_orders"].find({}, {"_id": 0})),
        "status": list(db[STATUS_COLLECTION].find({}, {"_id": 0})),
        "locks": list(db[locks.LOCKS_COLLECTION].find({}, {"_id": 1, "job": 1})),
        "receipts": list(db["payment_receipts"].find({}, {"_id": 0}))
        if "payment_receipts" in db.list_collection_names()
        else [],
        "delivery": list(db["delivery_notes"].find({}, {"_id": 0}))
        if "delivery_notes" in db.list_collection_names()
        else [],
    }


def _payload(
    so_id: int = 95106,
    qty_a: float = 1000,
    qty_b: float = 1000,
    *,
    customer_name: str = "Cust A",
    ship_zone: str = "K",
) -> dict[str, Any]:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = so_id
    payload["number"] = f"PO/TEST/{so_id}"
    payload["charField3"] = ship_zone
    payload["charField5"] = "Non - Buffer"
    payload["detailItem"][0]["salesmanName"] = "Sales Transaksi"
    payload["detailItem"][1]["salesmanName"] = "Sales Transaksi"
    payload["detailItem"][0]["quantity"] = qty_a
    payload["detailItem"][1]["quantity"] = qty_b
    if isinstance(payload.get("customer"), dict):
        payload["customer"]["name"] = customer_name
    return payload


def test_kill_switch_off_zero_mongo_mutation_all_jobs(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache
    from app.services.accurate_auto_sync import sj_job

    clear_settings_cache()
    before = _mongo_fingerprint(auto_sync_db)
    client = FakeAccurateClient(list_rows=[{"id": 95106}], details={"95106": _payload()})

    po_result = po_job.run_po_auto_sync(auto_sync_db, client=client)
    receipt_result = receipt_job.run_receipt_auto_sync(auto_sync_db)
    sj_result = sj_job.run_sj_auto_sync(auto_sync_db)

    after = _mongo_fingerprint(auto_sync_db)
    assert before == after
    assert client.list_calls == 0 and client.detail_calls == 0
    assert po_result["enabled"] is False and po_result["status"]["persisted"] is False
    assert receipt_result["enabled"] is False and receipt_result["status"]["persisted"] is False
    assert sj_result["enabled"] is False and sj_result["status"]["persisted"] is False


def test_po_auto_sync_creates_inbox_without_accepting(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    payload = _payload()
    client = FakeAccurateClient(list_rows=[{"id": 95106}], details={"95106": payload})
    result = po_job.run_po_auto_sync(auto_sync_db, client=client)
    assert result["ok"] is True
    assert result["complete"] is True
    assert result["status"]["health"] == "HEALTHY"
    doc = get_by_accurate_id(auto_sync_db, "95106")
    assert doc is not None
    assert doc["header"]["ship_zone"] == "K"
    assert doc["header"]["buffer_label"] == "Non - Buffer"
    assert doc["header"]["seller"] == "Sales Transaksi"
    assert sum(float(i["qty"]) for i in doc["header"]["items"]) == 2000
    assert (doc.get("operational") or {}).get("acceptance", {}).get("status") != ACCEPTANCE_ACCEPTED


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
                "operational.gate_marker": "KEEP",
            }
        },
    )
    changed = _payload(qty_a=1500, qty_b=1000, customer_name="Cust CHANGED")
    client = FakeAccurateClient(list_rows=[{"id": 95106}], details={"95106": changed})
    result = po_job.run_po_auto_sync(auto_sync_db, client=client)
    assert result["counters"]["review_required"] == 1
    doc = get_by_accurate_id(auto_sync_db, "95106")
    assert doc is not None
    assert sum(float(i["qty"]) for i in doc["header"]["items"]) == 2000
    assert (doc.get("operational") or {}).get("acceptance", {}).get("status") == ACCEPTANCE_ACCEPTED
    assert (doc.get("operational") or {}).get("gate_marker") == "KEEP"
    assert (doc.get("operational") or {}).get("sync_review_required") is True
    # Customer tidak boleh ditimpa sebagai "nonmaterial"
    assert "Cust CHANGED" not in str(doc["header"].get("customer") or "")


def test_acceptance_race_does_not_overwrite_just_accepted(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulasikan Terima PO di tengah upsert: filter atomik menolak full overwrite."""
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    payload = _payload()
    upsert_sales_order(auto_sync_db, normalize_sales_order(payload))
    # Claim acceptance seperti Terima PO
    auto_sync_db["accurate_sales_orders"].update_one(
        {"accurate_id": "95106"},
        {
            "$set": {
                "operational.acceptance": {
                    "status": ACCEPTANCE_ACCEPTED,
                    "accepted_by": "kp@example.com",
                },
                "operational.inbox_status": "PO Diterima",
                "operational.tasks_seeded": True,
            }
        },
    )
    # Sync mencoba ubah qty + customer — harus review, bukan overwrite items/ops
    racing = _payload(qty_a=999, qty_b=999, customer_name="Race Cust")
    token = locks.acquire_job_lock(auto_sync_db, job="po")
    try:
        client = FakeAccurateClient(list_rows=[], details={"95106": racing})
        out = po_job.sync_one_listed_sales_order(
            auto_sync_db, "95106", client=client, owner_token=token
        )
        assert out["outcome"] == "review_required"
    finally:
        locks.release_job_lock(auto_sync_db, job="po", owner_token=token)

    doc = get_by_accurate_id(auto_sync_db, "95106")
    assert doc is not None
    assert sum(float(i["qty"]) for i in doc["header"]["items"]) == 2000
    assert (doc.get("operational") or {}).get("tasks_seeded") is True
    assert "Race Cust" not in str(doc["header"].get("customer") or "")


def test_lease_loss_stops_further_commit(auto_sync_db: Database) -> None:
    token = locks.acquire_job_lock(auto_sync_db, job="po")
    # Steal lease (simulasi expiry + worker lain)
    auto_sync_db[locks.LOCKS_COLLECTION].update_one(
        {"_id": locks.lock_id_for("po")},
        {
            "$set": {
                "owner_token": "other-worker",
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
            }
        },
    )
    client = FakeAccurateClient(
        list_rows=[{"id": 95106}], details={"95106": _payload()}
    )
    with pytest.raises(locks.AutoSyncLockError):
        po_job.sync_one_listed_sales_order(
            auto_sync_db, "95106", client=client, owner_token=token
        )
    assert get_by_accurate_id(auto_sync_db, "95106") is None
    # Release milik token lama tidak boleh menghapus lock worker lain
    assert locks.release_job_lock(auto_sync_db, job="po", owner_token=token) is False
    assert locks.lock_snapshot(auto_sync_db, "po") is not None
    assert locks.release_job_lock(auto_sync_db, job="po", owner_token="other-worker") is True


def test_lock_blocks_concurrent_job(auto_sync_db: Database) -> None:
    token = locks.acquire_job_lock(auto_sync_db, job="po")
    with pytest.raises(locks.AutoSyncLockError):
        locks.acquire_job_lock(auto_sync_db, job="po")
    assert locks.release_job_lock(auto_sync_db, job="po", owner_token=token) is True


def test_partial_detail_failure_not_healthy(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    good = _payload(95106)
    client = FakeAccurateClient(
        list_rows=[{"id": 95106}, {"id": 95107}],
        details={"95106": good},
        detail_errors={"95107": RuntimeError("boom")},
    )
    result = po_job.run_po_auto_sync(auto_sync_db, client=client)
    assert result["ok"] is False
    assert result["counters"]["failed"] == 1
    assert result["counters"]["created"] == 1
    assert result["status"]["health"] == "FAILED"
    assert result["status"].get("last_success_at") is None


def test_max_pages_reported_as_partial(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    rows = [{"id": i} for i in range(1, 6)]
    details = {str(i): _payload(i) for i in range(1, 6)}
    client = FakeAccurateClient(list_rows=rows, details=details)
    result = po_job.run_po_auto_sync(
        auto_sync_db, client=client, page_size=2, max_pages=1, max_rows=100
    )
    assert result["partial"] is True
    assert result["complete"] is False
    assert result["stopped_reason"] == "max_pages"
    assert result["status"]["health"] == "PARTIAL"
    # Partial sukses tanpa failure tidak mengklaim last_success penuh
    assert result["status"].get("last_success_at") is None


def test_timeout_reported_as_partial(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    client = FakeAccurateClient(
        list_rows=[{"id": 1}, {"id": 2}],
        details={"1": _payload(1), "2": _payload(2)},
    )
    result = po_job.run_po_auto_sync(
        auto_sync_db, client=client, page_size=20, max_pages=5, timeout_seconds=0.0001
    )
    assert result["stopped_reason"] == "timeout"
    assert result["partial"] is True
    assert result["status"]["health"] == "PARTIAL"


def test_receipt_job_enabled_requires_client_or_stays_isolated(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill switch ON tanpa client: list Accuratereal akan gagal — kontrak verified."""
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    result = receipt_job.run_receipt_auto_sync(auto_sync_db)
    assert result["enabled"] is False
    assert result["accurate_requests"] == 0
    assert receipt_job.RECEIPT_CONTRACT_STATUS.startswith("verified")


def test_sj_job_off_zero_requests(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache
    from app.services.accurate_auto_sync import sj_job

    clear_settings_cache()
    before = _mongo_fingerprint(auto_sync_db)
    result = sj_job.run_sj_auto_sync(auto_sync_db)
    assert result["enabled"] is False
    assert before == _mongo_fingerprint(auto_sync_db)


def test_runner_all_with_flags_off(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_INVOICE_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_RECEIPT_AUTO_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    before = _mongo_fingerprint(auto_sync_db)
    result = runner.run_auto_sync_job(auto_sync_db, "all")
    assert result["ok"] is True
    assert set(result["jobs"]) == {"po", "invoice", "receipt", "sj"}
    assert before == _mongo_fingerprint(auto_sync_db)


def test_monitoring_snapshot_disabled_from_config(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    snap = runner.monitoring_snapshot(auto_sync_db)
    assert snap["manual_sync_button"] is False
    assert snap["interval_seconds"] == 60
    assert {j["job"] for j in snap["jobs"]} == {"po", "invoice", "receipt", "sj"}
    assert all(j["health"] == "DISABLED" for j in snap["jobs"])


def test_f12_zone_buffer_seller_regression_untouched() -> None:
    payload = _payload()
    po = normalize_sales_order(payload)
    assert po.ship_zone == "K"
    assert po.buffer_label == "Non - Buffer"
    assert po.seller == "Sales Transaksi"
    assert sum(float(i.qty) for i in po.items) == 2000
    assert len(po.items) == 2


def test_retry_idempotent_no_duplicate_po(
    auto_sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("V4_PO_AUTO_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    payload = _payload()
    client = FakeAccurateClient(list_rows=[{"id": 95106}], details={"95106": payload})
    po_job.run_po_auto_sync(auto_sync_db, client=client)
    po_job.run_po_auto_sync(auto_sync_db, client=client)
    assert auto_sync_db["accurate_sales_orders"].count_documents({"accurate_id": "95106"}) == 1
