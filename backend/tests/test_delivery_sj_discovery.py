"""Discovery SJ↔SO read-only + targeted sync (mock Accurate)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pymongo.database import Database

from app.integrations.accurate.client import PATH_API_TOKEN, AccurateReadClient
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories.po_repository import upsert_sales_order
from app.services import delivery_sync
from app.services.delivery_sj_discovery import discover_sj_so_links

ACCOUNT = "https://account.accurate.test"
DB_HOST = "https://public.accurate.test"
LIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"
)
OWNER = {"email_normalized": "owner@eternal.local", "role": "super_admin"}


@pytest.fixture(autouse=True)
def _enable_sj_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    yield
    clear_settings_cache()


def _detail(sj_id: int, so_id: int = 95106, **overrides) -> dict:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = sj_id
    payload["number"] = f"TEST-SJ/{sj_id}"
    for item in payload["detailItem"]:
        item["salesOrderId"] = so_id
        if isinstance(item.get("salesOrder"), dict):
            item["salesOrder"]["id"] = so_id
            item["salesOrder"]["number"] = f"PO/{so_id}"
        item["id"] = int(f"{sj_id}{item['id'] % 1000}")
    payload.update(overrides)
    return payload


def _multi_so_detail(sj_id: int) -> dict:
    payload = _detail(sj_id, so_id=95106)
    payload["detailItem"][1]["salesOrderId"] = 99999
    payload["detailItem"][1]["salesOrder"] = {
        "id": 99999,
        "number": "PO/OTHER",
        "poNumber": "X",
    }
    return payload


def _client(pages: dict[int, list], details: dict[int, dict]) -> AccurateReadClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(PATH_API_TOKEN):
            return httpx.Response(
                200,
                json={"s": True, "d": {"database": {"id": 1, "host": DB_HOST}}},
            )
        if "delivery-order/list.do" in request.url.path:
            page = int(request.url.params.get("sp.page") or "1")
            return httpx.Response(200, json={"s": True, "d": pages.get(page, [])})
        if "delivery-order/detail.do" in request.url.path:
            sj_id = int(request.url.params.get("id"))
            return httpx.Response(200, json={"s": True, "d": details[sj_id]})
        if any(x in request.url.path for x in ("save.do", "delete.do", "bulk-save.do")):
            return httpx.Response(500, json={"s": False})
        return httpx.Response(404, json={"s": False})

    return AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token="t",
        signature_secret="s",
        transport=httpx.MockTransport(handler),
    )


def test_discovery_prefers_seed_po_and_rejects_multi_so() -> None:
    details = {
        1: _detail(1, so_id=77777),
        2: _multi_so_detail(2),
        3: _detail(3, so_id=95106),
        4: _detail(4, so_id=95106),
    }
    client = _client(
        {1: [{"id": i} for i in (1, 2, 3, 4)]},
        details,
    )
    report = discover_sj_so_links(
        client=client, prefer_po_id="95106", page_size=20, max_pages=1
    )
    assert report["multi_so_rejected"] == 1
    assert report["prefer_po_sj_count"] == 2
    assert report["selected_po_id"] == "95106"
    assert set(report["selected_sj_ids"]) == {"3", "4"}
    assert report["selection_reason"] == "prefer_po_match"
    assert report["writes_accurate"] is False


def test_discovery_fallback_when_prefer_po_missing() -> None:
    details = {10: _detail(10, so_id=4242)}
    client = _client({1: [{"id": 10}]}, details)
    report = discover_sj_so_links(
        client=client, prefer_po_id="95106", page_size=20, max_pages=1
    )
    assert report["prefer_po_sj_count"] == 0
    assert report["selected_po_id"] == "4242"
    assert report["selected_sj_ids"] == ["10"]
    assert report["selection_reason"] == "prefer_po_miss_fallback_eligible"


def test_targeted_sync_idempotent_for_discovered_sj(db: Database) -> None:
    assert db.name.startswith("eternal_v4_test_")
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    details = {880101: _detail(880101, so_id=95106)}
    client = _client({1: [{"id": 880101}]}, details)
    first = delivery_sync.sync_selected_delivery_orders_from_accurate(
        db, OWNER, ["880101"], client=client, scope="sel-1"
    )
    second = delivery_sync.sync_selected_delivery_orders_from_accurate(
        db, OWNER, ["880101"], client=client, scope="sel-2"
    )
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["unchanged"] == 1
    notes = deliveries.list_delivery_notes(db, accurate_po_id="95106")
    assert len(notes) == 1


def test_list_sync_reports_po_not_registered_skip_reason(db: Database) -> None:
    details = {880101: _detail(880101, so_id=95106)}
    client = _client({1: [{"id": 880101}]}, details)
    result = delivery_sync.sync_delivery_orders_from_accurate(
        db, OWNER, client=client, scope="skip-reason"
    )
    assert result["skipped"] == 1
    assert result["skip_reasons"].get("po_not_registered") == 1
    assert result["created"] == 0


def test_multi_so_still_fail_closed_on_targeted_sync(db: Database) -> None:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    details = {9: _multi_so_detail(9)}
    client = _client({1: [{"id": 9}]}, details)
    result = delivery_sync.sync_selected_delivery_orders_from_accurate(
        db, OWNER, ["9"], client=client, scope="multi"
    )
    assert result["failed"] == 1
    assert result["created"] == 0
    assert any(
        f.get("code") == "MULTI_SO_NOT_SUPPORTED" for f in result["failures"]
    )
