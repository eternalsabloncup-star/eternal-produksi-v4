"""operations.tasks.view_all + Holding auto — read-only, anti spoof, no mutation."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.auth.permissions import PERM_VIEW_ALL_TASKS, has_permission, has_view_all_tasks
from app.auth.roles import ROLE_HOLDING
from app.bootstrap import create_admin_user
from app.domain.operational import TaskRef
from app.models.user import normalize_email
from app.repositories import master_repository as master
from app.repositories import settings_role_repository as settings_roles
from app.repositories import task_repository as tasks
from app.services import task_engine

PASSWORD = "password-kuat-123"
BRANCH_HQ = "HQ"
BRANCH_BDG = "BDG"
PO_HQ = "PO-HQ"
PO_BDG = "PO-BDG"


def _seed_sa(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _seed_po(db: Database, po_id: str, branch_id: str) -> None:
    from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, INBOX_STATUS_ACCEPTED

    db["accurate_sales_orders"].insert_one(
        {
            "accurate_id": po_id,
            "branch_id": branch_id,
            "number": po_id,
            "operational": {
                "acceptance": {"status": ACCEPTANCE_ACCEPTED},
                "inbox_status": INBOX_STATUS_ACCEPTED,
            },
        }
    )


def _seed_user(
    db: Database,
    email: str,
    *,
    role: str = "user",
    operational_role: str | None = None,
    permissions: list[str] | None = None,
    branch_ids: list[str] | None = None,
) -> None:
    db["users"].insert_one(
        {
            "email_normalized": normalize_email(email),
            "display_name": email,
            "password_hash": hash_password(PASSWORD),
            "role": role,
            "operational_role": operational_role,
            "is_active": True,
            "branch_ids": branch_ids if branch_ids is not None else [BRANCH_HQ],
            "effective_module_permissions": permissions or [],
        }
    )


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _seed_tasks(db: Database) -> None:
    master.seed_master_defaults(db)
    _seed_po(db, PO_HQ, BRANCH_HQ)
    _seed_po(db, PO_BDG, BRANCH_BDG)
    for role, line in (
        ("helper", "L-H"),
        ("designer", "L-D"),
        ("operator", "L-O"),
    ):
        tasks.upsert_task(
            db,
            task_type="design" if role == "designer" else "persiapan-cup",
            scope="po" if role == "designer" else "po_line",
            assigned_role=role,
            ref=TaskRef(
                accurate_po_id=PO_HQ,
                po_no=PO_HQ,
                accurate_line_id=None if role == "designer" else line,
                item_code="X",
                process_kind="Cetak",
            ),
            qty_target=10,
            label=role,
        )
    tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(
            accurate_po_id=PO_BDG,
            po_no=PO_BDG,
            accurate_line_id="L-BDG",
            item_code="X",
            process_kind="Cetak",
        ),
        qty_target=5,
        label="helper-bdg",
    )


def test_holding_auto_view_all_without_manual_grant(db: Database) -> None:
    user = {
        "role": ROLE_HOLDING,
        "effective_module_permissions": [],
        "operational_role": None,
    }
    assert has_view_all_tasks(user)
    assert has_permission(user, PERM_VIEW_ALL_TASKS)
    assert not has_permission(user, "operations.full")


def test_holding_reads_all_roles_and_mutation_denied(
    app_client: TestClient, db: Database
) -> None:
    _seed_sa(db)
    settings_roles.ensure_system_roles(db)
    settings_roles.create_custom_role(
        db, role_key=ROLE_HOLDING, label="Holding", module_permissions=[]
    )
    _seed_tasks(db)
    _seed_user(db, "holding@eternal.local", role=ROLE_HOLDING, permissions=[])
    _seed_user(db, "helper@eternal.local", operational_role="helper")

    csrf = _login(app_client, "holding@eternal.local")
    me = app_client.get("/api/auth/me")
    assert me.status_code == 200
    assert PERM_VIEW_ALL_TASKS in me.json()["user"]["permissions"]

    all_roles = app_client.get("/api/tasks")
    assert all_roles.status_code == 200
    roles = {i["assigned_role"] for i in all_roles.json()["items"]}
    assert "helper" in roles and "designer" in roles

    designer = app_client.get("/api/tasks?role=designer")
    assert designer.status_code == 200
    assert all(i["assigned_role"] == "designer" for i in designer.json()["items"])

    counts = app_client.get("/api/tasks/open-counts")
    assert counts.status_code == 200
    assert counts.json()["counts"]["helper"] >= 1
    assert counts.json()["counts"]["designer"] >= 1

    helper_task = next(
        i for i in all_roles.json()["items"] if i["assigned_role"] == "helper"
    )
    denied = app_client.post(
        f"/api/tasks/{helper_task['task_key']}/confirm",
        json={"qty": 1, "idempotency_key": "hold-mut-001"},
        headers={"X-CSRF-Token": csrf},
    )
    assert denied.status_code == 403


def test_non_holding_without_grant_fail_closed(
    app_client: TestClient, db: Database
) -> None:
    _seed_sa(db)
    _seed_tasks(db)
    _seed_user(db, "norole@eternal.local", role="user", permissions=[])
    _login(app_client, "norole@eternal.local")
    empty = app_client.get("/api/tasks")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    spoof = app_client.get("/api/tasks?role=helper")
    assert spoof.status_code == 403


def test_group_and_per_user_grant_view_all(
    app_client: TestClient, db: Database
) -> None:
    _seed_sa(db)
    settings_roles.ensure_system_roles(db)
    settings_roles.create_custom_role(
        db,
        role_key="viewer-ops",
        label="Viewer Ops",
        module_permissions=[PERM_VIEW_ALL_TASKS],
    )
    _seed_tasks(db)
    _seed_user(
        db,
        "group@eternal.local",
        role="viewer-ops",
        permissions=[PERM_VIEW_ALL_TASKS],
    )
    _seed_user(
        db,
        "peruser@eternal.local",
        role="user",
        permissions=[PERM_VIEW_ALL_TASKS],
    )

    _login(app_client, "group@eternal.local")
    g = app_client.get("/api/tasks?role=operator")
    assert g.status_code == 200
    assert g.json()["total"] >= 1

    _login(app_client, "peruser@eternal.local")
    u = app_client.get("/api/tasks")
    assert u.status_code == 200
    assert u.json()["total"] >= 1


def test_helper_isolation_and_spoof_still_denied(
    app_client: TestClient, db: Database
) -> None:
    _seed_sa(db)
    _seed_tasks(db)
    _seed_user(db, "helper@eternal.local", operational_role="helper")
    _login(app_client, "helper@eternal.local")
    own = app_client.get("/api/tasks")
    assert own.status_code == 200
    assert all(i["assigned_role"] == "helper" for i in own.json()["items"])
    assert app_client.get("/api/tasks?role=designer").status_code == 403


def test_view_all_respects_branch_scope(app_client: TestClient, db: Database) -> None:
    _seed_sa(db)
    _seed_tasks(db)
    _seed_user(
        db,
        "hold-hq@eternal.local",
        role=ROLE_HOLDING,
        permissions=[],
        branch_ids=[BRANCH_HQ],
    )
    _login(app_client, "hold-hq@eternal.local")
    listed = app_client.get("/api/tasks?role=helper")
    assert listed.status_code == 200
    for item in listed.json()["items"]:
        assert item["ref"]["accurate_po_id"] == PO_HQ


def test_holding_sees_loading_sj_of_other_assignee(
    app_client: TestClient, db: Database
) -> None:
    """Read parity: view_all melihat Loading SJ assignee lain; helper tetap isolasi."""
    _seed_sa(db)
    settings_roles.ensure_system_roles(db)
    settings_roles.create_custom_role(
        db, role_key=ROLE_HOLDING, label="Holding", module_permissions=[]
    )
    master.seed_master_defaults(db)
    _seed_po(db, PO_HQ, BRANCH_HQ)
    db["accurate_delivery_notes"].insert_one(
        {
            "accurate_sj_id": "SJ-HOLD",
            "branch_id": BRANCH_HQ,
            "number": "SJ-HOLD",
            "accurate_po_id": PO_HQ,
            "accurate_po_ids": [PO_HQ],
        }
    )
    tasks.upsert_task(
        db,
        task_type="loading-sj",
        scope="delivery_note",
        assigned_role="helper",
        ref=TaskRef(accurate_delivery_note_id="SJ-HOLD"),
        label="load-other",
        context={
            "assigned_user_email": "helper-a@eternal.local",
            "accurate_po_ids": [PO_HQ],
        },
    )
    _seed_user(db, "holding@eternal.local", role=ROLE_HOLDING, permissions=[])
    _seed_user(db, "helper-a@eternal.local", operational_role="helper")
    _seed_user(db, "helper-b@eternal.local", operational_role="helper")

    def _has_assigned_load(items: list) -> bool:
        return any(
            i.get("task_type") == "loading-sj"
            and (i.get("context") or {}).get("assigned_user_email")
            == "helper-a@eternal.local"
            for i in items
        )

    _login(app_client, "holding@eternal.local")
    hold_list = app_client.get("/api/tasks?role=helper&task_type=loading-sj")
    assert hold_list.status_code == 200
    assert _has_assigned_load(hold_list.json()["items"])

    _login(app_client, "helper-b@eternal.local")
    other = app_client.get("/api/tasks?role=helper&task_type=loading-sj")
    assert other.status_code == 200
    assert not _has_assigned_load(other.json()["items"])

    _login(app_client, "helper-a@eternal.local")
    own = app_client.get("/api/tasks?role=helper&task_type=loading-sj")
    assert own.status_code == 200
    assert _has_assigned_load(own.json()["items"])


def test_view_all_does_not_grant_can_act(db: Database) -> None:
    master.seed_master_defaults(db)
    user = {
        "role": ROLE_HOLDING,
        "effective_module_permissions": [PERM_VIEW_ALL_TASKS],
        "operational_role": None,
        "email_normalized": "h@eternal.local",
    }
    assert not task_engine.can_act(
        db, user, task_type="persiapan-cup", action="confirm"
    )


def test_catalog_exposes_holding_policy(app_client: TestClient, db: Database) -> None:
    _seed_sa(db)
    _login(app_client, "owner@eternal.local")
    cat = app_client.get("/api/settings/catalog")
    assert cat.status_code == 200
    body = cat.json()
    assert PERM_VIEW_ALL_TASKS in body["module_permissions"]
    assert ROLE_HOLDING in body["policy_grants"]
    grant = body["policy_grants"][ROLE_HOLDING][0]
    assert grant["permission"] == PERM_VIEW_ALL_TASKS
    assert grant["revocable"] is False
