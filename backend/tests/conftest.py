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


def _wait_for_mongo(uri: str, timeout_seconds: float = 60.0) -> MongoClient:
    client = MongoClient(uri, serverSelectionTimeoutMS=2000)
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            client.admin.command("ping")
            return client
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
    monkeypatch.setenv("MONGODB_DB", db_name)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")

    from app.config import clear_settings_cache
    from app.db import close_client, ensure_indexes

    clear_settings_cache()
    close_client()
    database = mongo_client[db_name]
    ensure_indexes(database)
    yield database
    close_client()
    clear_settings_cache()
    mongo_client.drop_database(db_name)


@pytest.fixture()
def app_client(db: Database, monkeypatch: pytest.MonkeyPatch):
    from app.auth.routes import reset_rate_limiter
    from app.config import clear_settings_cache
    from app.db import close_client
    from app.main import create_app

    # db fixture already set MONGODB_DB / cleared caches.
    clear_settings_cache()
    close_client()
    reset_rate_limiter()

    application = create_app()
    with TestClient(application) as client:
        yield client

    reset_rate_limiter()
    close_client()
    clear_settings_cache()
