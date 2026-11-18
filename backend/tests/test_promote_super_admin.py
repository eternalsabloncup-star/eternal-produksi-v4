"""Promotion helper — dry-run / execute against isolated test DB only."""

from __future__ import annotations

import pytest

from app.auth.passwords import hash_password
from app.auth.roles import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.bootstrap import create_admin_user
from app.services.promote_super_admin import (
    CANONICAL_SUPER_ADMIN_DISPLAY_NAME,
    CANONICAL_SUPER_ADMIN_EMAIL,
    PromoteError,
    assert_promote_target_env,
    promote_to_super_admin,
)


def test_env_guard_rejects_wrong_port_and_db() -> None:
    with pytest.raises(PromoteError):
        assert_promote_target_env(
            mongodb_uri="mongodb://u:p@127.0.0.1:27017/eternal_v4",
            mongodb_db="eternal_v4",
        )
    with pytest.raises(PromoteError):
        assert_promote_target_env(
            mongodb_uri="mongodb://u:p@127.0.0.1:27018/eternal_v4",
            mongodb_db="eternal_produksi_v4",
        )
    assert_promote_target_env(
        mongodb_uri="mongodb://u:p@127.0.0.1:27018/eternal_v4?authSource=eternal_v4",
        mongodb_db="eternal_v4",
    )


def test_dry_run_does_not_write(db) -> None:
    create_admin_user(
        db,
        email=CANONICAL_SUPER_ADMIN_EMAIL,
        display_name="Temp",
        password="password-kuat-123",
        role=ROLE_ADMIN,
        allow_if_admin_exists=True,
    )
    before = db["users"].find_one(
        {"email_normalized": CANONICAL_SUPER_ADMIN_EMAIL}
    )
    hash_before = before["password_hash"]
    plan = promote_to_super_admin(db, dry_run=True)
    assert plan["executed"] is False
    assert plan["dry_run"] is True
    after = db["users"].find_one(
        {"email_normalized": CANONICAL_SUPER_ADMIN_EMAIL}
    )
    assert after["role"] == ROLE_ADMIN
    assert after["password_hash"] == hash_before
    assert after["display_name"] == "Temp"


def test_execute_promotes_without_changing_password(db) -> None:
    create_admin_user(
        db,
        email=CANONICAL_SUPER_ADMIN_EMAIL,
        display_name="Temp",
        password="password-kuat-123",
        role=ROLE_ADMIN,
        allow_if_admin_exists=True,
    )
    before = db["users"].find_one(
        {"email_normalized": CANONICAL_SUPER_ADMIN_EMAIL}
    )
    hash_before = before["password_hash"]

    result = promote_to_super_admin(db, dry_run=False)
    assert result["executed"] is True
    stored = db["users"].find_one(
        {"email_normalized": CANONICAL_SUPER_ADMIN_EMAIL}
    )
    assert stored["role"] == ROLE_SUPER_ADMIN
    assert stored["display_name"] == CANONICAL_SUPER_ADMIN_DISPLAY_NAME
    assert stored["is_active"] is True
    assert stored["password_hash"] == hash_before

    # Idempotent second run
    again = promote_to_super_admin(db, dry_run=False)
    assert again["executed"] is True
    assert again["already_compliant"] is True
    stored2 = db["users"].find_one(
        {"email_normalized": CANONICAL_SUPER_ADMIN_EMAIL}
    )
    assert stored2["password_hash"] == hash_before
    assert db["users"].count_documents(
        {"email_normalized": CANONICAL_SUPER_ADMIN_EMAIL}
    ) == 1

    audits = list(
        db["audit_logs"].find({"action": "user.promote_super_admin"})
    )
    assert len(audits) >= 1
    assert all("password_hash" not in a.get("detail", {}) for a in audits)


def test_missing_user_does_not_create(db) -> None:
    with pytest.raises(PromoteError, match="tidak ditemukan"):
        promote_to_super_admin(
            db,
            email="tidak-ada@eternal.local",
            dry_run=False,
        )
    assert db["users"].count_documents({}) == 0


def test_password_hash_not_touched_even_if_display_changes(db) -> None:
    # Seed with known hash shape
    create_admin_user(
        db,
        email=CANONICAL_SUPER_ADMIN_EMAIL,
        display_name="X",
        password="password-kuat-123",
        role=ROLE_ADMIN,
        allow_if_admin_exists=True,
    )
    doc = db["users"].find_one(
        {"email_normalized": CANONICAL_SUPER_ADMIN_EMAIL}
    )
    # Sanity: hash_password produces argon2
    assert doc["password_hash"].startswith("$argon2")
    assert hash_password("password-kuat-123") != doc["password_hash"]  # unique salt
    promote_to_super_admin(db, dry_run=False)
    doc2 = db["users"].find_one(
        {"email_normalized": CANONICAL_SUPER_ADMIN_EMAIL}
    )
    assert doc2["password_hash"] == doc["password_hash"]
