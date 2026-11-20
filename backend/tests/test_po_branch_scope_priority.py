"""P0 — prioritas identitas cabang: kanonis menang atas meta/legacy."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.auth.permissions import PERM_ACCESS_ALL_BRANCHES, PERM_OPERATIONS_FULL
from app.bootstrap import create_admin_user
from app.models.user import normalize_email
from app.repositories import org_master_repository as org
from app.repositories.po_repository import (
    aggregate_inbox_metrics,
    ensure_po_indexes,
    get_by_accurate_id,
    list_sales_orders,
)
from app.services.object_auth import (
    ObjectAuthError,
    assert_branch_access,
    object_branch_code,
)
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


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _user(
    db: Database,
    email: str,
    *,
    branch_ids: list[str],
    perms: list[str] | None = None,
) -> dict:
    doc = {
        "email_normalized": normalize_email(email),
        "display_name": email,
        "password_hash": hash_password(PASSWORD),
        "role": "user",
        "operational_role": "operator",
        "is_active": True,
        "branch_ids": branch_ids,
        "effective_module_permissions": perms or [],
    }
    db["users"].insert_one(doc)
    return doc


def _seed_master(db: Database) -> None:
    org.ensure_org_master_indexes(db)
    ensure_po_indexes(db)
    org.upsert_branch(db, code="50", name="Cabang 50", is_active=True)
    org.upsert_branch(db, code="150", name="Cabang 150", is_active=True)


def _po_canonical_50_meta_150() -> dict:
    return {
        "accurate_id": "prio-1",
        "po_no": "SO-PRIO-1",
        "origin": "accurate",
        "branch_id": "50",
        "branch_code": "YGY",  # legacy berbeda — tidak boleh bocor
        "header": {
            "customer": "Cust",
            "items": [{"qty": 2}],
            "urgent": False,
            "raw_sync_meta": {"accurate_branch_id": "150"},
        },
        "updated_at": "2026-11-20T10:00:00Z",
    }


def test_object_auth_canonical_beats_meta() -> None:
    doc = _po_canonical_50_meta_150()
    assert object_branch_code(doc) == "50"
    u50 = {
        "email_normalized": "a@x",
        "branch_ids": ["50"],
        "effective_module_permissions": [],
        "role": "user",
    }
    u150 = {
        "email_normalized": "b@x",
        "branch_ids": ["150"],
        "effective_module_permissions": [],
        "role": "user",
    }
    assert_branch_access(u50, scope_object=doc)
    import pytest

    with pytest.raises(ObjectAuthError):
        assert_branch_access(u150, scope_object=doc)


def test_list_metrics_no_stale_meta_leak(db: Database) -> None:
    _seed_master(db)
    db["accurate_sales_orders"].insert_one(_po_canonical_50_meta_150())
    # Noise: PO benar-benar cabang 150
    db["accurate_sales_orders"].insert_one(
        {
            "accurate_id": "prio-150",
            "po_no": "SO-150",
            "origin": "accurate",
            "branch_id": "150",
            "header": {"customer": "B", "items": [], "urgent": False},
            "updated_at": "2026-11-20T11:00:00Z",
        }
    )

    rows50, total50 = list_sales_orders(
        db, origin="accurate", limit=50, skip=0, branch_ids=["50"], branch_scope=True
    )
    assert total50 == 1
    assert rows50[0]["accurate_id"] == "prio-1"

    rows150, total150 = list_sales_orders(
        db, origin="accurate", limit=50, skip=0, branch_ids=["150"], branch_scope=True
    )
    assert total150 == 1
    assert rows150[0]["accurate_id"] == "prio-150"

    m50 = aggregate_inbox_metrics(db, origin="accurate", branch_ids=["50"], branch_scope=True)
    m150 = aggregate_inbox_metrics(
        db, origin="accurate", branch_ids=["150"], branch_scope=True
    )
    assert m50["total"] == 1
    assert m150["total"] == 1


def test_legacy_branch_code_does_not_leak_when_canonical_set(db: Database) -> None:
    _seed_master(db)
    db["accurate_sales_orders"].insert_one(_po_canonical_50_meta_150())
    rows, total = list_sales_orders(
        db, origin="accurate", limit=10, skip=0, branch_ids=["YGY"], branch_scope=True
    )
    assert total == 0
    assert rows == []


def test_legacy_branch_code_fallback_when_no_canonical(db: Database) -> None:
    _seed_master(db)
    db["accurate_sales_orders"].insert_one(
        {
            "accurate_id": "leg-1",
            "po_no": "SO-LEG",
            "origin": "accurate",
            "branch_code": "50",
            "header": {"customer": "C", "items": [], "urgent": False},
            "updated_at": "2026-11-20T12:00:00Z",
        }
    )
    rows, total = list_sales_orders(
        db, origin="accurate", limit=10, skip=0, branch_ids=["50"], branch_scope=True
    )
    assert total == 1
    assert rows[0]["accurate_id"] == "leg-1"


def test_missing_branch_fail_closed(db: Database) -> None:
    _seed_master(db)
    db["accurate_sales_orders"].insert_one(
        {
            "accurate_id": "none-1",
            "po_no": "SO-NONE",
            "origin": "accurate",
            "header": {"customer": "D", "items": [], "urgent": False},
            "updated_at": "2026-11-20T12:30:00Z",
        }
    )
    _, total = list_sales_orders(
        db, origin="accurate", limit=10, skip=0, branch_ids=["50"], branch_scope=True
    )
    assert total == 0
    m = aggregate_inbox_metrics(db, origin="accurate", branch_ids=["50"], branch_scope=True)
    assert m["total"] == 0


def test_api_detail_list_metrics_user150_denied(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    _seed_master(db)
    db["accurate_sales_orders"].insert_one(_po_canonical_50_meta_150())
    _user(db, "u50@eternal.local", branch_ids=["50"])
    _user(db, "u150@eternal.local", branch_ids=["150"])

    _login(app_client, "u50@eternal.local")
    listed = app_client.get("/api/po")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["accurate_id"] == "prio-1"
    detail = app_client.get("/api/po/prio-1")
    assert detail.status_code == 200
    metrics = app_client.get("/api/po/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["total"] == 1

    app_client.cookies.clear()
    _login(app_client, "u150@eternal.local")
    listed150 = app_client.get("/api/po")
    assert listed150.status_code == 200
    assert listed150.json()["total"] == 0
    assert listed150.json()["items"] == []
    detail150 = app_client.get("/api/po/prio-1")
    assert detail150.status_code == 403
    metrics150 = app_client.get("/api/po/metrics")
    assert metrics150.status_code == 200
    assert metrics150.json()["total"] == 0


def test_edit_then_reingest_stale_meta_no_leak(db: Database) -> None:
    _seed_master(db)
    so = {
        "id": "edit-1",
        "number": "SO-EDIT-1",
        "charField1": "C",
        "transDate": "20/11/2026",
        "branchId": 150,
        "detailItem": [
            {
                "id": "L1",
                "item": {"no": "CUP", "name": "Cup"},
                "quantity": 1,
                "unit": {"name": "pcs"},
            }
        ],
    }
    sync_sales_order_payload(db, so, mongo_guard="test")
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "edit-1"}, {"$set": {"branch_id": "50"}}
    )
    # Re-ingest Accurate masih 150 — kanonis tetap 50; meta boleh 150.
    sync_sales_order_payload(db, so, mongo_guard="test")
    doc = get_by_accurate_id(db, "edit-1")
    assert doc.get("branch_id") == "50"
    assert object_branch_code(doc) == "50"
    meta = (doc.get("header") or {}).get("raw_sync_meta") or {}
    assert str(meta.get("accurate_branch_id")) == "150"

    _, t50 = list_sales_orders(
        db, origin="accurate", limit=10, skip=0, branch_ids=["50"], branch_scope=True
    )
    _, t150 = list_sales_orders(
        db, origin="accurate", limit=10, skip=0, branch_ids=["150"], branch_scope=True
    )
    assert t50 == 1
    assert t150 == 0


def test_elevated_contracts_unchanged(db: Database) -> None:
    _seed_master(db)
    db["accurate_sales_orders"].insert_one(_po_canonical_50_meta_150())
    ops = {
        "email_normalized": "ops@x",
        "branch_ids": [],
        "effective_module_permissions": [PERM_OPERATIONS_FULL],
        "role": "user",
    }
    ab = {
        "email_normalized": "ab@x",
        "branch_ids": [],
        "effective_module_permissions": [PERM_ACCESS_ALL_BRANCHES],
        "role": "user",
    }
    assert_branch_access(ops, scope_object=_po_canonical_50_meta_150())
    assert_branch_access(ab, scope_object=_po_canonical_50_meta_150())
    # List tanpa branch_scope (elevated path) melihat semua
    rows, total = list_sales_orders(db, origin="accurate", limit=50, skip=0)
    assert total == 1
    assert len(rows) == 1
