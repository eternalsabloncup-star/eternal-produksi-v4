"""MULTI-SO quarantine + deferred po_not_registered — isolated Mongo only."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import org_master_repository as org
from app.repositories.po_repository import (
    ensure_po_indexes,
    get_by_accurate_id,
    upsert_sales_order,
)
from app.services import delivery_sync
from app.services.accurate_auto_sync import sj_job
from app.services.accurate_auto_sync.checkpoint import load_job_checkpoint
from app.services.accurate_auto_sync.sj_sync_review import (
    REASON_MULTI_SO,
    REASON_PO_NOT_REGISTERED,
    get_sj_sync_review,
)
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.po_sync import sync_sales_order_payload

LIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"
)
OWNER = {
    "email_normalized": "owner@eternal.local",
    "role": "super_admin",
    "effective_module_permissions": ["access.all_branches"],
}


@pytest.fixture(autouse=True)
def _enable_sj(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    from app.config import clear_settings_cache
    from app.services.accurate_auto_sync.date_floor import clear_date_floor_cache

    clear_settings_cache()
    clear_date_floor_cache()
    yield
    clear_settings_cache()
    clear_date_floor_cache()


@pytest.fixture()
def sdb(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    ensure_po_indexes(db)
    from app.repositories.delivery_repository import ensure_delivery_indexes

    ensure_delivery_indexes(db)
    org.ensure_org_master_indexes(db)
    org.upsert_branch(db, code="50", name="Cabang 50", is_active=True)
    return db


def _sj(
    sj_id: int,
    *,
    so_ids: list[int],
    trans_date: str = "05/11/2026",
) -> dict[str, Any]:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = sj_id
    payload["number"] = f"TEST-SJ/Q/{sj_id}"
    payload["transDate"] = trans_date
    base = payload["detailItem"][0]
    items = []
    for i, so_id in enumerate(so_ids):
        item = copy.deepcopy(base)
        item["id"] = int(f"{sj_id}{i + 1}")
        item["salesOrderId"] = so_id
        if isinstance(item.get("salesOrder"), dict):
            item["salesOrder"]["id"] = so_id
            item["salesOrder"]["number"] = f"PO/{so_id}"
        items.append(item)
    payload["detailItem"] = items
    return payload


def _seed_po(db: Database, accurate_id: str = "95106") -> None:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id)
    payload["branchId"] = 50
    payload["transDate"] = "18/11/2026"
    out = sync_sales_order_payload(db, payload, mongo_guard="test")
    assert int(out.get("writes") or 0) >= 1
    assert get_by_accurate_id(db, accurate_id) is not None


class _Client:
    def __init__(self, details: dict[int, dict], pages: dict[int, list[dict]]) -> None:
        self.details = details
        self.pages = pages
        self.fail_detail: set[str] = set()

    def fetch_delivery_order_list(self, params: dict | None = None) -> list[dict]:
        params = params or {}
        page = int(params.get("sp.page") or 1)
        return list(self.pages.get(page) or [])

    def fetch_delivery_order_detail(self, accurate_id: str | int) -> dict:
        key = str(accurate_id)
        if key in self.fail_detail:
            raise ConnectionError("simulated_network")
        doc = self.details.get(int(accurate_id))
        if doc is None:
            raise LookupError(accurate_id)
        return copy.deepcopy(doc)


def test_multi_so_119068_quarantine_not_ingested(sdb: Database) -> None:
    """Representative DO 119068: multi-SO → review, zero DN."""
    detail = _sj(119068, so_ids=[95106, 99901])
    result = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert result["outcome"] == "review_required"
    assert result["reason"] == REASON_MULTI_SO
    assert result.get("writes") == 0
    assert deliveries.get_delivery_note(sdb, "119068") is None
    review = get_sj_sync_review(sdb, "119068")
    assert review is not None
    assert review["status"] == "open"
    assert set(review["source_so_ids"]) == {"95106", "99901"}
    assert review["retryable"] is True


def test_valid_do_after_multi_so_same_page_advances_checkpoint(sdb: Database) -> None:
    _seed_po(sdb)
    multi = _sj(119068, so_ids=[95106, 99901])
    valid = _sj(119070, so_ids=[95106])
    client = _Client(
        {119068: multi, 119070: valid},
        {1: [{"id": 119068, "transDate": "05/11/2026"}, {"id": 119070, "transDate": "05/11/2026"}]},
    )
    result = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=20, max_pages=5, max_rows=50
    )
    assert result["counters"]["review_required"] >= 1
    assert result["counters"]["created"] >= 1
    assert result["counters"]["failed"] == 0
    assert result.get("stopped_reason") != "page_failed"
    assert deliveries.get_delivery_note(sdb, "119068") is None
    assert deliveries.get_delivery_note(sdb, "119070") is not None
    cp = load_job_checkpoint(sdb, "sj", contract_status=sj_job.SJ_CONTRACT_STATUS)
    # Satu halaman penuh / pendek → next_page maju atau backfill selesai.
    assert int(cp.get("next_page") or 0) >= 1
    assert cp.get("last_completed_page") == 1 or cp.get("backfill_complete") is True


def test_repeated_multi_so_idempotent_review(sdb: Database) -> None:
    detail = _sj(119068, so_ids=[1, 2])
    first = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    second = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert first["outcome"] == second["outcome"] == "review_required"
    assert sdb["accurate_sj_sync_reviews"].count_documents({}) == 1
    review = get_sj_sync_review(sdb, "119068")
    assert int(review["seen_count"]) >= 2


def test_po_not_registered_deferred_then_ingests_after_po(sdb: Database) -> None:
    detail = _sj(880901, so_ids=[95106])
    first = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert first["outcome"] == "skipped"
    assert first["reason"] == REASON_PO_NOT_REGISTERED
    assert first.get("deferred") is True
    assert deliveries.get_delivery_note(sdb, "880901") is None
    review = get_sj_sync_review(sdb, "880901")
    assert review["retryable"] is True
    assert review["reason"] == REASON_PO_NOT_REGISTERED

    _seed_po(sdb)
    client = _Client({880901: detail}, {1: []})
    # Replay saja (list kosong) — harus ingest dari deferred.
    result = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=10, max_pages=2, max_rows=20
    )
    assert result["counters"]["created"] >= 1
    note = deliveries.get_delivery_note(sdb, "880901")
    assert note is not None
    assert note["accurate_po_id"] == "95106"
    resolved = get_sj_sync_review(sdb, "880901")
    assert resolved["status"] == "resolved"


def test_checkpoint_survives_interrupt_without_silent_loss(sdb: Database) -> None:
    _seed_po(sdb)
    # Page 1: multi-SO + valid. Page 2: another valid — budget 1 page.
    pages = {
        1: [
            {"id": 119068, "transDate": "05/11/2026"},
            {"id": 119069, "transDate": "05/11/2026"},
        ],
        2: [{"id": 119071, "transDate": "05/11/2026"}],
    }
    details = {
        119068: _sj(119068, so_ids=[95106, 77]),
        119069: _sj(119069, so_ids=[95106]),
        119071: _sj(119071, so_ids=[95106]),
    }
    client = _Client(details, pages)
    first = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=2, max_pages=1, max_rows=10
    )
    assert first["counters"]["failed"] == 0
    assert deliveries.get_delivery_note(sdb, "119069") is not None
    assert get_sj_sync_review(sdb, "119068") is not None
    cp1 = load_job_checkpoint(sdb, "sj", contract_status=sj_job.SJ_CONTRACT_STATUS)
    assert int(cp1.get("next_page") or 0) == 2

    second = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=2, max_pages=1, max_rows=10
    )
    assert second["counters"]["failed"] == 0
    assert deliveries.get_delivery_note(sdb, "119071") is not None
    # Multi-SO tetap tidak ter-ingest.
    assert deliveries.get_delivery_note(sdb, "119068") is None


def test_multi_so_not_on_unrelated_customer_tracking(sdb: Database) -> None:
    _seed_po(sdb)
    delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, _sj(119068, so_ids=[95106, 88]), mongo_guard="test"
    )
    # Tracking list DN by PO — quarantine tidak muncul sebagai SJ.
    notes = deliveries.list_delivery_notes(sdb, accurate_po_id="95106")
    assert all(str(n.get("accurate_sj_id")) != "119068" for n in notes)
    assert deliveries.get_delivery_note(sdb, "119068") is None


def test_no_false_accepted_loading_delivered(sdb: Database) -> None:
    _seed_po(sdb)
    delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, _sj(119068, so_ids=[1, 2]), mongo_guard="test"
    )
    po = get_by_accurate_id(sdb, "95106")
    assert (po.get("operational") or {}).get("acceptance", {}).get("status") != "accepted"
    assert sdb["loading_events"].count_documents({}) == 0
    assert sdb["delivery_events"].count_documents({}) == 0


def test_network_failure_still_fatal(sdb: Database) -> None:
    _seed_po(sdb)
    detail = _sj(119080, so_ids=[95106])
    client = _Client({119080: detail}, {1: [{"id": 119080, "transDate": "05/11/2026"}]})
    client.fail_detail.add("119080")
    result = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=10, max_pages=1, max_rows=10
    )
    assert result["ok"] is False
    assert result.get("stopped_reason") == "page_failed"
    assert deliveries.get_delivery_note(sdb, "119080") is None


def test_dual_flag_off_blocks_auto_sync(sdb: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    from app.config import clear_settings_cache

    clear_settings_cache()
    out = sj_job.run_sj_auto_sync(sdb, client=_Client({}, {}), mongo_guard="test")
    assert out["enabled"] is False
    assert out["writes"] == 0


def test_targeted_multi_so_is_review_not_failed(sdb: Database) -> None:
    upsert_sales_order(sdb, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    details = {9: _sj(9, so_ids=[95106, 100])}
    client = _Client(details, {})
    result = delivery_sync.sync_selected_delivery_orders_from_accurate(
        sdb, OWNER, ["9"], client=client, scope="multi-q"
    )
    assert result["review_required"] == 1
    assert result["failed"] == 0
    assert result["created"] == 0
    assert get_sj_sync_review(sdb, "9") is not None
