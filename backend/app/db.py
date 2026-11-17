"""MongoDB access via synchronous PyMongo."""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import Settings, get_settings

_client: MongoClient | None = None


def get_client(settings: Settings | None = None) -> MongoClient:
    global _client
    if _client is None:
        cfg = settings or get_settings()
        _client = MongoClient(
            cfg.mongodb_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        )
    return _client


def get_db(settings: Settings | None = None) -> Database:
    cfg = settings or get_settings()
    return get_client(cfg)[cfg.mongodb_db]


def users_collection(db: Database | None = None) -> Collection:
    database = get_db() if db is None else db
    return database["users"]


def sessions_collection(db: Database | None = None) -> Collection:
    database = get_db() if db is None else db
    return database["sessions"]


USERS_INDEXES: list[dict[str, Any]] = [
    {
        "keys": [("email_normalized", ASCENDING)],
        "name": "uniq_email_normalized",
        "unique": True,
    },
]

SESSIONS_INDEXES: list[dict[str, Any]] = [
    {
        "keys": [("token_hash", ASCENDING)],
        "name": "uniq_token_hash",
        "unique": True,
    },
    {
        "keys": [("user_id", ASCENDING)],
        "name": "idx_sessions_user_id",
    },
    {
        "keys": [("expires_at", ASCENDING)],
        "name": "ttl_sessions_expires_at",
        "expireAfterSeconds": 0,
    },
]


def ensure_indexes(db: Database | None = None) -> dict[str, list[str]]:
    """Create required indexes idempotently. Safe to re-run."""
    database = get_db() if db is None else db
    created: dict[str, list[str]] = {"users": [], "sessions": []}

    users = database["users"]
    for spec in USERS_INDEXES:
        name = users.create_index(
            spec["keys"],
            name=spec["name"],
            unique=spec.get("unique", False),
        )
        created["users"].append(name)

    sessions = database["sessions"]
    for spec in SESSIONS_INDEXES:
        kwargs: dict[str, Any] = {"name": spec["name"], "unique": spec.get("unique", False)}
        if "expireAfterSeconds" in spec:
            kwargs["expireAfterSeconds"] = spec["expireAfterSeconds"]
        name = sessions.create_index(spec["keys"], **kwargs)
        created["sessions"].append(name)

    return created


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def ping(db: Database | None = None) -> bool:
    database = get_db() if db is None else db
    database.client.admin.command("ping")
    return True
