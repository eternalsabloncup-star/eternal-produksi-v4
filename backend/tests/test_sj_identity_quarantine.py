"""DELIVERY_NOTE_IDENTITY quarantine — isolated Mongo only (bukan production).

Repro shape: Accurate SJ 120851 (salesOrderDetailId / SO ref hilang).
"""

from __future__ import annotations

import copy
import json
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
    REASON_DELIVERY_NOTE_IDENTITY,
    REASON_PO_NOT_REGISTERED,
    get_sj_sync_review,
    review_is_eligible,
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


def _sj_like_120851(
    sj_id: int = 120851,
    *,
    with_so: bool = False,
    with_line_ref: bool = False,
    so_id: int = 95106,
) -> dict[str, Any]:
    """Bangun payload mirip SJ 120851: identity gap pada line/SO."""
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = sj_id
    payload["number"] = f"DO/TEST/IDENTITY/{sj_id}"
    payload["transDate"] = "21/11/2026"
    payload["salesOrderId"] = None
    payload["salesOrder"] = False
    base = copy.deepcopy(payload["detailItem"][0])
    base["id"] = int(f"{sj_id}01")
    if with_line_ref:
        # Pertahankan salesOrderDetailId dari fixture live.
        pass
    else:
        base["salesOrderDetailId"] = None
        base.pop("accurate_line_id", None)
    if with_so:
        base["salesOrderId"] = so_id
        if isinstance(base.get("salesOrder"), dict):
            base["salesOrder"]["id"] = so_id
        else:
            base["salesOrder"] = {"id": so_id, "number": f"PO/{so_id}"}
    else:
        base["salesOrderId"] = None
        base["salesOrder"] = None
    payload["detailItem"] = [base]
    return payload


def _sj_valid(sj_id: int, *, so_id: int = 95106) -> dict[str, Any]:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = sj_id
    payload["number"] = f"TEST-SJ/OK/{sj_id}"
    payload["transDate"] = "21/11/2026"
    item = copy.deepcopy(payload["detailItem"][0])
    item["id"] = int(f"{sj_id}1")
    item["salesOrderId"] = so_id
    if isinstance(item.get("salesOrder"), dict):
        item["salesOrder"]["id"] = so_id
        item["salesOrder"]["number"] = f"PO/{so_id}"
    payload["detailItem"] = [item]
    return payload


def _sj_multi(sj_id: int) -> dict[str, Any]:
    payload = _sj_valid(sj_id, so_id=95106)
    second = copy.deepcopy(payload["detailItem"][0])
    second["id"] = int(f"{sj_id}2")
    second["salesOrderId"] = 99901
    if isinstance(second.get("salesOrder"), dict):
        second["salesOrder"]["id"] = 99901
        second["salesOrder"]["number"] = "PO/99901"
    payload["detailItem"].append(second)
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


def test_sj_120851_like_quarantines_zero_writes(sdb: Database) -> None:
    detail = _sj_like_120851(120851)
    result = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert result["outcome"] == "review_required"
    assert result["reason"] == REASON_DELIVERY_NOTE_IDENTITY
    assert result.get("writes") == 0
    assert result.get("quarantined") is True
    assert deliveries.get_delivery_note(sdb, "120851") is None
    review = get_sj_sync_review(sdb, "120851")
    assert review is not None
    assert review["status"] == "open"
    assert review["reason"] == REASON_DELIVERY_NOTE_IDENTITY
    assert review["retryable"] is True
    assert review["last_error_code"] == "DELIVERY_NOTE_IDENTITY"
    assert "accurate_line_id" in (review.get("last_error_detail") or "")
    assert review.get("next_retry_at") is not None
    # Tidak eligible segera (cooldown identity).
    assert review_is_eligible(review) is False


def test_identity_quarantine_write_failure_page_failed(
    sdb: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_po(sdb)
    bad = _sj_like_120851(120851)
    ok = _sj_valid(120852)

    def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("simulated_review_write_failure")

    monkeypatch.setattr(
        "app.services.accurate_auto_sync.sj_sync_review.upsert_sj_sync_review",
        _boom,
    )
    client = _Client(
        {120851: bad, 120852: ok},
        {
            1: [
                {"id": 120851, "transDate": "21/11/2026"},
                {"id": 120852, "transDate": "21/11/2026"},
            ]
        },
    )
    result = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=20, max_pages=5, max_rows=50
    )
    assert result["ok"] is False
    assert result.get("stopped_reason") == "page_failed"
    assert result["counters"]["failed"] >= 1
    assert deliveries.get_delivery_note(sdb, "120851") is None
    assert deliveries.get_delivery_note(sdb, "120852") is None
    assert get_sj_sync_review(sdb, "120851") is None
    cp = load_job_checkpoint(sdb, "sj", contract_status=sj_job.SJ_CONTRACT_STATUS)
    # Checkpoint tidak maju melewati item gagal yang belum tercatat.
    assert int(cp.get("next_page") or 1) == 1
    assert cp.get("last_completed_page") in (None, 0)


