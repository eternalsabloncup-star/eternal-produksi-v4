"""Kunci target Mongo untuk one-shot SJ sync (VPS test 27019 saja).

Setelah ``source backend.env`` (sering berisi MONGO_URL production 27018),
wajib memanggil ``pin_sj_oneshot_test_mongo_env`` lalu
``assert_effective_sj_oneshot_mongo_target`` sebelum operasi tulis apa pun.
Tidak mencetak URI/password/token.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from app.db_guard import (
    UnsafeMongoTargetError,
    assert_safe_mongo_sync_target,
    extract_mongo_auth_source,
    extract_mongo_hosts,
    extract_mongo_ports,
    extract_mongo_replica_set,
    extract_mongo_username,
)

# Kontrak VPS Owner (test SJ activation) — hard-coded defaults; override via args.
DEFAULT_TEST_HOST = "127.0.0.1"
DEFAULT_TEST_PORT = 27019
DEFAULT_TEST_DB = "eternal_v4_test_sj_activation"
DEFAULT_TEST_USER = "eternal_v4_test_sync"
DEFAULT_TEST_AUTH_SOURCE = "eternal_v4_test_sj_activation"
DEFAULT_TEST_RS = "eternalV4TestRS"

_MONGO_ENV_KEYS = (
    "MONGO_URL",
    "MONGODB_URI",
    "MONGODB_URL",
    "DB_NAME",
    "MONGODB_DB",
    "V4_MONGO_AUTH_SOURCE",
    "MONGO_AUTH_SOURCE",
)


def pin_sj_oneshot_test_mongo_env(
    *,
    locked_uri: str,
    db_name: str = DEFAULT_TEST_DB,
    auth_source: str = DEFAULT_TEST_AUTH_SOURCE,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Timpa seluruh alias Mongo ke URI/DB test terkunci.

    Dipakai *setelah* memuat backend.env agar MONGO_URL production (27018)
    tidak menjadi target efektif.
    """
    env = environ if environ is not None else os.environ
    uri = (locked_uri or "").strip()
    if not uri:
        raise UnsafeMongoTargetError(
            "locked_uri kosong — tidak bisa pin target test.",
            code="oneshot_locked_uri_missing",
        )
    ports = extract_mongo_ports(uri)
    if 27017 in ports or 27018 in ports:
        raise UnsafeMongoTargetError(
            "locked_uri mengandung port terlarang 27017/27018.",
            code="oneshot_locked_uri_forbidden_port",
        )
    if DEFAULT_TEST_PORT not in ports:
        raise UnsafeMongoTargetError(
            f"locked_uri wajib port {DEFAULT_TEST_PORT}.",
            code="oneshot_locked_uri_wrong_port",
        )

    db = (db_name or "").strip()
    auth = (auth_source or "").strip()
    if not db.startswith("eternal_v4_test_"):
        raise UnsafeMongoTargetError(
            "db_name pin wajib eternal_v4_test_*.",
            code="oneshot_locked_db_invalid",
        )

    # Hapus alias yang bisa menipu pydantic AliasChoices / kode lama.
    for key in _MONGO_ENV_KEYS:
        env.pop(key, None)

    env["MONGO_URL"] = uri
    env["MONGODB_URI"] = uri
    env["DB_NAME"] = db
    env["MONGODB_DB"] = db
    env["V4_MONGO_AUTH_SOURCE"] = auth
    env["MONGO_AUTH_SOURCE"] = auth
    return {
        "MONGO_URL": uri,
        "MONGODB_URI": uri,
        "DB_NAME": db,
        "MONGODB_DB": db,
        "V4_MONGO_AUTH_SOURCE": auth,
        "MONGO_AUTH_SOURCE": auth,
    }


def assert_effective_sj_oneshot_mongo_target(
    *,
    expected_host: str = DEFAULT_TEST_HOST,
    expected_port: int = DEFAULT_TEST_PORT,
    expected_db: str = DEFAULT_TEST_DB,
    expected_user: str = DEFAULT_TEST_USER,
    expected_auth_source: str = DEFAULT_TEST_AUTH_SOURCE,
    expected_replica_set: str = DEFAULT_TEST_RS,
    app_env: str = "development",
) -> dict[str, Any]:
    """Validasi ``get_settings()`` + URI efektif sebelum tulis.

    Fail-closed jika host/port/db/user/authSource/replicaSet tidak cocok.
    Tidak membuka koneksi jaringan (koneksi live diverifikasi di script).
    """
    from app.config import get_settings

    settings = get_settings()
    uri = settings.mongodb_uri
    db_name = settings.mongodb_db

    ports = extract_mongo_ports(uri)
    hosts = extract_mongo_hosts(uri)
    user = extract_mongo_username(uri)
    auth = extract_mongo_auth_source(uri)
    rs = extract_mongo_replica_set(uri)

    if 27017 in ports or 27018 in ports:
        raise UnsafeMongoTargetError(
            "Settings efektif mengarah ke port production/legacy.",
            code="oneshot_effective_forbidden_port",
        )
    if expected_port not in ports or any(p != expected_port for p in ports):
        raise UnsafeMongoTargetError(
            f"Port efektif bukan {expected_port}.",
            code="oneshot_effective_wrong_port",
        )
    if not hosts or any(h != expected_host for h in hosts):
        raise UnsafeMongoTargetError(
            f"Host efektif bukan {expected_host}.",
            code="oneshot_effective_wrong_host",
        )
    if db_name != expected_db:
        raise UnsafeMongoTargetError(
            "DB_NAME efektif bukan database test SJ activation.",
            code="oneshot_effective_wrong_db",
        )
    if user != expected_user:
        raise UnsafeMongoTargetError(
            "Username Mongo efektif bukan user test sync.",
            code="oneshot_effective_wrong_user",
        )
    if auth != expected_auth_source:
        raise UnsafeMongoTargetError(
            "authSource efektif tidak cocok kontrak test.",
            code="oneshot_effective_wrong_auth_source",
        )
    if rs != expected_replica_set:
        raise UnsafeMongoTargetError(
            "replicaSet URI efektif tidak cocok eternalV4TestRS.",
            code="oneshot_effective_wrong_replica_set",
        )

    # Guard generik (nama DB + port) — APP_ENV development agar authSource ketat.
    assert_safe_mongo_sync_target(
        uri=uri,
        db_name=db_name,
        app_env=app_env,
        expected_auth_source=expected_auth_source,
    )

    # Path Accurate: jangan log token; hanya pastikan bukan placeholder.
    host = urlparse(
        settings.accurate_base_url
        if "://" in (settings.accurate_base_url or "")
        else f"https://{settings.accurate_base_url or ''}"
    ).hostname or ""
    if not host or host.endswith(".test"):
        raise UnsafeMongoTargetError(
            "Accurate host tidak live / placeholder.",
            code="oneshot_accurate_not_live",
        )

    return {
        "ok": True,
        "host": expected_host,
        "port": expected_port,
        "db": expected_db,
        "user": expected_user,
        "auth_source": expected_auth_source,
        "replica_set": expected_replica_set,
        "sj_sync_enabled": bool(settings.sj_sync_enabled),
        "accurate_host_ok": True,
        "writes_accurate": False,
    }
