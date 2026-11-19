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
    "mongodb://eternal:eternal_dev_password@127.0.0.1:27017/"
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

    clear_settings_cache()
    close_client()
    database = mongo_client[db_name]
    ensure_indexes(database)
    # Bootstrap Master eksplisit di fixture — request path tidak seed/upsert.
    master.seed_master_defaults(database)
    yield database
    close_client()
    clear_settings_cache()
    mongo_client.drop_database(db_name)


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
