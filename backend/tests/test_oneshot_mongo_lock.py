"""One-shot SJ: backend.env production 27018 tidak boleh jadi target tulis."""

from __future__ import annotations

import pytest

from app.config import clear_settings_cache, get_settings
from app.db_guard import UnsafeMongoTargetError, extract_mongo_ports
from app.oneshot_mongo_lock import (
    assert_effective_sj_oneshot_mongo_target,
    pin_sj_oneshot_test_mongo_env,
)

_PROD_URI = (
    "mongodb://eternal_v4:prod-secret-do-not-print@127.0.0.1:27018/"
    "eternal_v4?authSource=eternal_v4"
)
_TEST_URI = (
    "mongodb://eternal_v4_test_sync:test-secret-do-not-print@127.0.0.1:27019/"
    "eternal_v4_test_sj_activation"
    "?authSource=eternal_v4_test_sj_activation"
    "&replicaSet=eternalV4TestRS"
    "&directConnection=true"
)


def _accurate_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv(
        "V4_ACCURATE_ACCOUNT_BASE", "https://account.accurate.id"
    )
    monkeypatch.setenv("V4_ACCURATE_API_TOKEN", "token-not-printed")
    monkeypatch.setenv("V4_ACCURATE_API_SIGNATURE_SECRET", "sig-not-printed")
    monkeypatch.setenv("V4_ACCURATE_DB_ID", "12345")
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")


def test_pin_overrides_production_backend_env_mongo_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulasi source backend.env (27018) lalu pin test — settings wajib 27019."""
    _accurate_live_env(monkeypatch)
    # Seperti isi /etc/eternal-v4/backend/backend.env produksi:
    monkeypatch.setenv("MONGO_URL", _PROD_URI)
    monkeypatch.setenv("MONGODB_URI", _PROD_URI)
    monkeypatch.setenv("DB_NAME", "eternal_v4")
    monkeypatch.setenv("MONGODB_DB", "eternal_v4")

    clear_settings_cache()
    contaminated = get_settings()
    assert 27018 in extract_mongo_ports(contaminated.mongodb_uri)
    assert contaminated.mongodb_db == "eternal_v4"

    pin_sj_oneshot_test_mongo_env(locked_uri=_TEST_URI)
    clear_settings_cache()

    settings = get_settings()
    ports = extract_mongo_ports(settings.mongodb_uri)
    assert 27019 in ports
    assert 27018 not in ports
    assert 27017 not in ports
    assert settings.mongodb_db == "eternal_v4_test_sj_activation"

    report = assert_effective_sj_oneshot_mongo_target()
    assert report["ok"] is True
    assert report["port"] == 27019
    assert report["db"] == "eternal_v4_test_sj_activation"
    assert report["user"] == "eternal_v4_test_sync"
    assert report["auth_source"] == "eternal_v4_test_sj_activation"
    assert report["replica_set"] == "eternalV4TestRS"


def test_without_pin_production_uri_is_rejected_by_effective_assert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tanpa pin, settings dari backend.env 27018 harus STOP sebelum tulis."""
    _accurate_live_env(monkeypatch)
    monkeypatch.setenv("MONGO_URL", _PROD_URI)
    monkeypatch.setenv("DB_NAME", "eternal_v4_test_sj_activation")  # nama test palsu
    clear_settings_cache()

    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_effective_sj_oneshot_mongo_target()
    assert exc.value.code == "oneshot_effective_forbidden_port"


def test_pin_rejects_locked_uri_on_27018() -> None:
    with pytest.raises(UnsafeMongoTargetError) as exc:
        pin_sj_oneshot_test_mongo_env(
            locked_uri=(
                "mongodb://u:p@127.0.0.1:27018/"
                "eternal_v4_test_sj_activation?authSource=eternal_v4_test_sj_activation"
            ),
            environ={},
        )
    assert exc.value.code == "oneshot_locked_uri_forbidden_port"


def test_effective_assert_rejects_wrong_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accurate_live_env(monkeypatch)
    bad = (
        "mongodb://wrong_user:x@127.0.0.1:27019/"
        "eternal_v4_test_sj_activation"
        "?authSource=eternal_v4_test_sj_activation"
        "&replicaSet=eternalV4TestRS"
    )
    pin_sj_oneshot_test_mongo_env(locked_uri=bad)
    clear_settings_cache()
    with pytest.raises(UnsafeMongoTargetError) as exc:
        assert_effective_sj_oneshot_mongo_target()
    assert exc.value.code == "oneshot_effective_wrong_user"
