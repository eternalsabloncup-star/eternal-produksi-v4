"""Kontrak Accurate branchId kanonis — auth, ingest, edit."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.models.user import normalize_email
from app.repositories import org_master_repository as org
from app.repositories.po_repository import get_by_accurate_id
from app.services.audit_log import list_audit_events
from app.services.object_auth import ObjectAuthError, assert_branch_access
from app.services.po_sync import sync_sales_order_payload


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


def _so(accurate_id: str, branch_id: int | None) -> dict:
    payload: dict = {
        "id": accurate_id,
        "number": f"SO-{accurate_id}",
        "charField1": "Cust",
        "transDate": "20/11/2026",
        "detailItem": [
            {
                "id": f"L-{accurate_id}",
                "item": {"no": "CUP", "name": "Cup"},
                "quantity": 1,
                "unit": {"name": "pcs"},
            }
        ],
    }
    if branch_id is not None:
        payload["branchId"] = branch_id
    return payload


def test_new_po_gets_accurate_branch_id_without_mapping(db: Database) -> None:
    from app.repositories.po_repository import ensure_po_indexes
    from app.services.object_auth import object_branch_code

    ensure_po_indexes(db)
    sync_sales_order_payload(db, _so("8001", 150), mongo_guard="test")
    doc = get_by_accurate_id(db, "8001")
    assert doc is not None
    assert doc.get("branch_id") == "150"
    assert not doc.get("branch_code")
    assert object_branch_code(doc) == "150"


def test_user_50_denied_po_150(db: Database) -> None:
    import pytest
    from app.repositories.po_repository import ensure_po_indexes

    org.upsert_branch(db, code="50", name="Cabang 50 (belum diverifikasi)", is_active=True)
    org.upsert_branch(db, code="150", name="Cabang 150 (belum diverifikasi)", is_active=True)
    ensure_po_indexes(db)
    sync_sales_order_payload(db, _so("8002", 150), mongo_guard="test")
    doc = get_by_accurate_id(db, "8002")
    user50 = {
        "email_normalized": "u50@eternal.local",
        "role": "user",
        "branch_ids": ["50"],
        "effective_module_permissions": [],
    }
    with pytest.raises(ObjectAuthError):
        assert_branch_access(user50, scope_object=doc)


def test_multi_branch_user_can_access_allowed(db: Database) -> None:
    from app.repositories.po_repository import ensure_po_indexes

    ensure_po_indexes(db)
    sync_sales_order_payload(db, _so("8003", 50), mongo_guard="test")
    doc = get_by_accurate_id(db, "8003")
    user = {
        "email_normalized": "multi@eternal.local",
        "role": "user",
        "branch_ids": ["50", "150"],
        "effective_module_permissions": [],
    }
    assert_branch_access(user, scope_object=doc)


def test_missing_branch_fail_closed(db: Database) -> None:
    from app.repositories.po_repository import ensure_po_indexes

    ensure_po_indexes(db)
    sync_sales_order_payload(db, _so("8004", None), mongo_guard="test")
    doc = get_by_accurate_id(db, "8004")
    user = {
        "email_normalized": "u@eternal.local",
        "role": "user",
        "branch_ids": ["50"],
        "effective_module_permissions": [],
    }
    import pytest

    with pytest.raises(ObjectAuthError):
        assert_branch_access(user, scope_object=doc)


def test_ops_full_cross_branch(db: Database) -> None:
    from app.repositories.po_repository import ensure_po_indexes
    from app.auth.permissions import PERM_OPERATIONS_FULL

    ensure_po_indexes(db)
    sync_sales_order_payload(db, _so("8005", 150), mongo_guard="test")
    doc = get_by_accurate_id(db, "8005")
    ops = {
        "email_normalized": "ops@eternal.local",
        "role": "user",
        "branch_ids": [],
        "effective_module_permissions": [PERM_OPERATIONS_FULL],
    }
    assert_branch_access(ops, scope_object=doc)


def test_all_branches_read_ok_no_edit_without_perm(
    app_client: TestClient, db: Database
) -> None:
    from app.auth.permissions import PERM_ACCESS_ALL_BRANCHES
    from app.repositories.po_repository import ensure_po_indexes

    _seed_owner(db)
    ensure_po_indexes(db)
    org.upsert_branch(db, code="50", name="B50", is_active=True)
    org.upsert_branch(db, code="150", name="B150", is_active=True)
    sync_sales_order_payload(db, _so("8006", 150), mongo_guard="test")
    db["users"].insert_one(
        {
            "email_normalized": normalize_email("ab@eternal.local"),
            "display_name": "All Branches",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "is_active": True,
            "branch_ids": [],
            "effective_module_permissions": [PERM_ACCESS_ALL_BRANCHES],
        }
    )
    csrf = _login(app_client, "ab@eternal.local")
    ok = app_client.get("/api/po/8006")
    assert ok.status_code == 200, ok.text
    denied = app_client.patch(
        "/api/po/8006/branch",
        json={"branch_id": "50", "reason": "move"},
        headers={"X-CSRF-Token": csrf},
    )
    assert denied.status_code == 403


def test_edit_branch_audited(app_client: TestClient, db: Database) -> None:
    from app.auth.permissions import PERM_EDIT_PO_BRANCH
    from app.repositories.po_repository import ensure_po_indexes

    _seed_owner(db)
    # Owner SA already has all perms including po.branch.edit via ALL_PERMISSIONS
    ensure_po_indexes(db)
    org.upsert_branch(db, code="50", name="B50", is_active=True)
    org.upsert_branch(db, code="150", name="B150", is_active=True)
    sync_sales_order_payload(db, _so("8007", 150), mongo_guard="test")
    csrf = _login(app_client)
    # Ensure SA effective includes new perm (super_admin bypasses has_permission)
    res = app_client.patch(
        "/api/po/8007/branch",
        json={"branch_id": "50", "reason": "koreksi Owner"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    assert res.json()["after"] == "50"
    doc = get_by_accurate_id(db, "8007")
    assert doc.get("branch_id") == "50"
    events = list_audit_events(db, action="po.branch.update", limit=5)["items"]
    assert events
    assert (events[0].get("detail") or {}).get("after", {}).get("branch_id") == "50"


def test_reingest_preserves_manual_branch(db: Database) -> None:
    from app.repositories.po_repository import ensure_po_indexes

    ensure_po_indexes(db)
    sync_sales_order_payload(db, _so("8008", 150), mongo_guard="test")
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "8008"}, {"$set": {"branch_id": "50"}}
    )
    sync_sales_order_payload(db, _so("8008", 150), mongo_guard="test")
    doc = get_by_accurate_id(db, "8008")
    assert doc.get("branch_id") == "50"


def test_dry_run_categories_stable() -> None:
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "dry", root / "scripts" / "po_branch_backfill_dry_run.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    a = mod.classify_po(
        {"accurate_id": "1", "header": {"raw_sync_meta": {"accurate_branch_id": "150"}}},
        master_ids={"50", "150"},
    )
    b = mod.classify_po(
        {"accurate_id": "1", "header": {"raw_sync_meta": {"accurate_branch_id": "150"}}},
        master_ids={"50", "150"},
    )
    assert a == b
    assert a["category"] == "READY_TO_BACKFILL"
