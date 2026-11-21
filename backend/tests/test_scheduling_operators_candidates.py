"""Kandidat Operator penjadwalan — kontrak Master Pengguna."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import gate_repository as gates
from app.repositories import settings_user_repository as users
from app.repositories.po_repository import upsert_sales_order

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_A = "106318"


def _seed_base(db: Database) -> None:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted

    mark_po_accepted(db)
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    db["users"].insert_one(
        {
            "email_normalized": normalize_email("kp@eternal.local"),
            "display_name": "Kepala Produksi",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": "production-head",
            "is_active": True,
            "effective_module_permissions": ["access.all_branches"],
        }
    )


def _insert_user(
    db: Database,
    *,
    email: str,
    display_name: str,
    operational_role: str | None,
    is_active: bool = True,
    role: str = "user",
) -> None:
    db["users"].insert_one(
        {
            "email_normalized": normalize_email(email),
            "display_name": display_name,
            "password_hash": hash_password(PASSWORD),
            "role": role,
            "operational_role": operational_role,
            "is_active": is_active,
            "effective_module_permissions": ["access.all_branches"],
        }
    )


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _open_gates(db: Database) -> None:
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji"
        )


def test_list_active_operators_filters_and_fallback(db: Database) -> None:
    _seed_base(db)
    _insert_user(
        db,
        email="op.ok@eternal.local",
        display_name="Budi Operator",
        operational_role="operator",
    )
    _insert_user(
        db,
        email="op.cased@eternal.local",
        display_name="Casing Op",
        operational_role="Operator",
    )
    _insert_user(
        db,
        email="op.noname@eternal.local",
        display_name="",
        operational_role="operator",
    )
    _insert_user(
        db,
        email="op.off@eternal.local",
        display_name="Nonaktif",
        operational_role="operator",
        is_active=False,
    )
    _insert_user(
        db,
        email="helper@eternal.local",
        display_name="Helper Aktif",
        operational_role="helper",
    )
    _insert_user(
        db,
        email="roleonly@eternal.local",
        display_name="Role Label Operator",
        operational_role=None,
        role="operator",
    )

    items = users.list_active_operators(db)
    emails = {i["email"] for i in items}
    assert "op.ok@eternal.local" in emails
    assert "op.cased@eternal.local" in emails
    assert "op.noname@eternal.local" in emails
    assert "op.off@eternal.local" not in emails
    assert "helper@eternal.local" not in emails
    assert "roleonly@eternal.local" not in emails

    noname = next(i for i in items if i["email"] == "op.noname@eternal.local")
    assert noname["display_name"] == "op.noname@eternal.local"
    assert noname["id"]


def test_operators_endpoint_ok_denied_and_empty(
    app_client: TestClient, db: Database
) -> None:
    _seed_base(db)
    _insert_user(
        db,
        email="op.ok@eternal.local",
        display_name="Budi Operator",
        operational_role="operator",
    )
    _login(app_client, "kp@eternal.local")
    ok = app_client.get("/api/scheduling/operators")
    assert ok.status_code == 200, ok.text
    assert any(i["email"] == "op.ok@eternal.local" for i in ok.json()["items"])

    app_client.post("/api/auth/logout")
    _insert_user(
        db,
        email="helper.only@eternal.local",
        display_name="Helper Only",
        operational_role="helper",
    )
    _login(app_client, "helper.only@eternal.local")
    denied = app_client.get("/api/scheduling/operators")
    assert denied.status_code == 403

    # Hapus semua operator → KP melihat empty list (bukan 403).
    db["users"].delete_many({"operational_role": {"$regex": "^operator$", "$options": "i"}})
    _login(app_client, "kp@eternal.local")
    empty = app_client.get("/api/scheduling/operators")
    assert empty.status_code == 200
    assert empty.json()["items"] == []


def test_create_allocation_requires_active_operator(
    app_client: TestClient, db: Database
) -> None:
    _seed_base(db)
    _open_gates(db)
    _insert_user(
        db,
        email="op.ok@eternal.local",
        display_name="Budi Operator",
        operational_role="operator",
    )
    _insert_user(
        db,
        email="helper@eternal.local",
        display_name="Helper",
        operational_role="helper",
    )
    csrf = _login(app_client, "kp@eternal.local")

    bad = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "helper@eternal.local", "qty": 50}],
            "idempotency_key": "alloc-reject-helper",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 422, bad.text
    assert "Operator" in bad.text

    _insert_user(
        db,
        email="op.off@eternal.local",
        display_name="Op Off",
        operational_role="operator",
        is_active=False,
    )
    inactive = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op.off@eternal.local", "qty": 50}],
            "idempotency_key": "alloc-reject-inactive",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert inactive.status_code == 422, inactive.text

    ok = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [
                {
                    "operator_email": "op.ok@eternal.local",
                    "qty": 50,
                    "print_color": "Hitam",
                    "screen_kind": "Besar",
                }
            ],
            "idempotency_key": "alloc-accept-op",
            "schedule_date": "2026-11-22",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["allocations"][0]["operator_email"] == "op.ok@eternal.local"


def test_create_allocation_allows_operator_without_display_name(
    app_client: TestClient, db: Database
) -> None:
    _seed_base(db)
    _open_gates(db)
    _insert_user(
        db,
        email="op.noname@eternal.local",
        display_name="",
        operational_role="operator",
    )
    csrf = _login(app_client, "kp@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [
                {
                    "operator_email": "op.noname@eternal.local",
                    "qty": 40,
                    "print_color": "Merah",
                    "screen_kind": "Kecil",
                }
            ],
            "idempotency_key": "alloc-noname-ok",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    state = app_client.get(f"/api/scheduling/{PO_ID}/lines/{LINE_A}")
    assert state.status_code == 200
    row = state.json()["allocations"][0]
    assert row["operator_email"] == "op.noname@eternal.local"
    assert row["operator_display_name"] == "op.noname@eternal.local"
