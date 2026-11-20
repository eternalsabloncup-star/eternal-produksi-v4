"""Preview konten bukti sampel & pengiriman — scope guard."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import production_service as prod
from app.services import scheduling_service

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_A = "106318"
PNG = b"\x89PNG\r\n\x1a\ncontent-sample"


def _seed(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted

    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji"
        )
    master.seed_master_defaults(db)
    return get_by_accurate_id(db, PO_ID)


def _users(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    for email, role, op in (
        ("op@eternal.local", "user", "operator"),
        ("kp@eternal.local", "user", "production-head"),
        ("helper@eternal.local", "user", "helper"),
    ):
        db["users"].insert_one(
            {
                "email_normalized": normalize_email(email),
                "display_name": email,
                "password_hash": hash_password(PASSWORD),
                "role": role,
                "operational_role": op,
                "is_active": True,
                "effective_module_permissions": ["access.all_branches"]
            }
        )


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def test_sample_evidence_content_scoped(app_client: TestClient, db: Database) -> None:
    po = _seed(db)
    _users(db)
    head = {
        "email_normalized": "kp@eternal.local",
        "role": "user",
        "operational_role": "production-head",
        "effective_module_permissions": ["access.all_branches"],
}
    op = {
        "email_normalized": "op@eternal.local",
        "role": "user",
        "operational_role": "operator",
        "effective_module_permissions": ["access.all_branches"],
}
    created = scheduling_service.create_allocations(
        db,
        head,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op@eternal.local", "qty": 400}],
        idempotency_key="evid-sample-1",
    )
    alloc = created["allocations"][0]["allocation_id"]
    # Screen + prep agar upload lolos
    from app.domain.operational import TaskRef, build_task_key
    from app.repositories import task_repository as tasks

    ref = TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc)
    tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A),
        qty_target=1000,
        label="Persiapan",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key(
            "persiapan-cup",
            "po_line",
            TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A),
        ),
        qty=400,
        actor_email="helper@eternal.local",
        actor_role="helper",
        idempotency_key="prep-evid-1",
    )
    tasks.upsert_task(
        db,
        task_type="screen",
        scope="po_line_allocation",
        assigned_role="screener",
        ref=ref,
        qty_target=400,
        label="Screen",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("screen", "po_line_allocation", ref),
        qty=10,
        actor_email="sc@eternal.local",
        actor_role="screener",
        idempotency_key="screen-evid-1",
    )
    uploaded = prod.submit_sample(
        db,
        op,
        allocation_id=alloc,
        filename="s1.png",
        content_type="image/png",
        content=PNG,
    )
    evid = uploaded["evidence"]["evidence_id"]

    _login(app_client, "op@eternal.local")
    ok = app_client.get(
        f"/api/production/allocations/{alloc}/samples/evidence/{evid}/content"
    )
    assert ok.status_code == 200, ok.text
    assert ok.content.startswith(b"\x89PNG")

    cross = app_client.get(
        f"/api/production/allocations/{alloc}/samples/evidence/evid:fake/content"
    )
    assert cross.status_code == 404

    # Helper tanpa view sampel → 403
    _login(app_client, "helper@eternal.local")
    denied = app_client.get(
        f"/api/production/allocations/{alloc}/samples/evidence/{evid}/content"
    )
    assert denied.status_code == 403


def test_sample_reject_sets_sampel_ulang_label(db: Database) -> None:
    from app.repositories import sample_repository as samples
    from app.repositories import task_repository as tasks

    po = _seed(db)
    head = {
        "email_normalized": "kp@eternal.local",
        "role": "user",
        "operational_role": "production-head",
        "effective_module_permissions": ["access.all_branches"],
}
    op = {
        "email_normalized": "op@eternal.local",
        "role": "user",
        "operational_role": "operator",
        "effective_module_permissions": ["access.all_branches"],
}
    created = scheduling_service.create_allocations(
        db,
        head,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op@eternal.local", "qty": 200}],
        idempotency_key="sampel-ulang-1",
    )
    alloc = created["allocations"][0]["allocation_id"]
    from app.domain.operational import TaskRef, build_task_key

    ref = TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc)
    tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A),
        qty_target=1000,
        label="Persiapan",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key(
            "persiapan-cup",
            "po_line",
            TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A),
        ),
        qty=200,
        actor_email="h@eternal.local",
        actor_role="helper",
        idempotency_key="prep-ulang-1",
    )
    tasks.upsert_task(
        db,
        task_type="screen",
        scope="po_line_allocation",
        assigned_role="screener",
        ref=ref,
        qty_target=200,
        label="Screen",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("screen", "po_line_allocation", ref),
        qty=5,
        actor_email="sc@eternal.local",
        actor_role="screener",
        idempotency_key="screen-ulang-1",
    )
    from tests.sample_flow_helpers import draft_and_confirm

    submitted = draft_and_confirm(db, op, alloc, filename="a.png")
    prod.decide_sample(
        db,
        head,
        allocation_id=alloc,
        version=int(submitted["version"]),
        approved=False,
        reason="warna salah",
    )
    sample_task = next(
        t
        for t in tasks.list_tasks(db, task_type="konfirmasi-sample")
        if (t.get("ref") or {}).get("allocation_id") == alloc
    )
    assert sample_task["label"] == "Sampel Ulang"
    assert sample_task["context"].get("display_kind") == "sampel-ulang"
    assert sample_task["status"] == tasks.TASK_STATUS_OPEN
    assert samples.list_samples(db, alloc)[0]["status"] == samples.STATUS_REJECTED


def test_sample_evidence_assignment_and_branch(app_client: TestClient, db: Database) -> None:
    """Operator lain / cabang salah → 403; KP & super_admin → 200."""
    po = _seed(db)
    _users(db)
    db["users"].insert_one(
        {
            "email_normalized": normalize_email("op2@eternal.local"),
            "display_name": "Op2",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": "operator",
            "is_active": True,
            "branch_ids": ["JKT"],
        }
    )
    db["users"].update_one(
        {"email_normalized": normalize_email("op@eternal.local")},
        {"$set": {"branch_ids": []}},
    )
    head = {
        "email_normalized": "kp@eternal.local",
        "role": "user",
        "operational_role": "production-head",
        "effective_module_permissions": ["access.all_branches"]
    }
    op = {
        "email_normalized": "op@eternal.local",
        "role": "user",
        "operational_role": "operator",
        "effective_module_permissions": ["access.all_branches"],
}
    created = scheduling_service.create_allocations(
        db,
        head,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op@eternal.local", "qty": 400}],
        idempotency_key="evid-assign-1",
    )
    alloc = created["allocations"][0]["allocation_id"]
    from app.domain.operational import TaskRef, build_task_key
    from app.repositories import task_repository as tasks

    ref = TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc)
    tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A),
        qty_target=1000,
        label="Persiapan",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key(
            "persiapan-cup",
            "po_line",
            TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A),
        ),
        qty=400,
        actor_email="helper@eternal.local",
        actor_role="helper",
        idempotency_key="prep-assign-1",
    )
    tasks.upsert_task(
        db,
        task_type="screen",
        scope="po_line_allocation",
        assigned_role="screener",
        ref=ref,
        qty_target=400,
        label="Screen",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("screen", "po_line_allocation", ref),
        qty=10,
        actor_email="sc@eternal.local",
        actor_role="screener",
        idempotency_key="screen-assign-1",
    )
    uploaded = prod.submit_sample(
        db,
        op,
        allocation_id=alloc,
        filename="s1.png",
        content_type="image/png",
        content=PNG,
    )
    evid = uploaded["evidence"]["evidence_id"]
    path = f"/api/production/allocations/{alloc}/samples/evidence/{evid}/content"

    _login(app_client, "op@eternal.local")
    assert app_client.get(path).status_code == 200

    _login(app_client, "op2@eternal.local")
    # Branch-scoped user + object tanpa cabang terbukti → fail closed 403
    assert app_client.get(path).status_code == 403

    _login(app_client, "kp@eternal.local")
    assert app_client.get(path).status_code == 200

    _login(app_client, "owner@eternal.local")
    assert app_client.get(path).status_code == 200
