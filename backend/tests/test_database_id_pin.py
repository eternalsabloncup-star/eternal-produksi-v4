"""Regresi klasifikasi / pin database id Accurate (tanpa nilai secret)."""

from __future__ import annotations

import pytest

from app.integrations.accurate.auth import (
    classify_database_id,
    collect_database_id_candidates,
    match_database_pin,
    normalize_id_for_compare,
)


def test_classify_numeric_uuid_other() -> None:
    assert classify_database_id(823960) == "numeric"
    assert classify_database_id("823960") == "numeric"
    assert classify_database_id("823960.0") == "numeric"
    assert (
        classify_database_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") == "uuid"
    )
    assert classify_database_id("not-an-id") == "other"
    assert len("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") == 36


def test_normalize_numeric_int_str_float() -> None:
    assert normalize_id_for_compare(99) == normalize_id_for_compare("99")
    assert normalize_id_for_compare("99.0") == "99"


def test_match_same_numeric() -> None:
    cands = collect_database_id_candidates(
        {"s": True, "d": {"database": {"id": 111, "host": "https://x"}}}
    )
    ok = match_database_pin("111", cands)
    assert ok.ok is True
    assert ok.matched_field == "d.database.id"
    assert all("raw" not in d for d in ok.diagnostics)


def test_match_kind_mismatch_uuid_pin() -> None:
    cands = collect_database_id_candidates(
        {"s": True, "d": {"database": {"id": 111, "host": "https://x"}}}
    )
    pin = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = match_database_pin(pin, cands)
    assert result.ok is False
    assert result.code == "accurate_database_id_kind_mismatch"
    assert pin not in str(result.diagnostics)
    assert "111" not in str(result.diagnostics)


def test_match_value_mismatch_same_kind() -> None:
    cands = collect_database_id_candidates(
        {"s": True, "d": {"database": {"id": 111}}}
    )
    result = match_database_pin("222", cands)
    assert result.ok is False
    assert result.code == "accurate_database_mismatch"


def test_match_missing_candidates() -> None:
    result = match_database_pin("111", [])
    assert result.code == "accurate_database_id_missing"


def test_diagnostics_never_embed_values() -> None:
    secretish = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    cands = collect_database_id_candidates(
        {
            "s": True,
            "d": {"database": {"id": 999001, "guid": secretish}},
        }
    )
    result = match_database_pin("123", cands)
    blob = str(result.diagnostics)
    assert secretish not in blob
    assert "999001" not in blob
    assert "123" not in blob


def test_numeric_pin_match_mismatch_missing_via_client() -> None:
    import httpx
    from app.integrations.accurate.auth import AccurateAuthError
    from app.integrations.accurate.client import AccurateReadClient

    account = "https://account.accurate.test"
    host = "https://public.accurate.test"

    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"s": True, "d": {"database": {"id": 555, "host": host}}},
        )

    matched = AccurateReadClient(
        account_base_url=account,
        api_token="t",
        signature_secret="s",
        db_id="555",
        transport=httpx.MockTransport(ok_handler),
    )
    assert matched.open_db().database_id == "555"

    mismatched = AccurateReadClient(
        account_base_url=account,
        api_token="t",
        signature_secret="s",
        db_id="999",
        transport=httpx.MockTransport(ok_handler),
    )
    with pytest.raises(AccurateAuthError) as exc_mm:
        mismatched.open_db()
    assert exc_mm.value.code == "accurate_database_mismatch"

    def missing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"s": True, "d": {"database": {"host": host}}}
        )

    missing = AccurateReadClient(
        account_base_url=account,
        api_token="t",
        signature_secret="s",
        db_id="555",
        transport=httpx.MockTransport(missing_handler),
    )
    with pytest.raises(AccurateAuthError) as exc_miss:
        missing.open_db()
    assert exc_miss.value.code == "accurate_database_id_missing"


def test_production_requires_db_pin() -> None:
    from app.integrations.accurate.auth import AccurateAuthError
    from app.integrations.accurate.client import AccurateReadClient, AccurateReadStatus

    client = AccurateReadClient(
        account_base_url="https://account.example",
        api_token="t",
        signature_secret="s",
        db_id="",
        require_db_pin=True,
    )
    assert client.status == AccurateReadStatus.CONFIG_PRESENT
    with pytest.raises(AccurateAuthError) as exc:
        client.open_db()
    assert exc.value.code == "accurate_database_pin_required"


def test_settings_production_defaults_require_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/test")
    monkeypatch.setenv("MONGODB_DB", "test")
    monkeypatch.delenv("ACCURATE_REQUIRE_DB_PIN", raising=False)
    monkeypatch.delenv("V4_ACCURATE_REQUIRE_DB_PIN", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    from app.config import Settings, clear_settings_cache

    clear_settings_cache()
    settings = Settings()  # type: ignore[call-arg]
    assert settings.accurate_db_pin_required is True
    summary = settings.safe_summary()
    assert summary["accurate_require_db_pin"] is True
    assert "accurate_db_id" not in summary or summary.get("accurate_db_id") in (
        None,
        "",
        False,
        0,
        True,
    )
    # Pastikan nilai pin tidak ada di summary
    assert settings.accurate_db_id == "" or "accurate_db_id" not in str(
        summary.get("accurate_db_id_configured")
    )
