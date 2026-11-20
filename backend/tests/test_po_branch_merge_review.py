"""Negative tests FINAL MERGE REVIEW — otorisasi edit/accept, pagination, KPI."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.auth.permissions import (
    PERM_ACCESS_ALL_BRANCHES,
    PERM_EDIT_PO_BRANCH,
    PERM_OPERATIONS_FULL,
)
from app.bootstrap import create_admin_user
from app.models.user import normalize_email
from app.repositories import org_master_repository as org
from app.repositories.po_repository import ensure_po_indexes, get_by_accurate_id
from app.services.object_auth import ObjectAuthError, assert_branch_access
from app.services.po_acceptance import accept_po
from app.services.po_branch_service import PoBranchConflictError, update_po_branch
from app.services.po_sync import sync_sales_order_payload
from app.services.task_engine import TaskPermissionError


PASSWORD = "password-kuat-123"
OWNER = "owner@eternal.local"


def _seed_owner(db: Database) -> None:
    create_admin_user(
        db,
        email=OWNER,
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _login(client: TestClient, email: str = OWNER) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _so(aid: str, branch_id: int) -> dict:
    return {
        "id": aid,
        "number": f"SO-{aid}",
        "charField1": "C",
        "transDate": "20/11/2026",
        "branchId": branch_id,
        "detailItem": [
            {
                "id": f"L-{aid}",
                "item": {"no": "CUP", "name": "Cup"},
                "quantity": 1,
                "unit": {"name": "pcs"},
            }
        ],
    }


def _seed_branches(db: Database) -> None:
    org.ensure_org_master_indexes(db)
    ensure_po_indexes(db)
    org.upsert_branch(db, code="50", name="Cabang 50", is_active=True)
    org.upsert_branch(db, code="150", name="Cabang 150", is_active=True)


def _user(
    db: Database,
    email: str,
    *,
    branch_ids: list[str],
    perms: list[str],
    operational_role: str | None = "operator",
) -> dict:
    doc = {
        "email_normalized": normalize_email(email),
        "display_name": email,
        "password_hash": hash_password(PASSWORD),
        "role": "user",
        "operational_role": operational_role,
        "is_active": True,
        "branch_ids": branch_ids,
        "effective_module_permissions": perms,
    }
    db["users"].insert_one(doc)
    return doc


def test_user50_with_edit_cannot_touch_po150(db: Database) -> None:
    import pytest

    _seed_branches(db)
    sync_sales_order_payload(db, _so("m1", 150), mongo_guard="test")
    u = _user(db, "u50e@eternal.local", branch_ids=["50"], perms=[PERM_EDIT_PO_BRANCH])
    with pytest.raises(ObjectAuthError):
        update_po_branch(
            db, u, accurate_po_id="m1", branch_id="50", reason="coba"
        )


def test_user50_cannot_move_own_po_to_150(db: Database) -> None:
    import pytest

    _seed_branches(db)
    sync_sales_order_payload(db, _so("m2", 50), mongo_guard="test")
    u = _user(db, "u50m@eternal.local", branch_ids=["50"], perms=[PERM_EDIT_PO_BRANCH])
    with pytest.raises(ObjectAuthError):
        update_po_branch(
            db, u, accurate_po_id="m2", branch_id="150", reason="pindah"
        )


def test_all_branches_plus_edit_can_cross(db: Database) -> None:
    _seed_branches(db)
    sync_sales_order_payload(db, _so("m3", 150), mongo_guard="test")
    u = _user(
        db,
        "abedit@eternal.local",
        branch_ids=[],
        perms=[PERM_ACCESS_ALL_BRANCHES, PERM_EDIT_PO_BRANCH],
    )
    res = update_po_branch(
        db, u, accurate_po_id="m3", branch_id="50", reason="koreksi lintas"
    )
    assert res["after"] == "50"


def test_all_branches_without_edit_denied(db: Database) -> None:
    import pytest

    _seed_branches(db)
    sync_sales_order_payload(db, _so("m4", 150), mongo_guard="test")
    u = _user(
        db,
        "abonly@eternal.local",
        branch_ids=[],
        perms=[PERM_ACCESS_ALL_BRANCHES],
    )
    with pytest.raises(TaskPermissionError):
        update_po_branch(
            db, u, accurate_po_id="m4", branch_id="50", reason="no grant"
        )


def test_kp50_cannot_accept_po150(db: Database) -> None:
    import pytest

    _seed_branches(db)
    sync_sales_order_payload(db, _so("m5", 150), mongo_guard="test")
    kp = _user(
        db,
        "kp50@eternal.local",
        branch_ids=["50"],
        perms=[],
        operational_role="production-head",
    )
    with pytest.raises(ObjectAuthError):
        accept_po(db, kp, accurate_po_id="m5")


def test_ops_full_accept_cross_branch(db: Database) -> None:
    _seed_branches(db)
    sync_sales_order_payload(db, _so("m6", 150), mongo_guard="test")
    ops = _user(
        db,
        "ops@eternal.local",
        branch_ids=[],
        perms=[PERM_OPERATIONS_FULL],
        operational_role="helper",
    )
    res = accept_po(db, ops, accurate_po_id="m6")
    assert res["duplicate"] is False


def test_edit_with_allocation_conflicts(db: Database) -> None:
    import pytest

    _seed_branches(db)
    sync_sales_order_payload(db, _so("m7", 50), mongo_guard="test")
    db["production_allocations"].insert_one(
        {"accurate_po_id": "m7", "allocation_id": "A1", "branch_id": "50"}
    )
    u = _user(
        db,
        "ed@eternal.local",
        branch_ids=["50", "150"],
        perms=[PERM_EDIT_PO_BRANCH],
    )
    with pytest.raises(PoBranchConflictError):
        update_po_branch(
            db, u, accurate_po_id="m7", branch_id="150", reason="pindah dengan alokasi"
        )


def test_pagination_scoped_over_500(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_branches(db)
    # 520 PO cabang 50 + 30 cabang 150
    coll = db["accurate_sales_orders"]
    docs = []
    for i in range(520):
        docs.append(
            {
                "accurate_id": f"p50-{i}",
                "po_no": f"SO50-{i}",
                "origin": "accurate",
                "branch_id": "50",
                "header": {"customer": "A", "items": [], "urgent": False},
                "updated_at": f"2026-11-20T00:00:{i % 60:02d}Z",
            }
        )
    for i in range(30):
        docs.append(
            {
                "accurate_id": f"p150-{i}",
                "po_no": f"SO150-{i}",
                "origin": "accurate",
                "branch_id": "150",
                "header": {"customer": "B", "items": [], "urgent": False},
                "updated_at": f"2026-11-20T01:00:{i % 60:02d}Z",
            }
        )
    coll.insert_many(docs)
    _user(db, "page50@eternal.local", branch_ids=["50"], perms=[])
    _login(app_client, "page50@eternal.local")
    page1 = app_client.get("/api/po?page=1&pageSize=100")
    assert page1.status_code == 200, page1.text
    body = page1.json()
    assert body["total"] == 520
    assert len(body["items"]) == 100
    assert all(it.get("branch_id") == "50" for it in body["items"])
    page6 = app_client.get("/api/po?page=6&pageSize=100")
    assert page6.status_code == 200
    assert len(page6.json()["items"]) == 20
    assert all(it.get("branch_id") == "50" for it in page6.json()["items"])


def test_metrics_no_cross_branch_leak(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_branches(db)
    for i in range(10):
        sync_sales_order_payload(db, _so(f"k50-{i}", 50), mongo_guard="test")
    for i in range(7):
        sync_sales_order_payload(db, _so(f"k150-{i}", 150), mongo_guard="test")
    _user(db, "kpi50@eternal.local", branch_ids=["50"], perms=[])
    _login(app_client, "kpi50@eternal.local")
    res = app_client.get("/api/po/metrics")
    assert res.status_code == 200
    assert res.json()["total"] == 10


def test_api_patch_denied_without_perm(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_branches(db)
    sync_sales_order_payload(db, _so("api1", 50), mongo_guard="test")
    _user(db, "nop@eternal.local", branch_ids=["50"], perms=[])
    csrf = _login(app_client, "nop@eternal.local")
    denied = app_client.patch(
        "/api/po/api1/branch",
        json={"branch_id": "150", "reason": "x"},
        headers={"X-CSRF-Token": csrf},
    )
    assert denied.status_code == 403
