#!/usr/bin/env python3
"""API smoke — kandidat Operator dari DB test terisolasi (bukan production)."""

from __future__ import annotations

import os
import sys
import uuid

# Pastikan env test sebelum import app.
os.environ["APP_ENV"] = "test"
os.environ["COOKIE_SECURE"] = "false"
os.environ.setdefault(
    "MONGODB_URI",
    "mongodb://eternal:eternal_dev_password@127.0.0.1:27019/"
    "?authSource=admin&replicaSet=rs0&directConnection=true",
)
DB_NAME = f"eternal_v4_test_smoke_op_{uuid.uuid4().hex[:8]}"
os.environ["MONGODB_DB"] = DB_NAME
os.environ["DB_NAME"] = DB_NAME

from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.auth.passwords import hash_password
from app.config import clear_settings_cache
from app.db import close_client, ensure_indexes, get_db
from app.main import create_app
from app.models.user import normalize_email
from app.repositories import master_repository as master
from app.repositories import settings_role_repository as settings_roles

PASSWORD = "password-kuat-123"


def main() -> int:
    clear_settings_cache()
    close_client()
    client_mongo = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=5000)
    client_mongo.admin.command("ping")
    db = client_mongo[DB_NAME]
    ensure_indexes(db)
    master.seed_master_defaults(db)
    settings_roles.ensure_system_roles(db)

    db["users"].insert_many(
        [
            {
                "email_normalized": normalize_email("kp@eternal.local"),
                "display_name": "Kepala Produksi",
                "password_hash": hash_password(PASSWORD),
                "role": "user",
                "operational_role": "production-head",
                "is_active": True,
                "effective_module_permissions": ["access.all_branches"],
            },
            {
                "email_normalized": normalize_email("op.smoke@eternal.local"),
                "display_name": "Smoke Operator",
                "password_hash": hash_password(PASSWORD),
                "role": "user",
                "operational_role": "operator",
                "is_active": True,
                "effective_module_permissions": ["access.all_branches"],
            },
            {
                "email_normalized": normalize_email("helper.smoke@eternal.local"),
                "display_name": "Smoke Helper",
                "password_hash": hash_password(PASSWORD),
                "role": "user",
                "operational_role": "helper",
                "is_active": True,
                "effective_module_permissions": ["access.all_branches"],
            },
        ]
    )

    app = create_app()
    http = TestClient(app)
    login = http.post(
        "/api/auth/login",
        json={"email": "kp@eternal.local", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    res = http.get("/api/scheduling/operators")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    emails = {i["email"] for i in items}
    assert "op.smoke@eternal.local" in emails, items
    assert "helper.smoke@eternal.local" not in emails, items
    named = next(i for i in items if i["email"] == "op.smoke@eternal.local")
    assert named["display_name"] == "Smoke Operator"
    assert named["id"]

    print("SMOKE_OK")
    print(f"db={DB_NAME}")
    print(f"operators={items}")
    client_mongo.drop_database(DB_NAME)
    close_client()
    clear_settings_cache()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE_FAIL: {exc}", file=sys.stderr)
        raise
