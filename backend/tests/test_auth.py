"""Authentication, session, CSRF, and rate-limit tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.auth.session import hash_token
from app.bootstrap import create_admin_user


def _seed_admin(db, *, active: bool = True, email: str = "admin@eternal.local"):
    user = create_admin_user(
        db,
        email=email,
        display_name="Admin Eternal",
        password="password-kuat-123",
        allow_if_admin_exists=True,
    )
    if not active:
        db["users"].update_one(
            {"email_normalized": email.lower()},
            {"$set": {"is_active": False}},
        )
    return user


def test_login_valid(app_client, db) -> None:
    _seed_admin(db)
    response = app_client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "password-kuat-123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "admin@eternal.local"
    assert "password" not in body
    assert "password_hash" not in body
    assert "csrf_token" in body
    assert "eternal_session" in response.cookies
    # Session token must be stored hashed.
    raw = response.cookies["eternal_session"]
    stored = db["sessions"].find_one({"token_hash": hash_token(raw)})
    assert stored is not None
    assert stored.get("token") is None


def test_login_invalid(app_client, db) -> None:
    _seed_admin(db)
    response = app_client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "salah-total"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Email atau kata sandi salah"


def test_login_inactive_user(app_client, db) -> None:
    _seed_admin(db, active=False)
    response = app_client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "password-kuat-123"},
    )
    assert response.status_code == 401


def test_session_persistence_me(app_client, db) -> None:
    _seed_admin(db)
    login = app_client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "password-kuat-123"},
    )
    assert login.status_code == 200
    me = app_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["display_name"] == "Admin Eternal"


def test_session_expiry_enforced(app_client, db) -> None:
    _seed_admin(db)
    login = app_client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "password-kuat-123"},
    )
    raw = login.cookies["eternal_session"]
    db["sessions"].update_one(
        {"token_hash": hash_token(raw)},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}},
    )
    me = app_client.get("/api/auth/me")
    assert me.status_code == 401


def test_logout_invalidates_session(app_client, db) -> None:
    _seed_admin(db)
    login = app_client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "password-kuat-123"},
    )
    csrf = login.json()["csrf_token"]
    logout = app_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": csrf},
    )
    assert logout.status_code == 200
    me = app_client.get("/api/auth/me")
    assert me.status_code == 401
    raw = login.cookies.get("eternal_session")
    if raw:
        doc = db["sessions"].find_one({"token_hash": hash_token(raw)})
        assert doc is not None
        assert doc.get("revoked_at") is not None


def test_csrf_rejection_on_logout(app_client, db) -> None:
    _seed_admin(db)
    login = app_client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "password-kuat-123"},
    )
    assert login.status_code == 200
    denied = app_client.post("/api/auth/logout")
    assert denied.status_code == 403
    denied2 = app_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": "token-palsu"},
    )
    assert denied2.status_code == 403


def test_login_rate_limit(app_client, db) -> None:
    _seed_admin(db)
    for _ in range(5):
        response = app_client.post(
            "/api/auth/login",
            json={"email": "admin@eternal.local", "password": "salah"},
        )
        assert response.status_code == 401
    blocked = app_client.post(
        "/api/auth/login",
        json={"email": "admin@eternal.local", "password": "salah"},
    )
    assert blocked.status_code == 429
