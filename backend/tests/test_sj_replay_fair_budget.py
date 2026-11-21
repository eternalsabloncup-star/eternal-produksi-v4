"""Fair discovery/replay budget + eligibility — isolated Mongo only."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

from app.repositories import delivery_repository as deliveries
from app.repositories import org_master_repository as org
from app.repositories.po_repository import ensure_po_indexes, get_by_accurate_id
from app.services import delivery_sync
from app.services.accurate_auto_sync import sj_job
from app.services.accurate_auto_sync.checkpoint import load_job_checkpoint
from app.services.accurate_auto_sync.sj_sync_review import (
    COLLECTION,
    REASON_MULTI_SO,
    REASON_PO_NOT_REGISTERED,
    allocate_sj_cycle_budgets,
    get_sj_sync_review,
    list_open_retryable_reviews,
    review_is_eligible,
    upsert_sj_sync_review,
)
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.po_sync import sync_sales_order_payload
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE

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
    monkeypatch.delenv("V4_SJ_DISCOVERY_MIN_RATIO", raising=False)
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


def _sj(sj_id: int, *, so_ids: list[int], trans_date: str = "05/11/2026") -> dict:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = sj_id
    payload["number"] = f"TEST-SJ/FB/{sj_id}"
    payload["transDate"] = trans_date
    base = payload["detailItem"][0]
    items = []
    for i, so_id in enumerate(so_ids):
        item = copy.deepcopy(base)
        item["id"] = int(f"{sj_id}{i + 1}")
        item["salesOrderId"] = so_id
        if isinstance(item.get("salesOrder"), dict):
            item["salesOrder"]["id"] = so_id
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
        self.detail_hits: list[str] = []

    def fetch_delivery_order_list(self, params: dict | None = None) -> list[dict]:
        page = int((params or {}).get("sp.page") or 1)
        return list(self.pages.get(page) or [])

    def fetch_delivery_order_detail(self, accurate_id: str | int) -> dict:
        key = str(accurate_id)
        self.detail_hits.append(key)
        doc = self.details.get(int(accurate_id))
        if doc is None:
            raise LookupError(accurate_id)
        return copy.deepcopy(doc)


def test_allocate_budgets_default_70_30() -> None:
    b = allocate_sj_cycle_budgets(100)
    assert b["discovery"] == 70
    assert b["replay"] == 30
    assert b["discovery"] + b["replay"] == 100


def test_starvation_1000_deferred_discovery_still_advances(sdb: Database) -> None:
    _seed_po(sdb)
    # 1000 deferred po_not_registered (legacy-compatible + eligible now)
    now = datetime.now(timezone.utc)
    for i in range(1000):
        sdb[COLLECTION].insert_one(
            {
                "_id": f"sj:defer{i}",
                "kind": "sj_sync_review",
                "accurate_sj_id": f"defer{i}",
                "reason": REASON_PO_NOT_REGISTERED,
                "accurate_po_id": "999999",
                "source_so_ids": ["999999"],
                "retryable": True,
                "status": "open",
                "seen_count": 1,
                "updated_at": now,
                # eligible sekarang — gate PO lokal menahan Accurate
                "next_retry_at": now - timedelta(seconds=1),
            }
        )
    # Discovery page: valid SJ ids
    pages = {
        1: [{"id": 200001, "transDate": "05/11/2026"}],
        2: [{"id": 200002, "transDate": "05/11/2026"}],
    }
    details = {
        200001: _sj(200001, so_ids=[95106]),
        200002: _sj(200002, so_ids=[95106]),
    }
    client = _Client(details, pages)
    first = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=1, max_pages=1, max_rows=100
    )
    assert first["fair_budget"]["discovery"] == 70
    assert first["fair_budget"]["replay"] == 30
    assert deliveries.get_delivery_note(sdb, "200001") is not None
    cp = load_job_checkpoint(sdb, "sj", contract_status=sj_job.SJ_CONTRACT_STATUS)
    assert int(cp.get("next_page") or 0) == 2
    # Replay tidak boleh fetch Accurate untuk 1000 deferred tanpa PO
    assert all(not h.startswith("defer") for h in client.detail_hits)
    replay = first.get("review_replay") or {}
    assert int(replay.get("replay_budget") or 0) <= 30
    assert int(replay.get("deferred_cooldown") or 0) <= 30

    second = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=1, max_pages=1, max_rows=100
    )
    assert deliveries.get_delivery_note(sdb, "200002") is not None
    cp2 = load_job_checkpoint(sdb, "sj", contract_status=sj_job.SJ_CONTRACT_STATUS)
    assert int(cp2.get("next_page") or 0) >= 2
    assert second["counters"]["failed"] == 0


def test_multi_so_not_eligible_every_cycle(sdb: Database) -> None:
    detail = _sj(119068, so_ids=[1, 2])
    delivery_sync.sync_delivery_order_payload(sdb, OWNER, detail, mongo_guard="test")
    rev = get_sj_sync_review(sdb, "119068")
    assert rev is not None
    assert rev["reason"] == REASON_MULTI_SO
    # Baru di-quarantine → cooldown panjang → tidak eligible
    assert review_is_eligible(rev) is False
    eligible = list_open_retryable_reviews(sdb, limit=50)
    assert all(str(r.get("accurate_sj_id")) != "119068" for r in eligible)


def test_po_not_registered_local_gate_then_ingest(sdb: Database) -> None:
    detail = _sj(880901, so_ids=[95106])
    delivery_sync.sync_delivery_order_payload(sdb, OWNER, detail, mongo_guard="test")
    client = _Client({880901: detail}, {1: []})
    # Belum ada PO → cooldown tanpa Accurate detail
    before = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=5, max_pages=2, max_rows=20
    )
    assert "880901" not in client.detail_hits
    assert deliveries.get_delivery_note(sdb, "880901") is None
    # Paksa eligible + seed PO
    _seed_po(sdb)
    sdb[COLLECTION].update_one(
        {"_id": "sj:880901"},
        {"$set": {"next_retry_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    client.detail_hits.clear()
    after = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=5, max_pages=2, max_rows=20
    )
    assert "880901" in client.detail_hits
    assert deliveries.get_delivery_note(sdb, "880901") is not None
    assert get_sj_sync_review(sdb, "880901")["status"] == "resolved"
    assert after["counters"]["failed"] == 0
    assert before["ok"] is True or before.get("enabled") is True


def test_legacy_review_without_next_retry_eligible(sdb: Database) -> None:
    now = datetime.now(timezone.utc)
    sdb[COLLECTION].insert_one(
        {
            "_id": "sj:legacy1",
            "accurate_sj_id": "legacy1",
            "reason": REASON_PO_NOT_REGISTERED,
            "accurate_po_id": "95106",
            "retryable": True,
            "status": "open",
            "updated_at": now,
            # no next_retry_at / attempt_count
        }
    )
    assert review_is_eligible(get_sj_sync_review(sdb, "legacy1") or {}) is True
    rows = list_open_retryable_reviews(sdb, limit=10)
    assert any(r.get("accurate_sj_id") == "legacy1" for r in rows)


def test_idempotent_no_duplicate_dn(sdb: Database) -> None:
    _seed_po(sdb)
    detail = _sj(300001, so_ids=[95106])
    pages = {1: [{"id": 300001, "transDate": "05/11/2026"}]}
    client = _Client({300001: detail}, pages)
    a = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=5, max_pages=2, max_rows=20
    )
    b = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=5, max_pages=2, max_rows=20
    )
    assert a["counters"]["created"] >= 1
    assert deliveries.get_delivery_note(sdb, "300001") is not None
    assert sdb["accurate_delivery_notes"].count_documents({"accurate_sj_id": "300001"}) == 1
    assert b["counters"]["failed"] == 0


def test_guards_untouched_on_fair_budget_cycle(sdb: Database) -> None:
    _seed_po(sdb)
    delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, _sj(119068, so_ids=[1, 2]), mongo_guard="test"
    )
    client = _Client(
        {400001: _sj(400001, so_ids=[95106])},
        {1: [{"id": 400001, "transDate": "05/11/2026"}]},
    )
    sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=5, max_pages=1, max_rows=20
    )
    po = get_by_accurate_id(sdb, "95106")
    assert (po.get("operational") or {}).get("acceptance", {}).get("status") != "accepted"
    assert sdb["loading_events"].count_documents({}) == 0
    assert sdb["delivery_events"].count_documents({}) == 0
    assert deliveries.get_delivery_note(sdb, "119068") is None


def test_ratio_env_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_DISCOVERY_MIN_RATIO", "0.95")
    b = allocate_sj_cycle_budgets(100)
    assert b["discovery"] == 90  # capped HI
    monkeypatch.setenv("V4_SJ_DISCOVERY_MIN_RATIO", "0.1")
    b2 = allocate_sj_cycle_budgets(100)
    assert b2["discovery"] == 50  # floored LO