def test_row_after_identity_gap_still_processed(sdb: Database) -> None:
    _seed_po(sdb)
    bad = _sj_like_120851(120851)
    ok = _sj_valid(120860)
    client = _Client(
        {120851: bad, 120860: ok},
        {
            1: [
                {"id": 120851, "transDate": "21/11/2026"},
                {"id": 120860, "transDate": "21/11/2026"},
            ]
        },
    )
    result = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=20, max_pages=5, max_rows=50
    )
    assert result["counters"]["review_required"] >= 1
    assert result["counters"]["created"] >= 1
    assert result["counters"]["failed"] == 0
    assert result.get("stopped_reason") != "page_failed"
    assert deliveries.get_delivery_note(sdb, "120851") is None
    assert deliveries.get_delivery_note(sdb, "120860") is not None
    assert get_sj_sync_review(sdb, "120851") is not None
    cp = load_job_checkpoint(sdb, "sj", contract_status=sj_job.SJ_CONTRACT_STATUS)
    assert int(cp.get("next_page") or 0) >= 1
    assert cp.get("last_completed_page") == 1 or cp.get("backfill_complete") is True


def test_identity_retry_cooldown_idempotent(sdb: Database) -> None:
    detail = _sj_like_120851(120851)
    first = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    second = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert first["outcome"] == second["outcome"] == "review_required"
    assert sdb["accurate_sj_sync_reviews"].count_documents({}) == 1
    review = get_sj_sync_review(sdb, "120851")
    assert int(review["seen_count"]) >= 2
    assert review["reason"] == REASON_DELIVERY_NOTE_IDENTITY
    assert review_is_eligible(review) is False


def test_multi_so_without_parents_defers_not_identity_quarantine(sdb: Database) -> None:
    detail = _sj_multi(119068)
    result = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert result["outcome"] == "skipped"
    assert result["reason"] == REASON_PO_NOT_REGISTERED
    assert result.get("writes") == 0
    assert deliveries.get_delivery_note(sdb, "119068") is None
    review = get_sj_sync_review(sdb, "119068")
    assert review["reason"] == REASON_PO_NOT_REGISTERED


def test_po_not_registered_still_deferred(sdb: Database) -> None:
    detail = _sj_valid(880901)
    first = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert first["outcome"] == "skipped"
    assert first["reason"] == REASON_PO_NOT_REGISTERED
    assert first.get("deferred") is True
    assert deliveries.get_delivery_note(sdb, "880901") is None


def test_no_false_dn_loading_delivery_accepted_payment(sdb: Database) -> None:
    _seed_po(sdb)
    delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, _sj_like_120851(120851), mongo_guard="test"
    )
    assert deliveries.get_delivery_note(sdb, "120851") is None
    po = get_by_accurate_id(sdb, "95106")
    assert (po.get("operational") or {}).get("acceptance", {}).get("status") != "accepted"
    assert sdb["loading_events"].count_documents({}) == 0
    assert sdb["delivery_events"].count_documents({}) == 0
    assert sdb["payment_events"].count_documents({}) == 0


def test_network_failure_still_fatal_not_quarantine(sdb: Database) -> None:
    _seed_po(sdb)
    detail = _sj_valid(120880)
    client = _Client(
        {120880: detail},
        {1: [{"id": 120880, "transDate": "21/11/2026"}]},
    )
    client.fail_detail.add("120880")
    result = sj_job.run_sj_auto_sync(
        sdb, client=client, mongo_guard="test", page_size=10, max_pages=1, max_rows=10
    )
    assert result["ok"] is False
    assert result.get("stopped_reason") == "page_failed"
    assert get_sj_sync_review(sdb, "120880") is None
    assert deliveries.get_delivery_note(sdb, "120880") is None


def test_so_id_conflict_quarantines_as_identity(sdb: Database) -> None:
    detail = _sj_valid(120900, so_id=95106)
    item = detail["detailItem"][0]
    item["salesOrderId"] = 111
    item["salesOrder"] = {"id": 222, "number": "PO/222"}
    result = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert result["outcome"] == "review_required"
    assert result["reason"] == REASON_DELIVERY_NOTE_IDENTITY
    assert result.get("last_error_code") == "SO_ID_CONFLICT"
    review = get_sj_sync_review(sdb, "120900")
    assert review["last_error_code"] == "SO_ID_CONFLICT"
    assert deliveries.get_delivery_note(sdb, "120900") is None
