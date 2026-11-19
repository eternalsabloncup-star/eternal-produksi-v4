"""API Master + tugas operasional: auth, CSRF, RBAC, idempotency, gate Qty."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.domain.operational import TaskRef
from app.models.user import normalize_email
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks

PASSWORD = "password-kuat-123"


def _seed_super_admin(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _seed_operational_user(db: Database, email: str, operational_role: str) -> None:
    db["users"].insert_one(
        {
            "email_normalized": normalize_email(email),
            "display_name": email,
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": operational_role,
            "is_active": True,
        }
    )


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _seed_prep_task(db: Database, qty_target: float = 1000) -> str:
    master.seed_master_defaults(db)
    task = tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(
            accurate_po_id="95106",
            po_no="PO/18/11/2026/000010",
            accurate_line_id="106318",
            item_code="100359",
            process_kind="Cetak",
        ),
        qty_target=qty_target,
        label="Persiapan Cup",
    )
    return task["task_key"]


def test_master_task_types_requires_session(app_client: TestClient) -> None:
    assert app_client.get("/api/master/task-types").status_code == 401
    assert app_client.get("/api/tasks").status_code == 401


def test_master_task_types_listed_after_login(
    app_client: TestClient, db: Database
) -> None:
    _seed_super_admin(db)
    _login(app_client, "owner@eternal.local")
    res = app_client.get("/api/master/task-types")
    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 12
    assert "production-head" in body["roles"]
    design = next(i for i in body["items"] if i["task_type"] == "design")
    assert design["assigned_role"] == "designer"
    assert design["scope"] == "po"


def test_master_write_requires_csrf_and_permission(
    app_client: TestClient, db: Database
) -> None:
    _seed_super_admin(db)
    _seed_operational_user(db, "helper@eternal.local", "helper")

    csrf = _login(app_client, "owner@eternal.local")
    no_csrf = app_client.put(
        "/api/master/task-types/qc-packing/role", json={"role": "screener"}
    )
    assert no_csrf.status_code == 403

    ok = app_client.put(
        "/api/master/task-types/qc-packing/role",
        json={"role": "screener"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200
    assert ok.json()["assigned_role"] == "screener"

    helper_csrf = _login(app_client, "helper@eternal.local")
    denied = app_client.put(
        "/api/master/task-types/qc-packing/role",
        json={"role": "helper"},
        headers={"X-CSRF-Token": helper_csrf},
    )
    assert denied.status_code == 403


def test_confirm_task_enforces_role_from_master(
    app_client: TestClient, db: Database
) -> None:
    task_key = _seed_prep_task(db)
    _seed_super_admin(db)
    _seed_operational_user(db, "driver@eternal.local", "driver")
    _seed_operational_user(db, "helper@eternal.local", "helper")

    driver_csrf = _login(app_client, "driver@eternal.local")
    denied = app_client.post(
        f"/api/tasks/{task_key}/confirm",
        json={"qty": 100, "idempotency_key": "api-driver-001"},
        headers={"X-CSRF-Token": driver_csrf},
    )
    assert denied.status_code == 403
    assert tasks.get_task(db, task_key)["qty_confirmed"] == 0

    helper_csrf = _login(app_client, "helper@eternal.local")
    allowed = app_client.post(
        f"/api/tasks/{task_key}/confirm",
        json={"qty": 100, "idempotency_key": "api-helper-001"},
        headers={"X-CSRF-Token": helper_csrf},
    )
    assert allowed.status_code == 200
    assert allowed.json()["task"]["qty_confirmed"] == 100


def test_confirm_is_idempotent_over_http(app_client: TestClient, db: Database) -> None:
    task_key = _seed_prep_task(db)
    _seed_super_admin(db)
    _seed_operational_user(db, "helper@eternal.local", "helper")
    csrf = _login(app_client, "helper@eternal.local")

    payload = {"qty": 250, "idempotency_key": "api-double-click-001"}
    first = app_client.post(
        f"/api/tasks/{task_key}/confirm", json=payload, headers={"X-CSRF-Token": csrf}
    )
    second = app_client.post(
        f"/api/tasks/{task_key}/confirm", json=payload, headers={"X-CSRF-Token": csrf}
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert tasks.get_task(db, task_key)["qty_confirmed"] == 250


def test_confirm_over_target_returns_conflict(
    app_client: TestClient, db: Database
) -> None:
    task_key = _seed_prep_task(db, qty_target=300)
    _seed_super_admin(db)
    _seed_operational_user(db, "helper@eternal.local", "helper")
    csrf = _login(app_client, "helper@eternal.local")

    app_client.post(
        f"/api/tasks/{task_key}/confirm",
        json={"qty": 300, "idempotency_key": "api-full-001"},
        headers={"X-CSRF-Token": csrf},
    )
    over = app_client.post(
        f"/api/tasks/{task_key}/confirm",
        json={"qty": 1, "idempotency_key": "api-over-001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert over.status_code == 409
    assert tasks.get_task(db, task_key)["qty_confirmed"] == 300


def test_task_list_and_history_persist(app_client: TestClient, db: Database) -> None:
    task_key = _seed_prep_task(db)
    _seed_super_admin(db)
    _seed_operational_user(db, "helper@eternal.local", "helper")
    csrf = _login(app_client, "helper@eternal.local")

    app_client.post(
        f"/api/tasks/{task_key}/confirm",
        json={"qty": 120, "idempotency_key": "api-hist-001"},
        headers={"X-CSRF-Token": csrf},
    )
    app_client.post(
        f"/api/tasks/{task_key}/confirm",
        json={"qty": 80, "idempotency_key": "api-hist-002"},
        headers={"X-CSRF-Token": csrf},
    )

    listed = app_client.get("/api/tasks?role=helper&done=false")
    assert listed.status_code == 200
    body = listed.json()
    assert body["source"] == "mongodb"
    assert body["total"] == 1
    assert body["items"][0]["qty_confirmed"] == 200
    assert body["items"][0]["status"] == "sebagian"

    history = app_client.get(f"/api/tasks/{task_key}/confirmations")
    assert history.status_code == 200
    assert [r["qty"] for r in history.json()["items"]] == [120, 80]


def test_missing_task_returns_404(app_client: TestClient, db: Database) -> None:
    master.seed_master_defaults(db)
    _seed_super_admin(db)
    csrf = _login(app_client, "owner@eternal.local")
    res = app_client.post(
        "/api/tasks/task:persiapan-cup:po:0:line:0/confirm",
        json={"qty": 1, "idempotency_key": "api-missing-001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 404
