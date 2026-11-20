"""Sinkronisasi manual Surat Jalan Accurate → Mongo test (mock HTTP)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.db_guard import UnsafeMongoTargetError
from app.integrations.accurate.client import PATH_API_TOKEN, AccurateReadClient
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import delivery_repository as deliveries
from app.repositories.po_repository import upsert_sales_order
from app.integrations.accurate.normalize import normalize_sales_order
from app.services import delivery_sync

ACCOUNT = "https://account.accurate.test"
DB_HOST = "https://public.accurate.test"
LIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"
)
PASSWORD = "password-kuat-123"
OWNER = {"email_normalized": "owner@eternal.local", "role": "super_admin"}


@pytest.fixture(autouse=True)
def _enable_sj_sync_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uji sync menyalakan kill switch; default produksi tetap OFF."""
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    from app.config import clear_settings_cache

    clear_settings_cache()
    yield
    clear_settings_cache()


def _detail(sj_id: int = 880101, **overrides) -> dict:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = sj_id
    payload["number"] = f"TEST-SJ/SYNC/{sj_id}"
    for item in payload["detailItem"]:
        item["id"] = int(f"{sj_id}{item['id'] % 1000}")
    payload.update(overrides)
    return payload


def _seed_po(db: Database) -> None:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))


def _client_for(pages: dict[int, list[dict]], details: dict[int, dict]) -> AccurateReadClient:
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
            if sj_id not in details:
                return httpx.Response(404, json={"s": False})
            return httpx.Response(200, json={"s": True, "d": details[sj_id]})
        # Tolak write path apa pun.
        if any(x in request.url.path for x in ("save.do", "delete.do", "bulk-save.do")):
            return httpx.Response(500, json={"s": False, "message": "write-forbidden"})
        return httpx.Response(404, json={"s": False})

    return AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token="t",
        signature_secret="s",
        transport=httpx.MockTransport(handler),
    )


def test_pagination_beyond_first_twenty(db: Database) -> None:
    assert db.name.startswith("eternal_v4_test_")
    _seed_po(db)
    page1 = [{"id": i, "number": f"SJ-{i}"} for i in range(1, 21)]
    page2 = [{"id": i, "number": f"SJ-{i}"} for i in range(21, 26)]
    details = {i: _detail(i) for i in range(1, 26)}
    client = _client_for({1: page1, 2: page2}, details)

    result = delivery_sync.sync_delivery_orders_from_accurate(
        db,
        OWNER,
        client=client,
        page_size=20,
        max_pages=5,
        max_rows=100,
        scope="test-pagination",
    )
    assert result["pages"] == 2
    assert result["listed"] == 25
    assert result["created"] == 25
    assert result["auto_sync"] is False
    assert result["writes_accurate"] is False
    assert result["deletes_missing_sj"] is False
    assert deliveries.list_delivery_notes(db, accurate_po_id="95106")
    assert len(deliveries.list_delivery_notes(db, accurate_po_id="95106")) == 25


def test_resync_idempotent_no_duplicate(db: Database) -> None:
    _seed_po(db)
    details = {880101: _detail(880101)}
    client = _client_for({1: [{"id": 880101}]}, details)
    first = delivery_sync.sync_delivery_orders_from_accurate(
        db, OWNER, client=client, scope="idem-1"
    )
    second = delivery_sync.sync_delivery_orders_from_accurate(
        db, OWNER, client=client, scope="idem-2"
    )
    assert first["created"] == 1
    assert second["unchanged"] == 1
    assert second["created"] == 0
    notes = deliveries.list_delivery_notes(db, accurate_po_id="95106")
    assert len(notes) == 1
    assert notes[0]["sj_version"] == 1


def test_detail_change_triggers_revision_flow(db: Database) -> None:
    _seed_po(db)
    original = _detail(880101)
    revised = copy.deepcopy(original)
    revised["detailItem"][0]["quantity"] = 99
    client1 = _client_for({1: [{"id": 880101}]}, {880101: original})
    delivery_sync.sync_delivery_orders_from_accurate(
        db, OWNER, client=client1, scope="rev-1"
    )
    client2 = _client_for({1: [{"id": 880101}]}, {880101: revised})
    result = delivery_sync.sync_delivery_orders_from_accurate(
        db, OWNER, client=client2, scope="rev-2"
    )
    assert result["updated"] == 1
    note = deliveries.get_delivery_note(db, "880101")
    assert note is not None
    assert note["sj_version"] == 2
    assert note["items"][0]["qty"] == 99.0
    revisions = deliveries.list_note_revisions(db, "880101")
    assert len(revisions) == 2


def test_unrelated_po_is_skipped(db: Database) -> None:
    # Tanpa seed PO 95106 → skip.
    details = {880101: _detail(880101)}
    client = _client_for({1: [{"id": 880101}]}, details)
    result = delivery_sync.sync_delivery_orders_from_accurate(
        db, OWNER, client=client, scope="skip-po"
    )
    assert result["skipped"] == 1
    assert result["created"] == 0
    assert deliveries.get_delivery_note(db, "880101") is None


