"""Production sync path — mocked Accurate; isolated test Mongo (no live ops writes)."""

from __future__ import annotations

import copy

import httpx
import pytest

from app.config import clear_settings_cache
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.po_repository import count_by_accurate_id, get_by_accurate_id
from app.services.po_sync_production import (
    ProductionSyncError,
    run_production_sync_one_po,
)


def _accurate_handler(payload=None):
    body = payload if payload is not None else SAMPLE_SALES_ORDER_STRUCTURE

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/api/api-token.do"):
            return httpx.Response(
                200,
                json={
                    "s": True,
                    "d": {
                        "host": "https://edge.accurate.test",
                        "session": "",
                        "database": {"id": 424242, "alias": "pin-ok"},
                    },
                },
            )
        if "sales-order" in path:
            return httpx.Response(200, json={"s": True, "d": body})
        return httpx.Response(404, json={"s": False})

    return handler


def _prod_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    uri = (
        "mongodb://eternal:eternal_dev_password@127.0.0.1:27018/"
        "eternal_v4?authSource=eternal_v4"
    )
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGODB_URI", uri)
    monkeypatch.setenv("MONGO_URL", uri)
    monkeypatch.setenv("MONGODB_DB", "eternal_v4")
    monkeypatch.setenv("DB_NAME", "eternal_v4")
    monkeypatch.setenv("V4_ACCURATE_ACCOUNT_BASE", "https://account.accurate.test")
    monkeypatch.setenv("V4_ACCURATE_API_TOKEN", "token-test")
    monkeypatch.setenv("V4_ACCURATE_API_SIGNATURE_SECRET", "secret-test")
    monkeypatch.setenv("V4_ACCURATE_DB_ID", "424242")
    monkeypatch.setenv("V4_ACCURATE_REQUIRE_DB_PIN", "1")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    clear_settings_cache()


def _bypass_prod_mongo_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard URI dicek ketat; tes memakai DB isolasi — bypass hanya assert produksi."""

    def _ok(**_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.po_sync_production.assert_safe_mongo_production_sync_target",
        _ok,
    )
    monkeypatch.setattr(
        "app.services.po_sync.assert_safe_mongo_production_sync_target",
        _ok,
    )


def _client():
    from app.integrations.accurate.client import AccurateReadClient

    return AccurateReadClient(
        account_base_url="https://account.accurate.test",
        api_token="token-test",
        signature_secret="secret-test",
        db_id="424242",
        require_db_pin=True,
        transport=httpx.MockTransport(_accurate_handler()),
    )


def test_dry_run_zero_writes(db, monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_settings_env(monkeypatch)
    _bypass_prod_mongo_guard(monkeypatch)
    before = db["accurate_sales_orders"].count_documents({})
    result = run_production_sync_one_po(
        db, 95106, client=_client(), dry_run=True
    )
    assert result["dry_run"] is True
    assert result["writes"] == 0
    assert result["executed"] is False
    assert result["line_count"] == 2
    assert "Cetak" in result["process_kinds"]
    assert db["accurate_sales_orders"].count_documents({}) == before


def test_apply_without_confirm_rejected(db, monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_settings_env(monkeypatch)
    _bypass_prod_mongo_guard(monkeypatch)
    with pytest.raises(ProductionSyncError) as exc:
        run_production_sync_one_po(
            db, 95106, client=_client(), dry_run=False, confirm_po_id=None
        )
    assert exc.value.code == "po_confirm_required"
    assert count_by_accurate_id(db, "95106") == 0


def test_wrong_pin_rejected(db, monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_settings_env(monkeypatch)
    monkeypatch.setenv("V4_ACCURATE_DB_ID", "not-a-number")
    clear_settings_cache()
    _bypass_prod_mongo_guard(monkeypatch)
    with pytest.raises(ProductionSyncError) as exc:
        run_production_sync_one_po(db, 95106, dry_run=True)
    assert exc.value.code == "accurate_database_pin_invalid"


def test_apply_two_lines_and_idempotent(db, monkeypatch: pytest.MonkeyPatch) -> None:
    _prod_settings_env(monkeypatch)
    _bypass_prod_mongo_guard(monkeypatch)
    client = _client()

    first = run_production_sync_one_po(
        db, "95106", client=client, dry_run=False, confirm_po_id="95106"
    )
    assert first["executed"] is True
    assert first["line_count"] == 2
    assert first["inserted"] is True
    assert first["count_after"] == 1
    stored = get_by_accurate_id(db, "95106")
    assert stored is not None
    stored["operational"] = {"note": "keep-me"}
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "95106"},
        {"$set": {"operational": {"note": "keep-me"}}},
    )

    second = run_production_sync_one_po(
        db, "95106", client=client, dry_run=False, confirm_po_id="95106"
    )
    assert second["inserted"] is False
    assert second["count_after"] == 1
    assert count_by_accurate_id(db, "95106") == 1
    again = get_by_accurate_id(db, "95106")
    assert again["operational"] == {"note": "keep-me"}
    assert second["operational_preserved"] is True


def test_port_27017_rejected_without_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db_guard import UnsafeMongoTargetError, assert_safe_mongo_production_sync_target

    with pytest.raises(UnsafeMongoTargetError):
        assert_safe_mongo_production_sync_target(
            uri="mongodb://u:p@127.0.0.1:27017/eternal_v4?authSource=eternal_v4",
            db_name="eternal_v4",
            app_env="production",
        )


def test_script_apply_without_confirm_exits(monkeypatch: pytest.MonkeyPatch, db) -> None:
    import importlib.util
    from pathlib import Path

    from app.config import get_settings

    _prod_settings_env(monkeypatch)
    _bypass_prod_mongo_guard(monkeypatch)

    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "accurate_sync_one_po_production.py"
    )
    spec = importlib.util.spec_from_file_location("prod_sync_script", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(
        "app.db.get_db",
        lambda _settings=None: db,
    )
    monkeypatch.setattr(
        "app.services.po_sync_production._build_reader",
        lambda cfg, client=None: _client(),
    )

    code = mod.main(["95106", "--apply"])
    assert code == 4  # ProductionSyncError po_confirm_required
    assert count_by_accurate_id(db, "95106") == 0
    _ = get_settings  # settings loaded via env in main
