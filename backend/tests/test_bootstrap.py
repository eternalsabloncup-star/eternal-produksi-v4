"""Bootstrap index + admin provisioning tests."""

from __future__ import annotations

import pytest
from pymongo.database import Database

from app.bootstrap import admin_exists, create_admin_user, init_indexes


def test_init_indexes_idempotent(db: Database) -> None:
    first = init_indexes(db)
    second = init_indexes(db)
    assert "uniq_email_normalized" in first["users"]
    assert "ttl_sessions_expires_at" in first["sessions"]
    assert first["users"] == second["users"]


def test_create_admin_secure_and_idempotent(db: Database) -> None:
    assert admin_exists(db) is False
    user = create_admin_user(
        db,
        email="Admin@Example.com",
        display_name="Admin Eternal",
        password="password-kuat-123",
    )
    assert user["email_normalized"] == "admin@example.com"
    assert user["role"] == "super_admin"
    assert admin_exists(db) is True

    with pytest.raises(RuntimeError, match="sudah ada"):
        create_admin_user(
            db,
            email="other@example.com",
            display_name="Other",
            password="password-kuat-456",
        )

    with pytest.raises(RuntimeError, match="sudah ada"):
        create_admin_user(
            db,
            email="admin@example.com",
            display_name="Dup",
            password="password-kuat-789",
            allow_if_admin_exists=True,
        )


def test_unique_email_index(db: Database) -> None:
    init_indexes(db)
    create_admin_user(
        db,
        email="unique@example.com",
        display_name="One",
        password="password-kuat-123",
    )
    # Bypass helper to assert DB unique index.
    from datetime import datetime, timezone

    from pymongo.errors import DuplicateKeyError

    with pytest.raises(DuplicateKeyError):
        db["users"].insert_one(
            {
                "email_normalized": "unique@example.com",
                "display_name": "Two",
                "password_hash": "x",
                "role": "user",
                "is_active": True,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
