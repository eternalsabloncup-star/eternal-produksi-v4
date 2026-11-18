"""Focused tests for UI table preference API (not business data)."""

from __future__ import annotations

from app.bootstrap import create_admin_user


def _seed_user(db, *, email: str, name: str):
    return create_admin_user(
        db,
        email=email,
        display_name=name,
        password="password-kuat-123",
        allow_if_admin_exists=True,
    )


def _login(client, email: str = "admin@eternal.local"):
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "password-kuat-123"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_get_default_when_missing(app_client, db) -> None:
    _seed_user(db, email="admin@eternal.local", name="Admin")
    _login(app_client)
    response = app_client.get("/api/me/table-preferences/po-demo:inbox")
    assert response.status_code == 200
    body = response.json()
    assert body["table_id"] == "po-demo:inbox"
    assert body["column_order"] == []
    assert body["column_widths"] == {}
    assert body["hidden_columns"] == []


def test_put_and_get_round_trip(app_client, db) -> None:
    _seed_user(db, email="admin@eternal.local", name="Admin")
    csrf = _login(app_client)
    payload = {
        "version": 1,
        "column_order": ["id", "customer", "status"],
        "column_widths": {"id": 120, "customer": 180},
        "hidden_columns": ["machine"],
    }
    put = app_client.put(
        "/api/me/table-preferences/po-demo:inbox",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert put.status_code == 200
    assert put.json()["column_order"] == payload["column_order"]

    get = app_client.get("/api/me/table-preferences/po-demo:inbox")
    assert get.status_code == 200
    assert get.json()["hidden_columns"] == ["machine"]
    assert get.json()["column_widths"]["id"] == 120


def test_upsert_idempotent_no_duplicate(app_client, db) -> None:
    _seed_user(db, email="admin@eternal.local", name="Admin")
    csrf = _login(app_client)
    payload = {
        "version": 1,
        "column_order": ["id"],
        "column_widths": {"id": 100},
        "hidden_columns": [],
    }
    for _ in range(2):
        r = app_client.put(
            "/api/me/table-preferences/po-demo:control",
            json=payload,
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200
    count = db["ui_table_preferences"].count_documents({})
    assert count == 1


def test_isolation_between_users(app_client, db) -> None:
    _seed_user(db, email="a@eternal.local", name="User A")
    _seed_user(db, email="b@eternal.local", name="User B")

    csrf_a = _login(app_client, "a@eternal.local")
    app_client.put(
        "/api/me/table-preferences/team-tasks:prepress",
        json={
            "version": 1,
            "column_order": ["id", "title"],
            "column_widths": {"id": 110},
            "hidden_columns": ["due"],
        },
        headers={"X-CSRF-Token": csrf_a},
    )

    app_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": csrf_a},
    )
    _login(app_client, "b@eternal.local")
    get_b = app_client.get("/api/me/table-preferences/team-tasks:prepress")
    assert get_b.status_code == 200
    assert get_b.json()["column_order"] == []
    assert get_b.json()["hidden_columns"] == []


def test_unauthenticated_rejected(app_client, db) -> None:
    response = app_client.get("/api/me/table-preferences/po-demo:inbox")
    assert response.status_code == 401


def test_csrf_required_on_put(app_client, db) -> None:
    _seed_user(db, email="admin@eternal.local", name="Admin")
    _login(app_client)
    response = app_client.put(
        "/api/me/table-preferences/po-demo:inbox",
        json={
            "version": 1,
            "column_order": ["id"],
            "column_widths": {},
            "hidden_columns": [],
        },
    )
    assert response.status_code == 403


def test_invalid_table_id_rejected(app_client, db) -> None:
    _seed_user(db, email="admin@eternal.local", name="Admin")
    _login(app_client)
    response = app_client.get("/api/me/table-preferences/bad id!")
    assert response.status_code == 422


def test_invalid_width_rejected(app_client, db) -> None:
    _seed_user(db, email="admin@eternal.local", name="Admin")
    csrf = _login(app_client)
    response = app_client.put(
        "/api/me/table-preferences/po-demo:inbox",
        json={
            "version": 1,
            "column_order": ["id"],
            "column_widths": {"id": 10},
            "hidden_columns": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_reset_delete(app_client, db) -> None:
    _seed_user(db, email="admin@eternal.local", name="Admin")
    csrf = _login(app_client)
    app_client.put(
        "/api/me/table-preferences/po-demo:inbox",
        json={
            "version": 1,
            "column_order": ["id"],
            "column_widths": {"id": 120},
            "hidden_columns": [],
        },
        headers={"X-CSRF-Token": csrf},
    )
    deleted = app_client.delete(
        "/api/me/table-preferences/po-demo:inbox",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    get = app_client.get("/api/me/table-preferences/po-demo:inbox")
    assert get.json()["column_order"] == []


def test_indexes_include_table_preferences(db) -> None:
    from app.bootstrap import init_indexes

    created = init_indexes(db)
    assert "uniq_user_table_prefs" in created["ui_table_preferences"]
