"""Gate visibilitas tugas: PO accepted = tampil; SJ tidak menyembunyikan."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.models.user import normalize_email
from app.repositories.task_repository import TASK_STATUS_OPEN
from app.services.object_auth import filter_tasks_for_actor, task_source_po_accepted
from app.services.po_acceptance import (
    ACCEPTANCE_ACCEPTED,
    ACCEPTANCE_PENDING,
    INBOX_STATUS_ACCEPTED,
    INBOX_STATUS_NEW,
)
from app.services.task_open_counts import count_actionable_open_by_role

PASSWORD = "password-kuat-123"
BRANCH_HQ = "HQ"


def _seed_sa(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _seed_user(
    db: Database,
    email: str,
    *,
    operational_role: str = "helper",
) -> None:
    db["users"].insert_one(
        {
            "email_normalized": normalize_email(email),
            "display_name": email,
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": operational_role,
            "is_active": True,
            "branch_ids": [BRANCH_HQ],
        }
    )


def _login(client: TestClient, email: str) -> None:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text


def _seed_po(db: Database, po_id: str, *, accepted: bool) -> None:
    status = ACCEPTANCE_ACCEPTED if accepted else ACCEPTANCE_PENDING
    db["accurate_sales_orders"].insert_one(
        {
            "accurate_id": po_id,
            "branch_id": BRANCH_HQ,
            "number": po_id,
            "operational": {
                "acceptance": {"status": status},
                "inbox_status": (
                    INBOX_STATUS_ACCEPTED if accepted else INBOX_STATUS_NEW
                ),
            },
        }
    )


def _seed_sj(db: Database, sj_id: str, po_id: str) -> None:
    db["accurate_delivery_notes"].insert_one(
        {
            "accurate_sj_id": sj_id,
            "branch_id": BRANCH_HQ,
            "number": sj_id,
            "accurate_po_id": po_id,
            "accurate_po_ids": [po_id],
        }
    )


def _insert_helper_task(
    db: Database,
    *,
    key: str,
    po_id: str | None = None,
    sj_id: str | None = None,
    task_type: str = "loading-menunggu-sj",
) -> dict:
    ref: dict = {}
    ctx: dict = {}
    if po_id:
        ref["accurate_po_id"] = po_id
    if sj_id:
        ref["accurate_delivery_note_id"] = sj_id
        if po_id:
            ctx["accurate_po_ids"] = [po_id]
    doc = {
        "task_key": key,
        "task_type": task_type,
        "scope": "delivery_note" if sj_id and not po_id else "po_line",
        "assigned_role": "helper",
        "status": TASK_STATUS_OPEN,
        "qty_confirmed": 0,
        "ref": ref,
        "context": ctx,
    }
    db["operational_tasks"].insert_one(doc)
    return doc


def test_A_accepted_without_sj_visible(db: Database) -> None:
    _seed_po(db, "PO-A", accepted=True)
    task = _insert_helper_task(db, key="t-a", po_id="PO-A")
    assert task_source_po_accepted(db, task) is True
    user = {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    keys = {t["task_key"] for t in filter_tasks_for_actor(db, user, [task])}
    assert keys == {"t-a"}
    assert count_actionable_open_by_role(db, user)["helper"] == 1


def test_B_accepted_with_sj_still_visible(db: Database) -> None:
    _seed_po(db, "PO-B", accepted=True)
    _seed_sj(db, "SJ-B", "PO-B")
    prelim = _insert_helper_task(db, key="t-prelim", po_id="PO-B")
    loading = _insert_helper_task(
        db,
        key="t-load",
        po_id=None,
        sj_id="SJ-B",
        task_type="loading-sj",
    )
    user = {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    filtered = filter_tasks_for_actor(db, user, [prelim, loading])
    keys = {t["task_key"] for t in filtered}
    assert keys == {"t-prelim", "t-load"}
    assert count_actionable_open_by_role(db, user)["helper"] == 2


def test_C_non_accepted_without_sj_hidden(db: Database) -> None:
    _seed_po(db, "PO-C", accepted=False)
    task = _insert_helper_task(db, key="t-c", po_id="PO-C")
    assert task_source_po_accepted(db, task) is False
    user = {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    assert filter_tasks_for_actor(db, user, [task]) == []
    assert count_actionable_open_by_role(db, user)["helper"] == 0


def test_D_non_accepted_with_sj_hidden(db: Database) -> None:
    _seed_po(db, "PO-D", accepted=False)
    _seed_sj(db, "SJ-D", "PO-D")
    prelim = _insert_helper_task(db, key="t-d-prelim", po_id="PO-D")
    loading = _insert_helper_task(
        db,
        key="t-d-load",
        po_id=None,
        sj_id="SJ-D",
        task_type="loading-sj",
    )
    user = {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    assert filter_tasks_for_actor(db, user, [prelim, loading]) == []
    assert count_actionable_open_by_role(db, user)["helper"] == 0


def test_E_api_list_and_counts_parity(
    app_client: TestClient, db: Database
) -> None:
    _seed_sa(db)
    _seed_user(db, "helper@eternal.local", operational_role="helper")
    _seed_po(db, "PO-OK", accepted=True)
    _seed_po(db, "PO-NO", accepted=False)
    _seed_sj(db, "SJ-OK", "PO-OK")
    _seed_sj(db, "SJ-NO", "PO-NO")
    _insert_helper_task(db, key="ok-prelim", po_id="PO-OK")
    _insert_helper_task(
        db, key="ok-load", po_id=None, sj_id="SJ-OK", task_type="loading-sj"
    )
    _insert_helper_task(db, key="no-prelim", po_id="PO-NO")
    _insert_helper_task(
        db, key="no-load", po_id=None, sj_id="SJ-NO", task_type="loading-sj"
    )

    _login(app_client, "helper@eternal.local")
    listed = app_client.get("/api/tasks?role=helper")
    assert listed.status_code == 200, listed.text
    keys = {i["task_key"] for i in listed.json()["items"]}
    assert "ok-prelim" in keys
    assert "ok-load" in keys
    assert "no-prelim" not in keys
    assert "no-load" not in keys

    counts = app_client.get("/api/tasks/open-counts")
    assert counts.status_code == 200, counts.text
    body = counts.json()
    assert body["accepted_only"] is True
    assert body["counts"]["helper"] == 2


def test_E_permission_isolation_unchanged(
    app_client: TestClient, db: Database
) -> None:
    """Helper hanya melihat role sendiri; SA melihat semua role accepted."""
    _seed_sa(db)
    _seed_user(db, "helper@eternal.local", operational_role="helper")
    _seed_user(db, "designer@eternal.local", operational_role="designer")
    _seed_po(db, "PO-OK", accepted=True)
    _insert_helper_task(db, key="h1", po_id="PO-OK", task_type="persiapan-cup")
    db["operational_tasks"].insert_one(
        {
            "task_key": "d1",
            "task_type": "design",
            "scope": "po",
            "assigned_role": "designer",
            "status": TASK_STATUS_OPEN,
            "qty_confirmed": 0,
            "ref": {"accurate_po_id": "PO-OK"},
            "context": {},
        }
    )

    _login(app_client, "helper@eternal.local")
    helper_list = app_client.get("/api/tasks?role=helper")
    assert all(i["assigned_role"] == "helper" for i in helper_list.json()["items"])
    spoof = app_client.get("/api/tasks?role=designer")
    assert spoof.status_code == 403

    _login(app_client, "owner@eternal.local")
    all_roles = app_client.get("/api/tasks")
    assert all_roles.status_code == 200
    roles = {i["assigned_role"] for i in all_roles.json()["items"]}
    assert "helper" in roles and "designer" in roles
