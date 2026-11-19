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


def table_preferences_collection(db: Database | None = None) -> Collection:
    database = get_db() if db is None else db
    return database["ui_table_preferences"]


def accurate_sales_orders_collection(db: Database | None = None) -> Collection:
    database = get_db() if db is None else db
    return database["accurate_sales_orders"]


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

TABLE_PREFERENCES_INDEXES: list[dict[str, Any]] = [
    {
        "keys": [("user_id", ASCENDING), ("table_id", ASCENDING)],
        "name": "uniq_user_table_prefs",
        "unique": True,
    },
]


def ensure_indexes(db: Database | None = None) -> dict[str, list[str]]:
    """Create required indexes idempotently. Safe to re-run."""
    from app.repositories.delivery_repository import ensure_delivery_indexes
    from app.repositories.design_repository import ensure_design_indexes
    from app.repositories.evidence_repository import ensure_evidence_indexes
    from app.repositories.gate_repository import ensure_gate_indexes
    from app.repositories.master_repository import ensure_master_indexes
    from app.repositories.product_master_repository import ensure_product_master_indexes
    from app.repositories.payment_repository import ensure_payment_indexes
    from app.repositories.po_repository import ensure_po_indexes
    from app.repositories.sample_repository import ensure_sample_indexes
    from app.repositories.schedule_repository import ensure_schedule_indexes
    from app.repositories.task_repository import ensure_task_indexes
    from app.repositories.tracking_link_repository import ensure_tracking_indexes
    from app.services.audit_log import ensure_audit_indexes

    database = get_db() if db is None else db
    created: dict[str, list[str]] = {
        "users": [],
        "sessions": [],
        "ui_table_preferences": [],
        "accurate_sales_orders": [],
        "audit_logs": [],
        "master": [],
        "operational_tasks": [],
        "tracking_links": [],
    }

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

    prefs = database["ui_table_preferences"]
    for spec in TABLE_PREFERENCES_INDEXES:
        name = prefs.create_index(
            spec["keys"],
            name=spec["name"],
            unique=spec.get("unique", False),
        )
        created["ui_table_preferences"].append(name)

    created["accurate_sales_orders"] = ensure_po_indexes(database)
    created["audit_logs"] = ensure_audit_indexes(database)
    created["master"] = ensure_master_indexes(database)
    created["master_products"] = ensure_product_master_indexes(database)
    created["operational_tasks"] = ensure_task_indexes(database)
    created["po_gates"] = ensure_gate_indexes(database)
    created["design"] = ensure_design_indexes(database)
    created["payment_receipts"] = ensure_payment_indexes(database)
    created["evidence_files"] = ensure_evidence_indexes(database)
    created["production_allocations"] = ensure_schedule_indexes(database)
    created["operator_samples"] = ensure_sample_indexes(database)
    created["accurate_delivery_notes"] = ensure_delivery_indexes(database)
    # Maintenance/bootstrap only — request path tracking tetap verify-only.
    created["tracking_links"] = ensure_tracking_indexes(database)

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
