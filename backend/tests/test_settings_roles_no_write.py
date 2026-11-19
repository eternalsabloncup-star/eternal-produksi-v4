"""Settings roles: GET/catalog/permission checks must not seed or write."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.auth.permissions import PERM_MANAGE_ROLES, has_permission, require_permission
from app.auth.roles import ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_USER
from app.bootstrap import create_admin_user
from app.models.user import normalize_email
from app.repositories import settings_role_repository as roles

PASSWORD = "password-kuat-123"


def _seed_owner(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _login(client: TestClient, email: str = "owner@eternal.local") -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _roles_snapshot(db: Database) -> list[dict]:
    return list(
        db["settings_roles"].find({}, {"_id": 0, "role_key": 1, "version": 1, "updated_at": 1})
    )


def test_get_roles_does_not_write_when_system_roles_missing(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    db["settings_roles"].delete_many({})
    before = _roles_snapshot(db)
    assert before == []

    csrf = _login(app_client)
    res = app_client.get("/api/settings/roles", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["items"] == []
    assert body["system_roles_bootstrap"]["ready"] is False
    assert set(body["system_roles_bootstrap"]["missing"]) == {
        ROLE_SUPER_ADMIN,
        ROLE_ADMIN,
        ROLE_USER,
    }
    assert "bootstrap_mongo.py --seed-system-roles" in body["system_roles_bootstrap"]["note"]

    after = _roles_snapshot(db)
    assert after == []
    assert db["settings_roles"].count_documents({}) == 0


def test_get_catalog_does_not_write_when_system_roles_missing(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    db["settings_roles"].delete_many({})
    assert db["settings_roles"].count_documents({}) == 0

    _login(app_client)
    res = app_client.get("/api/settings/catalog")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "module_permissions" in body
    assert body["system_roles_bootstrap"]["ready"] is False
    assert ROLE_SUPER_ADMIN in body["system_roles_bootstrap"]["missing"]
    assert db["settings_roles"].count_documents({}) == 0


def test_permission_checks_do_not_write_settings_roles(db: Database) -> None:
    db["settings_roles"].delete_many({})
    assert db["settings_roles"].count_documents({}) == 0

    user = {
        "role": ROLE_ADMIN,
        "effective_module_permissions": ["reports.view", "operations.full"],
    }
    assert has_permission(user, "reports.view") is True
    assert has_permission(user, PERM_MANAGE_ROLES) is False
    assert has_permission({"role": ROLE_SUPER_ADMIN}, PERM_MANAGE_ROLES) is True
    assert has_permission(ROLE_USER, "operations.full") is False

    # Dependency factory must not touch the catalog either.
    dep = require_permission("reports.view")
    assert callable(dep)

    assert db["settings_roles"].count_documents({}) == 0


def test_login_does_not_seed_system_roles(app_client: TestClient, db: Database) -> None:
    db["settings_roles"].delete_many({})
    db["users"].insert_one(
        {
            "email_normalized": normalize_email("plain@eternal.local"),
            "display_name": "Plain",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "is_active": True,
            "effective_module_permissions": [],
        }
    )
    assert db["settings_roles"].count_documents({}) == 0
    res = app_client.post(
        "/api/auth/login",
        json={"email": "plain@eternal.local", "password": PASSWORD},
    )
    assert res.status_code == 200, res.text
    assert db["settings_roles"].count_documents({}) == 0


def test_ensure_system_roles_is_explicit_and_idempotent(db: Database) -> None:
    db["settings_roles"].delete_many({})
    first = roles.ensure_system_roles(db)
    second = roles.ensure_system_roles(db)
    assert first == 3
    assert second == 0
    status = roles.system_roles_bootstrap_status(db)
    assert status["ready"] is True
    assert status["missing"] == []
    assert set(status["present"]) == {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER}


def test_get_role_and_list_roles_are_read_only(db: Database) -> None:
    db["settings_roles"].delete_many({})
    assert roles.get_role(db, ROLE_ADMIN) is None
    assert roles.list_roles(db) == []
    assert db["settings_roles"].count_documents({}) == 0
