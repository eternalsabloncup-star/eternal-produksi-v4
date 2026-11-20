"""Resolver Accurate→Master + mapping API — fail-closed, no invent codes."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.models.user import normalize_email
from app.repositories import org_master_repository as org
from app.repositories.po_repository import get_by_accurate_id
from app.services.audit_log import list_audit_events
from app.services.branch_mapping import (
    BranchResolveStatus,
    apply_branch_code_on_ingest,
    resolve_eternal_branch_code,
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


def _login(client: TestClient, email: str = OWNER) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _so_payload(accurate_id: str, *, branch_id: int | None = 150) -> dict:
    payload = {
        "id": accurate_id,
        "number": f"SO-{accurate_id}",
        "charField1": "Cust",
        "transDate": "20/11/2026",
        "detailItem": [
            {
                "id": f"L-{accurate_id}",
                "item": {"no": "CUP", "name": "Cup"},
                "quantity": 10,
                "unit": {"name": "pcs"},
            }
        ],
    }
    if branch_id is not None:
        payload["branchId"] = branch_id
    return payload


def test_resolve_valid_mapping(db: Database) -> None:
    org.upsert_branch(db, code="YGY", name="Yogyakarta", is_active=True)
    org.set_branch_accurate_mapping(db, "YGY", accurate_branch_id="150")
    r = resolve_eternal_branch_code(db, "150")
    assert r.status == BranchResolveStatus.RESOLVED
    assert r.branch_code == "YGY"


def test_resolve_unmapped_does_not_invent(db: Database) -> None:
    r = resolve_eternal_branch_code(db, "50")
    assert r.status == BranchResolveStatus.UNMAPPED
    assert r.branch_code is None


def test_resolve_inactive_master(db: Database) -> None:
    org.upsert_branch(db, code="SMG", name="Semarang", is_active=True)
    org.set_branch_accurate_mapping(db, "SMG", accurate_branch_id="50")
    org.set_branch_active(db, "SMG", is_active=False)
    r = resolve_eternal_branch_code(db, "50")
    assert r.status == BranchResolveStatus.INACTIVE_MASTER


def test_duplicate_mapping_rejected(db: Database) -> None:
    org.upsert_branch(db, code="HQ", name="HQ", is_active=True)
    org.upsert_branch(db, code="YGY", name="YGY", is_active=True)
    org.set_branch_accurate_mapping(db, "HQ", accurate_branch_id="150")
    try:
        org.set_branch_accurate_mapping(db, "YGY", accurate_branch_id="150")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "sudah dipetakan" in str(exc)


def test_invalid_branch_id_rejected(db: Database) -> None:
    org.upsert_branch(db, code="HQ", name="HQ", is_active=True)
    try:
        org.set_branch_accurate_mapping(db, "HQ", accurate_branch_id="SMG")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "numerik" in str(exc).lower()


def test_mapping_api_audit_and_permission(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    csrf = _login(app_client)
    assert (
        app_client.put(
            "/api/settings/branches",
            json={"code": "YGY", "name": "Yogyakarta"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 200
    )
    mapped = app_client.put(
        "/api/settings/branches/YGY/accurate-mapping",
        json={"accurate_branch_id": "150", "reason": "probe Accurate SO"},
        headers={"X-CSRF-Token": csrf},
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    assert body["accurate_branch_id"] == "150"
    assert body["mapping_status"] == "mapped"
    assert body["accurate_verified"] is False

    events = list_audit_events(db, module="settings", limit=30)["items"]
    assert any(
        e.get("action") == "settings.branch.accurate_mapping"
        and (e.get("detail") or {}).get("after", {}).get("accurate_branch_id") == "150"
        for e in events
    )

    # Duplikat aktif → 409
    assert (
        app_client.put(
            "/api/settings/branches",
            json={"code": "HQ", "name": "HQ"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 200
    )
    conflict = app_client.put(
        "/api/settings/branches/HQ/accurate-mapping",
        json={"accurate_branch_id": "150", "reason": "dup"},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409

    # Tanpa settings.manage
    db["users"].insert_one(
        {
            "email_normalized": normalize_email("plain@eternal.local"),
            "display_name": "Plain",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": "helper",
            "is_active": True,
            "branch_ids": ["YGY"],
            "effective_module_permissions": [],
        }
    )
    plain = _login(app_client, "plain@eternal.local")
    denied = app_client.put(
        "/api/settings/branches/YGY/accurate-mapping",
        json={"accurate_branch_id": "50", "reason": "nope"},
        headers={"X-CSRF-Token": plain},
    )
    assert denied.status_code == 403


def test_new_po_follows_mapping_historical_preserved(
    app_client: TestClient, db: Database
) -> None:
    del app_client
    org.ensure_org_master_indexes(db)
    from app.repositories.po_repository import ensure_po_indexes

    ensure_po_indexes(db)
    org.upsert_branch(db, code="YGY", name="Yogyakarta", is_active=True)
    org.set_branch_accurate_mapping(db, "YGY", accurate_branch_id="150")

    # Insert baru → branch_code dari mapping
    sync_sales_order_payload(db, _so_payload("9001", branch_id=150), mongo_guard="test")
    doc = get_by_accurate_id(db, "9001")
    assert doc is not None
    assert doc.get("branch_code") == "YGY"

    # Re-ingest setelah mapping diganti → cabang historis tidak digeser
    org.upsert_branch(db, code="SMG", name="Semarang", is_active=True)
    org.set_branch_accurate_mapping(db, "YGY", accurate_branch_id=None)
    org.set_branch_accurate_mapping(db, "SMG", accurate_branch_id="150")
    sync_sales_order_payload(db, _so_payload("9001", branch_id=150), mongo_guard="test")
    doc2 = get_by_accurate_id(db, "9001")
    assert doc2 is not None
    assert doc2.get("branch_code") == "YGY"

    # PO tanpa mapping tetap tanpa invent branch_code
    sync_sales_order_payload(db, _so_payload("9002", branch_id=50), mongo_guard="test")
    doc3 = get_by_accurate_id(db, "9002")
    assert doc3 is not None
    assert not doc3.get("branch_code")
    meta = (doc3.get("header") or {}).get("raw_sync_meta") or {}
    assert meta.get("accurate_branch_id") == "50"


def test_apply_preserves_existing() -> None:
    decision = apply_branch_code_on_ingest(
        None,  # type: ignore[arg-type]
        existing_doc={"branch_code": "HQ"},
        accurate_branch_id="150",
    )
    assert decision["action"] == "preserve_existing"
    assert decision["branch_code"] == "HQ"


def test_normalize_still_does_not_invent_branch() -> None:
    header = normalize_sales_order(_so_payload("1", branch_id=150))
    data = header.model_dump()
    assert not data.get("branch_code")
    assert data["raw_sync_meta"]["accurate_branch_id"] == "150"
