"""Config validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_missing_mongodb_uri_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.setenv("MONGODB_DB", "eternal_produksi_v4_test")
    from app.config import Settings, clear_settings_cache

    clear_settings_cache()
    with pytest.raises((ValidationError, Exception)):
        Settings()  # type: ignore[call-arg]


def test_cookie_secure_derived_for_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/test")
    monkeypatch.setenv("MONGODB_DB", "test")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    from app.config import Settings, clear_settings_cache

    clear_settings_cache()
    settings = Settings()  # type: ignore[call-arg]
    assert settings.cookie_secure is True
    summary = settings.safe_summary()
    assert "mongodb_uri" not in summary