def test_partial_failure_does_not_corrupt_others(db: Database) -> None:
    _seed_po(db)
    good = _detail(880101)
    bad = _detail(880102)
    bad["detailItem"][0].pop("id")
    client = _client_for(
        {1: [{"id": 880101}, {"id": 880102}]},
        {880101: good, 880102: bad},
    )
    result = delivery_sync.sync_delivery_orders_from_accurate(
        db, OWNER, client=client, scope="partial"
    )
    assert result["created"] == 1
    assert result["failed"] == 1
    assert deliveries.get_delivery_note(db, "880101") is not None
    assert deliveries.get_delivery_note(db, "880102") is None


def test_concurrent_sync_lock(db: Database) -> None:
    delivery_sync.acquire_delivery_sync_lock(db, scope="lock-test")
    with pytest.raises(delivery_sync.DeliverySyncInProgressError):
        delivery_sync.acquire_delivery_sync_lock(db, scope="lock-test")
    delivery_sync.release_delivery_sync_lock(db, scope="lock-test")
    delivery_sync.acquire_delivery_sync_lock(db, scope="lock-test")
    delivery_sync.release_delivery_sync_lock(db, scope="lock-test")


def test_production_mongo_guard_blocks_write(
    mongo_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import clear_settings_cache
    from app.db import close_client

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "MONGODB_URI",
        "mongodb://eternal:eternal_dev_password@127.0.0.1:27018/"
        "?authSource=eternal_v4",
    )
    monkeypatch.setenv("MONGODB_DB", "eternal_v4")
    clear_settings_cache()
    close_client()
    ops = mongo_client["eternal_v4"]
    try:
        with pytest.raises(UnsafeMongoTargetError):
            delivery_sync.sync_delivery_order_payload(
                ops, OWNER, _detail(880101), mongo_guard="test"
            )
    finally:
        mongo_client.drop_database("eternal_v4")
        close_client()
        clear_settings_cache()


def test_no_auto_sync_flag_in_result(db: Database) -> None:
    _seed_po(db)
    client = _client_for({1: []}, {})
    result = delivery_sync.sync_delivery_orders_from_accurate(
        db, OWNER, client=client, scope="empty"
    )
    assert result["auto_sync"] is False
    assert result["listed"] == 0


def _login(client: TestClient, email: str) -> str:
    res = client.post(
        "/api/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert res.status_code == 200
    return res.json()["csrf_token"]


def test_kill_switch_blocks_sync_when_disabled(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import clear_settings_cache

    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    clear_settings_cache()
    with pytest.raises(delivery_sync.DeliverySyncDisabledError) as exc:
        delivery_sync.sync_delivery_order_payload(db, OWNER, _detail(880101))
    assert exc.value.code == "SJ_SYNC_DISABLED"


def test_preview_db_name_is_not_accepted_as_sync_target(
    mongo_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nama non eternal_v4_test_* (mis. preview) ditolak — jangan tebak isolasi dari host saja."""
    from app.config import clear_settings_cache
    from app.db import close_client

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "MONGODB_URI",
        "mongodb://eternal:eternal_dev_password@127.0.0.1:27017/"
        "?authSource=admin&replicaSet=rs0&directConnection=true",
    )
    monkeypatch.setenv("MONGODB_DB", "eternal_v4_preview_pr6")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    clear_settings_cache()
    close_client()
    preview = mongo_client["eternal_v4_preview_pr6"]
    with pytest.raises(UnsafeMongoTargetError):
        delivery_sync.sync_delivery_order_payload(
            preview, OWNER, _detail(880101), mongo_guard="test"
        )


def test_sync_api_permission_denied_for_ordinary_user(
    app_client: TestClient, db: Database
) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    db["users"].insert_one(
        {
            "email": "driver@eternal.local",
            "email_normalized": normalize_email("driver@eternal.local"),
            "display_name": "Driver",
            "role": "user",
            "operational_role": "driver",
            "password_hash": hash_password(PASSWORD),
            "is_active": True,
            "effective_module_permissions": ["access.all_branches"]
        }
    )
    csrf = _login(app_client, "driver@eternal.local")
    denied = app_client.post(
        "/api/delivery/sync/accurate",
        headers={"X-CSRF-Token": csrf},
        json={"page_size": 20, "max_pages": 1, "max_rows": 10},
    )
    assert denied.status_code == 403


def test_sync_api_blocked_when_kill_switch_off(
    app_client: TestClient, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import clear_settings_cache

    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    clear_settings_cache()
    csrf = _login(app_client, "owner@eternal.local")
    denied = app_client.post(
        "/api/delivery/sync/accurate",
        headers={"X-CSRF-Token": csrf},
        json={"page_size": 20, "max_pages": 1, "max_rows": 10},
    )
    assert denied.status_code == 403
    assert "V4_SJ_SYNC_ENABLED" in denied.json()["detail"]
