"""Roles, permissions, and Super Admin guards."""

from __future__ import annotations

from app.auth.permissions import (
    ALL_PERMISSIONS,
    PERM_MANAGE_ROLES,
    PERM_MANAGE_USERS,
    branch_filter_applies,
    has_permission,
    permissions_for_role,
)
from app.auth.roles import ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_USER, is_super_admin
from app.auth.user_guards import (
    UserGuardError,
    assert_actor_can_change_role,
    assert_actor_can_deactivate,
    assert_can_demote_super_admin,
    count_active_super_admins,
)
from app.bootstrap import admin_exists, create_admin_user


def test_super_admin_has_all_permissions() -> None:
    assert is_super_admin(ROLE_SUPER_ADMIN)
    assert permissions_for_role(ROLE_SUPER_ADMIN) == ALL_PERMISSIONS
    for perm in ALL_PERMISSIONS:
        assert has_permission({"role": ROLE_SUPER_ADMIN}, perm)
    assert branch_filter_applies({"role": ROLE_SUPER_ADMIN}) is False


def test_admin_and_user_are_limited() -> None:
    assert has_permission({"role": ROLE_ADMIN}, PERM_MANAGE_USERS) is False
    assert has_permission({"role": ROLE_ADMIN}, PERM_MANAGE_ROLES) is False
    assert has_permission({"role": ROLE_USER}, PERM_MANAGE_USERS) is False
    assert branch_filter_applies({"role": ROLE_ADMIN}) is True


def test_ordinary_role_cannot_promote_to_super_admin() -> None:
    actor = {"_id": "a", "role": ROLE_ADMIN}
    target = {"_id": "b", "role": ROLE_USER}
    try:
        assert_actor_can_change_role(actor, target, ROLE_SUPER_ADMIN)
        raise AssertionError("expected UserGuardError")
    except UserGuardError:
        pass


def test_ordinary_role_cannot_alter_super_admin() -> None:
    actor = {"_id": "a", "role": ROLE_ADMIN}
    target = {"_id": "b", "role": ROLE_SUPER_ADMIN, "is_active": True}
    try:
        assert_actor_can_change_role(actor, target, ROLE_ADMIN)
        raise AssertionError("expected UserGuardError")
    except UserGuardError:
        pass


def test_bootstrap_creates_super_admin(db) -> None:
    assert admin_exists(db) is False
    user = create_admin_user(
        db,
        email="first@eternal.local",
        display_name="First",
        password="password-kuat-123",
    )
    assert user["role"] == ROLE_SUPER_ADMIN
    assert admin_exists(db) is True
    stored = db["users"].find_one({"email_normalized": "first@eternal.local"})
    assert stored["role"] == ROLE_SUPER_ADMIN
    assert stored["is_active"] is True
    audits = list(db["audit_logs"].find({"action": "user.bootstrap_create"}))
    assert len(audits) == 1
    assert "password" not in audits[0].get("detail", {})
    assert "password_hash" not in audits[0].get("detail", {})


def test_last_super_admin_cannot_be_demoted(db) -> None:
    create_admin_user(
        db,
        email="solo@eternal.local",
        display_name="Solo",
        password="password-kuat-123",
    )
    target = db["users"].find_one({"email_normalized": "solo@eternal.local"})
    assert count_active_super_admins(db) == 1
    try:
        assert_can_demote_super_admin(db, target, ROLE_ADMIN)
        raise AssertionError("expected UserGuardError")
    except UserGuardError:
        pass
    try:
        assert_actor_can_deactivate(
            {"role": ROLE_SUPER_ADMIN},
            target,
            db=db,
        )
        raise AssertionError("expected UserGuardError")
    except UserGuardError:
        pass


def test_login_me_returns_super_admin_role(app_client, db) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password="password-kuat-123",
    )
    login = app_client.post(
        "/api/auth/login",
        json={"email": "owner@eternal.local", "password": "password-kuat-123"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"] == ROLE_SUPER_ADMIN
    assert "password_hash" not in login.json()["user"]
    me = app_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["role"] == ROLE_SUPER_ADMIN
