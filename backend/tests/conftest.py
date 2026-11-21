"""Test fixtures — isolated MongoDB only (never production)."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.database import Database

# Configure env BEFORE importing app modules that cache settings.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "MONGODB_URI",
    # Port 27019 = scripts/start-test-mongo.sh (27018 = V4 production, diblokir db_guard).
    "mongodb://eternal:eternal_dev_password@127.0.0.1:27019/"
    "?authSource=admin&replicaSet=rs0&directConnection=true",
)
os.environ.setdefault("MONGODB_DB", "eternal_produksi_v4_test")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("SESSION_TTL_SECONDS", "3600")
os.environ.setdefault("LOGIN_RATE_LIMIT_ATTEMPTS", "5")
os.environ.setdefault("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")
os.environ.setdefault("CORS_ORIGINS", "")


def _wait_for_mongo(uri: str, timeout_seconds: float = 90.0) -> MongoClient:
    """Tunggu ping + PRIMARY replica set (hindari race saat RS election)."""
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            client.admin.command("ping")
            hello = client.admin.command("hello")
            set_name = hello.get("setName")
            is_primary = bool(
                hello.get("isWritablePrimary") or hello.get("ismaster")
            )
            # URI CI memakai replicaSet=rs0 — jangan lanjut sebelum PRIMARY.
            if set_name == "rs0" and is_primary:
                return client
            last_error = RuntimeError(
                f"Mongo belum PRIMARY rs0 (setName={set_name!r}, primary={is_primary})"
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"MongoDB test instance not reachable: {last_error}")


@pytest.fixture(scope="session")
def mongo_client() -> Generator[MongoClient, None, None]:
    uri = os.environ["MONGODB_URI"]
    try:
        client = _wait_for_mongo(uri)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MongoDB test instance unavailable: {exc}")
    yield client
    client.close()


@pytest.fixture()
def db_name() -> str:
    return f"eternal_v4_test_{uuid.uuid4().hex[:10]}"


@pytest.fixture()
def db(
    mongo_client: MongoClient,
    db_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Database, None, None]:
    # AliasChoices mengutamakan DB_NAME — samakan agar tidak nyasar ke env shell.
    monkeypatch.setenv("MONGODB_DB", db_name)
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")

    from app.config import clear_settings_cache
    from app.db import close_client, ensure_indexes
    from app.repositories import master_repository as master

    from app.repositories import settings_role_repository as settings_roles

    clear_settings_cache()
    close_client()
    # Health singkat sebelum createIndex — gagal lingkungan ≠ assertion failure.
    try:
        mongo_client.admin.command("ping")
        hello = mongo_client.admin.command("hello")
        if hello.get("setName") != "rs0" or not (
            hello.get("isWritablePrimary") or hello.get("ismaster")
        ):
            pytest.skip(
                f"Mongo test RS tidak PRIMARY (setName={hello.get('setName')!r})"
            )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Mongo test instance unavailable mid-suite: {exc}")
    database = mongo_client[db_name]
    try:
        ensure_indexes(database)
    except Exception as exc:  # noqa: BLE001
        # AutoReconnect / WT panic mid-suite = lingkungan, bukan assertion.
        pytest.skip(f"Mongo ensure_indexes gagal (lingkungan): {exc}")
    # Bootstrap Master + system roles eksplisit di fixture — request path tidak seed/upsert.
    master.seed_master_defaults(database)
    settings_roles.ensure_system_roles(database)
    yield database
    close_client()
    clear_settings_cache()
    try:
        mongo_client.drop_database(db_name)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _auto_ensure_suite_operators_on_create(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """Regresi suite: email alokasi default di-seed jika belum ada di Master.

    Tidak membuat ghost; tidak menimpa helper/nonaktif yang sudah tersimpan.
    """
    from app.services import scheduling_service
    from tests.scheduling_operator_fixtures import ensure_operators_for_allocations

    original = scheduling_service.create_allocations

    def _wrapped(db_arg: Database, actor: dict, **kwargs):  # type: ignore[no-untyped-def]
        ensure_operators_for_allocations(db_arg, list(kwargs.get("allocations") or []))
        return original(db_arg, actor, **kwargs)

    monkeypatch.setattr(scheduling_service, "create_allocations", _wrapped)
    yield


@pytest.fixture()
def app_client(db: Database, monkeypatch: pytest.MonkeyPatch):
    from app.api.tracking import reset_tracking_rate_limiter
    from app.auth.routes import reset_rate_limiter
    from app.config import clear_settings_cache
    from app.db import close_client
    from app.main import create_app

    # db fixture already set MONGODB_DB / cleared caches.
    clear_settings_cache()
    close_client()
    reset_rate_limiter()
    reset_tracking_rate_limiter()

    application = create_app()
    with TestClient(application) as client:
        yield client

    reset_rate_limiter()
    reset_tracking_rate_limiter()
    close_client()
    clear_settings_cache()
