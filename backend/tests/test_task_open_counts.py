"""Badge Tugas Tim — open-counts authoritative + Loading-SJ assignee parity."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.models.user import normalize_email
from app.repositories.task_repository import (
    TASK_STATUS_DONE,
    TASK_STATUS_OPEN,
    TASK_STATUS_PARTIAL,
)
from app.services.object_auth import filter_tasks_for_actor, loading_sj_visible_to_actor
from app.services.task_open_counts import count_actionable_open_by_role

PASSWORD = "password-kuat-123"
BRANCH_HQ = "HQ"
BRANCH_BDG = "BDG"


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
    operational_role: str = "driver",
    branch_ids: list[str] | None = None,
    permissions: list[str] | None = None,
) -> dict:
    doc = {
        "email_normalized": normalize_email(email),
        "display_name": email,
        "password_hash": hash_password(PASSWORD),
        "role": "user",
        "operational_role": operational_role,
        "is_active": True,
        "branch_ids": branch_ids if branch_ids is not None else [BRANCH_HQ],
    }
    if permissions:
        doc["effective_module_permissions"] = permissions
    db["users"].insert_one(doc)
    return doc


def _login(client: TestClient, email: str) -> None:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text


def _insert_task(
    db: Database,
    *,
    key: str,
    role: str,
    status: str = TASK_STATUS_OPEN,
    task_type: str = "cetak",
    po_id: str | None = "PO-HQ",
    sj_id: str | None = None,
    assigned_email: str | None = None,
    cancelled: bool = False,
) -> None:
    ref: dict = {}
    if sj_id:
        ref["accurate_delivery_note_id"] = sj_id
    if po_id:
        ref["accurate_po_id"] = po_id
    ctx: dict = {}
    if assigned_email:
        ctx["assigned_user_email"] = assigned_email
    if cancelled:
        ctx["loading_cancelled"] = True
    db["operational_tasks"].insert_one(
        {
            "task_key": key,
            "task_type": task_type,
            "scope": "po_line_allocation",
            "assigned_role": role,
            "status": status,
            "qty_confirmed": 0,
            "ref": ref,
            "context": ctx,
        }
    )


def _seed_po(
    db: Database,
    po_id: str,
    branch_id: str,
    *,
    accepted: bool = True,
) -> None:
    from app.services.po_acceptance import (
        ACCEPTANCE_ACCEPTED,
        ACCEPTANCE_PENDING,
        INBOX_STATUS_ACCEPTED,
        INBOX_STATUS_NEW,
    )

    status = ACCEPTANCE_ACCEPTED if accepted else ACCEPTANCE_PENDING
    db["accurate_sales_orders"].insert_one(
        {
            "accurate_id": po_id,
            "branch_id": branch_id,
            "number": po_id,
            "operational": {
                "acceptance": {"status": status},
                "inbox_status": (
                    INBOX_STATUS_ACCEPTED if accepted else INBOX_STATUS_NEW
                ),
            },
        }
    )


def _seed_sj(
    db: Database,
    sj_id: str,
    branch_id: str,
    *,
    accurate_po_id: str | None = "PO-HQ",
) -> None:
    doc: dict = {
        "accurate_sj_id": sj_id,
        "branch_id": branch_id,
        "number": sj_id,
    }
    if accurate_po_id:
        doc["accurate_po_id"] = accurate_po_id
        doc["accurate_po_ids"] = [accurate_po_id]
    db["accurate_delivery_notes"].insert_one(doc)


def test_loading_sj_assignee_visibility_parity() -> None:
    task = {
        "task_type": "loading-sj",
        "context": {"assigned_user_email": "drva@eternal.local"},
    }
    drv_a = {"email_normalized": "drva@eternal.local", "role": "user"}
    drv_b = {"email_normalized": "drvb@eternal.local", "role": "user"}
    sa = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
    ops = {
        "email_normalized": "ops@eternal.local",
        "role": "user",
        "effective_module_permissions": ["operations.full"],
    }
    holding = {
        "email_normalized": "holding@eternal.local",
        "role": "holding",
        "effective_module_permissions": ["operations.tasks.view_all"],
    }
    view_all_grant = {
        "email_normalized": "viewer@eternal.local",
        "role": "user",
        "effective_module_permissions": ["operations.tasks.view_all"],
    }
    assert loading_sj_visible_to_actor(drv_a, task) is True
    assert loading_sj_visible_to_actor(drv_b, task) is False
    assert loading_sj_visible_to_actor(sa, task) is True
    assert loading_sj_visible_to_actor(ops, task) is True
    # Holding / view_all: read lintas assignee (mutation tetap terpisah).
    assert loading_sj_visible_to_actor(holding, task) is True
    assert loading_sj_visible_to_actor(view_all_grant, task) is True
    assert loading_sj_visible_to_actor(
        drv_b, {"task_type": "cetak", "context": {}}
    )


def test_filter_and_count_assignee_and_branch(db: Database) -> None:
    _seed_po(db, "PO-HQ", BRANCH_HQ)
    _seed_po(db, "PO-BDG", BRANCH_BDG)
    _seed_sj(db, "SJ-A", BRANCH_HQ)

    _insert_task(db, key="d-hq-1", role="designer", po_id="PO-HQ")
    _insert_task(db, key="d-bdg-1", role="designer", po_id="PO-BDG")
    _insert_task(
        db,
        key="load-a",
        role="helper",
        task_type="loading-sj",
        po_id=None,
        sj_id="SJ-A",
        assigned_email="drva@eternal.local",
    )
    _insert_task(
        db,
        key="load-open",
        role="helper",
        task_type="loading-sj",
        po_id=None,
        sj_id="SJ-A",
    )
    _insert_task(
        db,
        key="done",
        role="designer",
        status=TASK_STATUS_DONE,
        po_id="PO-HQ",
    )
    _insert_task(
        db,
        key="cancel",
        role="helper",
        task_type="loading-sj",
        po_id=None,
        sj_id="SJ-A",
        cancelled=True,
    )
    _insert_task(
        db,
        key="partial",
        role="operator",
        status=TASK_STATUS_PARTIAL,
        po_id="PO-HQ",
    )

    drv_a = {
        "email_normalized": "drva@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    drv_b = {
        "email_normalized": "drvb@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    sa = {"email_normalized": "owner@eternal.local", "role": "super_admin"}

    items = list(db["operational_tasks"].find({}, {"_id": 0}))
    filtered_a = filter_tasks_for_actor(db, drv_a, items)
    filtered_b = filter_tasks_for_actor(db, drv_b, items)
    keys_a = {t["task_key"] for t in filtered_a}
    keys_b = {t["task_key"] for t in filtered_b}
    assert "load-a" in keys_a
    assert "load-a" not in keys_b
    assert "d-bdg-1" not in keys_a
    assert "d-hq-1" in keys_a

    counts_a = count_actionable_open_by_role(db, drv_a)
    counts_b = count_actionable_open_by_role(db, drv_b)
    counts_sa = count_actionable_open_by_role(db, sa)

    # Driver A: designer HQ + loading assigned + loading open + operator partial
    assert counts_a["designer"] == 1
    assert counts_a["helper"] == 2  # load-a + load-open (cancel/done excluded)
    assert counts_a["operator"] == 1
    # Driver B: same minus assigned loading-sj
    assert counts_b["designer"] == 1
    assert counts_b["helper"] == 1  # only load-open
    assert counts_b["operator"] == 1
    # SA sees both branches + assigned loading
    assert counts_sa["designer"] == 2
    assert counts_sa["helper"] == 2


def test_count_scales_past_list_limit(db: Database) -> None:
    _seed_po(db, "PO-HQ", BRANCH_HQ)
    docs = [
        {
            "task_key": f"bulk-{i}",
            "task_type": "design",
            "scope": "po",
            "assigned_role": "designer",
            "status": TASK_STATUS_OPEN,
            "qty_confirmed": 0,
            "ref": {"accurate_po_id": "PO-HQ"},
            "context": {},
        }
        for i in range(1500)
    ]
    docs.append(
        {
            "task_key": "bulk-done",
            "task_type": "design",
            "scope": "po",
            "assigned_role": "designer",
            "status": TASK_STATUS_DONE,
            "qty_confirmed": 1,
            "ref": {"accurate_po_id": "PO-HQ"},
            "context": {},
        }
    )
    db["operational_tasks"].insert_many(docs)

    user = {
        "email_normalized": "designer@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    assert count_actionable_open_by_role(db, user)["designer"] == 1500

    # Keep bulk-0..999 → 1000 open (+ done ignored)
    db["operational_tasks"].delete_many(
        {"task_key": {"$regex": r"^bulk-(1[0-4]\d{2})$"}}
    )  # bulk-1000 .. bulk-1499
    assert count_actionable_open_by_role(db, user)["designer"] == 1000

    db["operational_tasks"].insert_one(
        {
            "task_key": "bulk-extra",
            "task_type": "design",
            "scope": "po",
            "assigned_role": "designer",
            "status": TASK_STATUS_OPEN,
            "qty_confirmed": 0,
            "ref": {"accurate_po_id": "PO-HQ"},
            "context": {},
        }
    )
    assert count_actionable_open_by_role(db, user)["designer"] == 1001


def test_count_zero_and_small(db: Database) -> None:
    user = {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    assert count_actionable_open_by_role(db, user)["helper"] == 0

    _seed_po(db, "PO-HQ", BRANCH_HQ)
    for i in range(3):
        _insert_task(db, key=f"h-{i}", role="helper", po_id="PO-HQ")
    assert count_actionable_open_by_role(db, user)["helper"] == 3


def test_reassignment_updates_both_actors(db: Database) -> None:
    _seed_po(db, "PO-HQ", BRANCH_HQ)
    _seed_sj(db, "SJ-1", BRANCH_HQ, accurate_po_id="PO-HQ")
    _insert_task(
        db,
        key="load-1",
        role="helper",
        task_type="loading-sj",
        po_id=None,
        sj_id="SJ-1",
        assigned_email="drva@eternal.local",
    )
    a = {
        "email_normalized": "drva@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    b = {
        "email_normalized": "drvb@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    assert count_actionable_open_by_role(db, a)["helper"] == 1
    assert count_actionable_open_by_role(db, b)["helper"] == 0

    db["operational_tasks"].update_one(
        {"task_key": "load-1"},
        {"$set": {"context.assigned_user_email": "drvb@eternal.local"}},
    )
    assert count_actionable_open_by_role(db, a)["helper"] == 0
    assert count_actionable_open_by_role(db, b)["helper"] == 1


def test_open_counts_api_and_six_roles(
    app_client: TestClient, db: Database
) -> None:
    _seed_sa(db)
    _seed_user(db, "designer@eternal.local", operational_role="designer")
    _seed_po(db, "PO-HQ", BRANCH_HQ)
    roles = [
        "production-head",
        "designer",
        "helper",
        "screener",
        "operator",
        "driver",
    ]
    for role in roles:
        _insert_task(
            db,
            key=f"role-{role}",
            role=role,
            status=TASK_STATUS_OPEN if role != "operator" else TASK_STATUS_PARTIAL,
            po_id="PO-HQ",
        )
    _insert_task(
        db, key="role-done", role="designer", status=TASK_STATUS_DONE, po_id="PO-HQ"
    )

    _login(app_client, "designer@eternal.local")
    res = app_client.get("/api/tasks/open-counts")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["truncated"] is False
    assert body["accepted_only"] is True
    assert body["source"] == "mongodb"
    counts = body["counts"]
    # Non-elevated: hanya role akun (anti spoof / info leak antar role).
    assert counts["designer"] == 1
    for role in roles:
        if role == "designer":
            continue
        assert counts[role] == 0, role
    assert sum(counts.values()) == 1

    # Super Admin tetap melihat semua role (bypass assignment).
    _login(app_client, "owner@eternal.local")
    sa = app_client.get("/api/tasks/open-counts")
    assert sa.status_code == 200, sa.text
    sa_counts = sa.json()["counts"]
    for role in roles:
        assert sa_counts[role] == 1, role
    assert sum(sa_counts.values()) == 6


def test_open_counts_requires_session(app_client: TestClient) -> None:
    assert app_client.get("/api/tasks/open-counts").status_code == 401


def test_dedupe_task_key_in_filter_path(db: Database) -> None:
    """Unique index mencegah duplikat DB; dedupe tetap dihitung di jalur filter."""
    _seed_po(db, "PO-HQ", BRANCH_HQ)
    dup = {
        "task_key": "same",
        "task_type": "cetak",
        "assigned_role": "designer",
        "status": TASK_STATUS_OPEN,
        "ref": {"accurate_po_id": "PO-HQ"},
        "context": {},
    }
    user = {
        "email_normalized": "d@eternal.local",
        "role": "user",
        "branch_ids": [BRANCH_HQ],
    }
    filtered = filter_tasks_for_actor(db, user, [dup, dict(dup)])
    assert len(filtered) == 2  # filter tidak dedupe; count path yang dedupe
    # Simulasikan stream count: seen set
    from app.services.object_auth import is_actionable_open_task

    seen: set[str] = set()
    n = 0
    for task in filtered:
        if not is_actionable_open_task(task):
            continue
        key = str(task.get("task_key") or "")
        if key in seen:
            continue
        seen.add(key)
        n += 1
    assert n == 1
