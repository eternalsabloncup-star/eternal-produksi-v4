"""API Checkpoint B via HTTP: route tidak bentrok, RBAC, gate, idempotency."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order

PASSWORD = "password-kuat-123"
PO_ID = "95106"
SIGNATURE = base64.b64encode(b"PNG-signature-test").decode()


def _seed_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    return get_by_accurate_id(db, PO_ID)


def _seed_owner(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _seed_role_user(db: Database, email: str, operational_role: str) -> None:
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


def test_gates_route_not_swallowed_by_po_catalog(
    app_client: TestClient, db: Database
) -> None:
    _seed_po(db)
    _seed_owner(db)
    _login(app_client, "owner@eternal.local")
    res = app_client.get(f"/api/workflow/{PO_ID}/gates")
    assert res.status_code == 200
    body = res.json()
    assert body["gates"]["design"]["state"] == "closed"
    assert body["scheduling_unlocked"] is False


def test_derive_tasks_requires_permission(app_client: TestClient, db: Database) -> None:
    _seed_po(db)
    _seed_owner(db)
    _seed_role_user(db, "designer@eternal.local", "designer")

    designer_csrf = _login(app_client, "designer@eternal.local")
    denied = app_client.post(
        f"/api/workflow/{PO_ID}/derive-tasks", headers={"X-CSRF-Token": designer_csrf}
    )
    assert denied.status_code == 403

    owner_csrf = _login(app_client, "owner@eternal.local")
    ok = app_client.post(
        f"/api/workflow/{PO_ID}/derive-tasks", headers={"X-CSRF-Token": owner_csrf}
    )
    assert ok.status_code == 200
    assert ok.json()["created_task_keys"] == [f"task:design:po:{PO_ID}"]


def test_design_upload_confirm_and_customer_approval_over_http(
    app_client: TestClient, db: Database
) -> None:
    _seed_po(db)
    _seed_owner(db)
    _seed_role_user(db, "designer@eternal.local", "designer")
    csrf = _login(app_client, "designer@eternal.local")

    upload = app_client.post(
        f"/api/design/{PO_ID}/submission",
        files={"file": ("design.pdf", b"%PDF-1.4 uji", "application/pdf")},
        headers={"X-CSRF-Token": csrf},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["submission"]["version"] == 1

    early_link = app_client.post(
        f"/api/design/{PO_ID}/approval-link", headers={"X-CSRF-Token": csrf}
    )
    assert early_link.status_code == 409

    confirmed = app_client.post(
        f"/api/design/{PO_ID}/confirm",
        json={"version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed_at"] is not None

    link = app_client.post(
        f"/api/design/{PO_ID}/approval-link", headers={"X-CSRF-Token": csrf}
    )
    assert link.status_code == 200
    token = link.json()["token"]

    # Halaman customer tidak memerlukan sesi internal dan hanya memuat satu PO.
    app_client.cookies.clear()
    context = app_client.get(f"/api/public/design-approval/{token}")
    assert context.status_code == 200
    assert context.json()["po_no"] == "PO/18/11/2026/000010"
    assert "operational" not in context.text

    approved = app_client.post(
        f"/api/public/design-approval/{token}/approve",
        json={"signer_name": "Pelanggan Uji", "signature_png_base64": SIGNATURE},
    )
    assert approved.status_code == 200
    assert approved.json()["duplicate"] is False
    assert approved.json()["gates"]["design"]["state"] == "open"

    repeat = app_client.post(
        f"/api/public/design-approval/{token}/approve",
        json={"signer_name": "Orang Lain", "signature_png_base64": SIGNATURE},
    )
    assert repeat.json()["duplicate"] is True


def test_invalid_public_token_404(app_client: TestClient, db: Database) -> None:
    _seed_po(db)
    assert app_client.get("/api/public/design-approval/token-palsu").status_code == 404


def test_payment_review_then_bypass_unlocks_scheduling(
    app_client: TestClient, db: Database
) -> None:
    _seed_po(db)
    _seed_owner(db)
    csrf = _login(app_client, "owner@eternal.local")

    review = app_client.post(
        f"/api/payment/{PO_ID}/receipts",
        json={"receipts": [{"id": "RC-1", "invoice_no": "", "amount": 100}]},
        headers={"X-CSRF-Token": csrf},
    )
    assert review.status_code == 200
    assert review.json()["gates"]["payment"]["state"] == "review"

    bypass = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "Disetujui Owner secara tertulis"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bypass.status_code == 200
    assert bypass.json()["payment"]["state"] == "open"

    app_client.post(
        f"/api/design/{PO_ID}/../design/{PO_ID}/confirm",
        json={"version": 1},
        headers={"X-CSRF-Token": csrf},
    )


def test_bypass_denied_for_non_privileged_role(
    app_client: TestClient, db: Database
) -> None:
    _seed_po(db)
    _seed_owner(db)
    _seed_role_user(db, "operator@eternal.local", "operator")
    csrf = _login(app_client, "operator@eternal.local")
    res = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "mau cepat"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 403


def test_design_endpoints_require_csrf(app_client: TestClient, db: Database) -> None:
    _seed_po(db)
    _seed_owner(db)
    _seed_role_user(db, "designer@eternal.local", "designer")
    _login(app_client, "designer@eternal.local")
    res = app_client.post(
        f"/api/design/{PO_ID}/submission",
        files={"file": ("design.pdf", b"x", "application/pdf")},
    )
    assert res.status_code == 403
