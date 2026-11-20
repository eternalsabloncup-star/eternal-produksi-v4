"""Settings V4 — users/roles/permissions/org/audit focused tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.models.user import normalize_email

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


def test_settings_users_crud_and_forbid_without_permission(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    db["users"].insert_one(
        {
            "email_normalized": normalize_email("plain@eternal.local"),
            "display_name": "Plain",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": "helper",
            "is_active": True,
            "effective_module_permissions": [],
        }
    )
    plain_csrf = _login(app_client, "plain@eternal.local")
    denied = app_client.get(
        "/api/settings/users", headers={"X-CSRF-Token": plain_csrf}
    )
    # GET uses session only but service checks permission → 403
    assert denied.status_code == 403

    csrf = _login(app_client, "owner@eternal.local")
    created = app_client.post(
        "/api/settings/users",
        json={
            "email": "op@eternal.local",
            "display_name": "Operator Satu",
            "password": PASSWORD,
            "role": "user",
            "operational_role": "operator",
            "branch_ids": ["JKT"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["email"] == "op@eternal.local"
    assert "password" not in body and "password_hash" not in body
    uid = body["id"]

    listed = app_client.get("/api/settings/users")
    assert listed.status_code == 200
    assert any(u["id"] == uid for u in listed.json()["items"])

    # Cannot create super_admin via API
    bad = app_client.post(
        "/api/settings/users",
        json={
            "email": "evil@eternal.local",
            "display_name": "Evil",
            "password": PASSWORD,
            "role": "super_admin",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 422


def test_custom_role_default_deny_and_permission_version_conflict(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    csrf = _login(app_client)
    role = app_client.post(
        "/api/settings/roles",
        json={"role_key": "viewer-ops", "label": "Viewer Ops"},
        headers={"X-CSRF-Token": csrf},
    )
    assert role.status_code == 200, role.text
    assert role.json()["module_permissions"] == []
    assert role.json()["version"] == 1

    dup = app_client.post(
        "/api/settings/roles/admin/duplicate",
        json={"new_key": "admin-copy", "label": "Admin Copy"},
        headers={"X-CSRF-Token": csrf},
    )
    assert dup.status_code == 200, dup.text
    # Sensitive stripped from admin duplicate
    assert "users.manage" not in dup.json()["module_permissions"]
    assert "operations.full" not in dup.json()["module_permissions"]

    save = app_client.put(
        "/api/settings/roles/viewer-ops/permissions",
        json={
            "module_permissions": ["reports.view"],
            "expected_version": 1,
            "confirm_sensitive": False,
        },
        headers={"X-CSRF-Token": csrf, "If-Match": "1"},
    )
    assert save.status_code == 200, save.text
    assert save.json()["version"] == 2

    conflict = app_client.put(
        "/api/settings/roles/viewer-ops/permissions",
        json={
            "module_permissions": ["reports.view", "audit.view"],
            "expected_version": 1,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409

    sensitive = app_client.put(
        "/api/settings/roles/viewer-ops/permissions",
        json={
            "module_permissions": ["reports.view", "users.manage"],
            "expected_version": 2,
            "confirm_sensitive": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert sensitive.status_code == 422


def test_deactivate_role_blocked_when_users_exist(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    csrf = _login(app_client)
    app_client.post(
        "/api/settings/roles",
        json={"role_key": "temp-role", "label": "Temp"},
        headers={"X-CSRF-Token": csrf},
    )
    app_client.post(
        "/api/settings/users",
        json={
            "email": "tempuser@eternal.local",
            "display_name": "Temp User",
            "password": PASSWORD,
            "role": "temp-role",
        },
        headers={"X-CSRF-Token": csrf},
    )
    blocked = app_client.post(
        "/api/settings/roles/temp-role/deactivate",
        json={"is_active": False, "reason": "cleanup"},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 409


def test_branch_machine_shift_and_audit_read(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    csrf = _login(app_client)
    br = app_client.put(
        "/api/settings/branches",
        json={"code": "jkt", "name": "Jakarta"},
        headers={"X-CSRF-Token": csrf},
    )
    assert br.status_code == 200
    assert br.json()["code"] == "JKT"

    mc = app_client.put(
        "/api/settings/machines",
        json={"code": "m1", "label": "Mesin 1", "branch_code": "JKT"},
        headers={"X-CSRF-Token": csrf},
    )
    assert mc.status_code == 200

    sh = app_client.put(
        "/api/settings/shifts",
        json={
            "code": "s1",
            "label": "Pagi",
            "start_time": "08:00",
            "end_time": "16:00",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert sh.status_code == 200

    audit = app_client.get("/api/settings/audit?module=settings&limit=20")
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1
    assert all("password" not in str(i.get("detail")) for i in audit.json()["items"])


def test_products_adapter_uses_product_master(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    csrf = _login(app_client)
    listed = app_client.get("/api/settings/products")
    assert listed.status_code == 200, listed.text
    assert listed.json()["source"] == "product_master_repository"
    assert listed.json()["screen_unset_label"] == "Belum diatur"

    saved = app_client.put(
        "/api/settings/products/screen-size",
        json={"item_code": "ITEM-A", "screen_size": "40x50"},
        headers={"X-CSRF-Token": csrf},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["display_screen_size"] == "40x50"

    # Satu sumber: operational master products melihat data yang sama
    via_ops = app_client.get("/api/master/products")
    assert via_ops.status_code == 200, via_ops.text
    codes = {i["item_code"] for i in via_ops.json()["items"]}
    assert "ITEM-A" in codes


def test_accurate_status_no_secrets(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _login(app_client)
    res = app_client.get("/api/settings/accurate")
    assert res.status_code == 200
    body = res.json()
    text = res.text.lower()
    assert "sj_live_sync_enabled" in text
    assert body.get("manual_sync_button") is False
    assert body.get("po_auto_sync_enabled") is False
    assert body.get("invoice_auto_sync_enabled") is False
    assert body.get("receipt_auto_sync_enabled") is False
    assert body.get("auto_sync_interval_seconds") == 60
    assert "verified" in str(body.get("receipt_contract", {}).get("status") or "")
    assert "verified" in str(body.get("invoice_contract", {}).get("status") or "")
    assert "auto_sync" in body
    assert body["auto_sync"].get("manual_sync_button") is False
    assert {j["job"] for j in body["auto_sync"]["jobs"]} == {
        "po",
        "invoice",
        "receipt",
        "sj",
    }
    assert "accurate_api_token" not in text or "configured" in text
    # raw token value must not appear
    assert "bearer" not in text


def test_manual_task_type_create(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    csrf = _login(app_client)
    # seed master indexes via existing ensure in fixture
    from app.repositories import master_repository as master

    master.seed_master_defaults(db)
    res = app_client.post(
        "/api/settings/task-types/manual",
        json={
            "task_type": "catatan-manual",
            "label": "Catatan Manual",
            "scope": "po",
            "assigned_role": "helper",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    assert res.json()["kind"] == "manual"
    assert res.json()["has_workflow_handler"] is False

    # Cannot deactivate registered workflow type
    blocked = app_client.post(
        "/api/settings/task-types/design/deactivate",
        json={"is_active": False, "reason": "x"},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 422


def test_last_super_admin_cannot_deactivate(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    csrf = _login(app_client)
    owner = db["users"].find_one({"email_normalized": "owner@eternal.local"})
    assert owner is not None
    res = app_client.post(
        f"/api/settings/users/{owner['_id']}/active",
        json={"is_active": False, "reason": "lockout-test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 422


def test_sessions_list_hides_token_and_workflow_policies_readonly(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    csrf = _login(app_client)
    owner = db["users"].find_one({"email_normalized": "owner@eternal.local"})
    assert owner is not None
    sessions = app_client.get(f"/api/settings/users/{owner['_id']}/sessions")
    assert sessions.status_code == 200, sessions.text
    payload = sessions.json()
    assert "items" in payload
    blob = str(payload).lower()
    assert "token_hash" not in blob
    assert "session_token" not in blob
    assert "csrf" not in blob or "csrf_token" not in blob

    policies = app_client.get("/api/settings/workflow-policies")
    assert policies.status_code == 200
    items = policies.json()["items"]
    assert any(p["id"] == "payment_bypass" for p in items)
    assert any(p["id"] == "freeform_transition_editor" and p["status"] == "unavailable" for p in items)
    assert any(
        p["id"] == "master_products_screen_size" and p["status"] == "available"
        for p in items
    )
