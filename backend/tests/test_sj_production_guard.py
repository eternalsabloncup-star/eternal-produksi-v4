"""Jalur production SJ: default OFF, fail-closed, terpisah dari guard test."""

from __future__ import annotations

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.db import close_client
from app.db_guard import UnsafeMongoTargetError
from app.services import delivery_sync
from app.services.accurate_auto_sync import sj_job


OWNER = {
    "email_normalized": "owner@example.com",
    "role": "super_admin",
}


def _detail(sj_id: int = 880101) -> dict:
    return {
        "id": sj_id,
        "number": f"DO-{sj_id}",
        "transDate": "20/11/2026",
        "detailItem": [
            {
                "salesOrderId": 95106,
                "salesOrderDetailId": 1,
                "quantity": 1,
                "item": {"no": "X"},
            }
        ],
    }


def test_resolve_guard_defaults_to_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    clear_settings_cache()
    assert delivery_sync.resolve_sj_mongo_guard() == "test"
    note = sj_job.sj_auto_sync_settings_note()
    assert note["sj_sync_production_enabled"] is False
    assert note["resolved_mongo_guard"] == "test"


def test_resolve_guard_production_requires_both_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    clear_settings_cache()
    assert delivery_sync.resolve_sj_mongo_guard() == "test"

    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "1")
    clear_settings_cache()
    assert delivery_sync.resolve_sj_mongo_guard() == "production"


def test_production_guard_blocked_when_flag_off(
    mongo_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "MONGODB_URI",
        "mongodb://eternal:eternal_dev_password@127.0.0.1:27018/"
        "?authSource=eternal_v4",
    )
    monkeypatch.setenv("MONGODB_DB", "eternal_v4")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    clear_settings_cache()
    close_client()
    ops = mongo_client["eternal_v4"]
    try:
        with pytest.raises(UnsafeMongoTargetError) as ei:
            delivery_sync.sync_delivery_order_payload(
                ops, OWNER, _detail(), mongo_guard="production"
            )
        assert ei.value.code == "mongo_sj_production_flag_off"
    finally:
        mongo_client.drop_database("eternal_v4")
        close_client()
        clear_settings_cache()


def test_production_guard_requires_production_env_and_port(
    mongo_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv(
        "MONGODB_URI",
        "mongodb://eternal:eternal_dev_password@127.0.0.1:27018/"
        "?authSource=eternal_v4",
    )
    monkeypatch.setenv("MONGODB_DB", "eternal_v4")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "1")
    clear_settings_cache()
    close_client()
    ops = mongo_client["eternal_v4"]
    try:
        with pytest.raises(UnsafeMongoTargetError) as ei:
            delivery_sync.sync_delivery_order_payload(
                ops, OWNER, _detail(), mongo_guard="production"
            )
        assert ei.value.code == "mongo_prod_env_required"
    finally:
        mongo_client.drop_database("eternal_v4")
        close_client()
        clear_settings_cache()


def test_sj_auto_sync_disabled_zero_writes(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    clear_settings_cache()
    out = sj_job.run_sj_auto_sync(db)
    assert out["enabled"] is False
    assert out["accurate_requests"] == 0
    assert out["writes"] == 0
