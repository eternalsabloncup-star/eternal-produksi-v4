"""Integrasi Accurate read client → sync Mongo V4 (mock HTTP, DB test)."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import httpx
import pytest
from pymongo.database import Database

from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.org_master_repository import (
    ensure_org_master_indexes,
    upsert_branch,
)
from app.repositories.po_repository import get_by_accurate_id, po_collection
from app.services.po_sync import (
    sync_one_sales_order_from_accurate,
    sync_sales_order_payload,
)


ACCOUNT = "https://account.accurate.test"
DB_HOST = "https://public.accurate.test"


def _sample_with_branch(branch_id: str | int = "50") -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["branchId"] = int(branch_id) if str(branch_id).isdigit() else branch_id
    return payload


@pytest.fixture()
def sync_db(db: Database) -> Database:
    ensure_org_master_indexes(db)
    upsert_branch(db, code="50", name="Yogyakarta", is_active=True)
    upsert_branch(db, code="150", name="Semarang", is_active=True)
    return db


def _accurate_handler(payload: dict | None = None):
    body = payload if payload is not None else _sample_with_branch()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/api-token.do"):
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {"database": {"id": 1, "host": DB_HOST}},
                },
            )
        if "sales-order/detail.do" in request.url.path:
            return httpx.Response(200, json={"s": True, "d": body})
        return httpx.Response(404, json={"s": False})

    return handler


def test_sync_from_accurate_idempotent(sync_db: Database) -> None:
    assert sync_db.name.startswith("eternal_v4_test_")
    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token="t",
        signature_secret="s",
        transport=httpx.MockTransport(_accurate_handler()),
    )
    first = sync_one_sales_order_from_accurate(sync_db, 95106, client=client)
    second = sync_one_sales_order_from_accurate(sync_db, 95106, client=client)
    assert first["was_insert"] is True
    assert second["was_insert"] is False
    assert first["duplicate_prevented"] is True
    assert second["count_after"] == 1
    assert first["po_no"] == "PO/18/11/2026/000010"
    assert first["line_count"] == 2
    assert first["process_kinds"] == ["Cetak", "Non-Cetak"]
    # Tidak membocorkan pelanggan di hasil aman
    assert "Pelanggan" not in str(first)


def test_sync_handles_qty_add_remove(sync_db: Database) -> None:
    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token="t",
        signature_secret="s",
        transport=httpx.MockTransport(_accurate_handler()),
    )
    sync_one_sales_order_from_accurate(sync_db, 95106, client=client)

    changed = _sample_with_branch()
    changed["detailItem"][0]["quantity"] = 777
    changed["detailItem"].append(
        {
            "id": 106320,
            "quantity": 10,
            "item": {"no": "BRG-NEW", "name": "Baru"},
            "itemUnit": {"name": "PCS"},
            "charField1": "Cetak",
            "charField2": "Hitam",
        }
    )
    # hapus line 106319
    changed["detailItem"] = [
        d for d in changed["detailItem"] if d["id"] != 106319
    ]

    client2 = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token="t",
        signature_secret="s",
        transport=httpx.MockTransport(_accurate_handler(changed)),
    )
    result = sync_one_sales_order_from_accurate(sync_db, 95106, client=client2)
    assert "106320" in result["line_diff"]["added_line_ids"]
    assert "106319" in result["line_diff"]["removed_line_ids"]
    assert "106318" in result["line_diff"]["changed_line_ids"]
    stored = get_by_accurate_id(sync_db, "95106")
    assert stored is not None
    assert stored["line_ids"] == ["106318", "106320"]
    assert stored["header"]["items"][0]["qty"] == 777


def test_operational_not_wiped_on_resync(sync_db: Database) -> None:
    sample = _sample_with_branch()
    sync_sales_order_payload(sync_db, sample)
    po_collection(sync_db).update_one(
        {"accurate_id": "95106"},
        {"$set": {"operational": {"manual_note": "jangan-hapus", "stage": "inbox"}}},
    )
    again = sync_sales_order_payload(sync_db, sample)
    assert again["operational_preserved"] is True
    stored = get_by_accurate_id(sync_db, "95106")
    assert stored is not None
    assert stored["operational"]["manual_note"] == "jangan-hapus"
    assert stored["operational"]["stage"] == "inbox"


def test_accurate_api_error_propagates(db: Database) -> None:
    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"s": False})

    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token="t",
        signature_secret="s",
        transport=httpx.MockTransport(bad),
    )
    with pytest.raises(AccurateAuthError):
        sync_one_sales_order_from_accurate(db, 95106, client=client)


def test_sync_blocked_on_legacy_db_name(
    mongo_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import clear_settings_cache
    from app.db import close_client
    from app.db_guard import UnsafeMongoTargetError

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "MONGODB_URI",
        "mongodb://eternal:eternal_dev_password@127.0.0.1:27018/"
        "?authSource=eternal_v4",
    )
    monkeypatch.setenv("MONGODB_DB", "eternal_v4")
    clear_settings_cache()
    close_client()
    legacy = mongo_client["eternal_v4"]
    try:
        with pytest.raises(UnsafeMongoTargetError):
            sync_sales_order_payload(legacy, SAMPLE_SALES_ORDER_STRUCTURE)
    finally:
        mongo_client.drop_database("eternal_v4")
        close_client()
        clear_settings_cache()


def test_double_sync_po_count_remains_one(sync_db: Database) -> None:
    client = AccurateReadClient(
        account_base_url=ACCOUNT,
        api_token="t",
        signature_secret="s",
        transport=httpx.MockTransport(_accurate_handler()),
    )
    a = sync_one_sales_order_from_accurate(sync_db, "95106", client=client)
    b = sync_one_sales_order_from_accurate(sync_db, "95106", client=client)
    assert a["po_count"] == 1
    assert b["po_count"] == 1
    assert a["inserted"] is True
    assert b["inserted"] is False
    assert b["updated"] is True
    assert b["idempotent"] is True
    assert po_collection(sync_db).count_documents({"accurate_id": "95106"}) == 1


def test_manual_script_sync_mock(
    sync_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "accurate_sync_one_po.py"
    spec = importlib.util.spec_from_file_location("accurate_sync_one_po", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MONGODB_DB", sync_db.name)
    monkeypatch.setenv("V4_ACCURATE_ACCOUNT_BASE", ACCOUNT)
    monkeypatch.setenv("V4_ACCURATE_API_TOKEN", "t")
    monkeypatch.setenv("V4_ACCURATE_API_SIGNATURE_SECRET", "s")
    monkeypatch.setenv("V4_ACCURATE_DB_ID", "1")
    monkeypatch.setenv("V4_ACCURATE_REQUIRE_DB_PIN", "0")

    from app.config import clear_settings_cache
    from app.db import close_client

    clear_settings_cache()
    close_client()
    code = mod.run("95106", transport=httpx.MockTransport(_accurate_handler()))
    assert code == 0
    stored = get_by_accurate_id(sync_db, "95106")
    assert stored is not None
    assert stored["po_no"] == "PO/18/11/2026/000010"
