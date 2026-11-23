"""M7.1 — public design evidence + reject workflow (token-scoped)."""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import design_repository as design
from app.repositories import gate_repository as gates
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.repositories.task_repository import TASK_STATUS_OPEN

PASSWORD = "password-kuat-123"
PO_ID = "95106"
SIGNATURE = base64.b64encode(b"PNG-signature-test").decode()
def _seed_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted

    mark_po_accepted(db)
    return get_by_accurate_id(db, PO_ID)


def _seed_users(db: Database) -> None:
    if db["users"].find_one({"email_normalized": normalize_email("owner@eternal.local")}) is None:
        create_admin_user(
            db,
            email="owner@eternal.local",
            display_name="Owner",
            password=PASSWORD,
            allow_if_admin_exists=True,
        )
    if db["users"].find_one({"email_normalized": normalize_email("designer@eternal.local")}) is None:
        db["users"].insert_one(
            {
                "email_normalized": normalize_email("designer@eternal.local"),
                "display_name": "Designer",
                "password_hash": hash_password(PASSWORD),
                "role": "user",
                "operational_role": "designer",
                "is_active": True,
            }
        )


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _prepare_link(app_client: TestClient, db: Database, *, filename: str = "design.pdf", content: bytes = b"%PDF-1.4 uji", content_type: str = "application/pdf") -> str:
    _seed_po(db)
    _seed_users(db)
    csrf = _login(app_client, "designer@eternal.local")
    upload = app_client.post(
        f"/api/design/{PO_ID}/submission",
        files={"file": (filename, content, content_type)},
        headers={"X-CSRF-Token": csrf},
    )
    assert upload.status_code == 200, upload.text
    confirmed = app_client.post(
        f"/api/design/{PO_ID}/confirm",
        json={"version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200, confirmed.text
    link = app_client.post(
        f"/api/design/{PO_ID}/approval-link", headers={"X-CSRF-Token": csrf}
    )
    assert link.status_code == 200, link.text
    app_client.cookies.clear()
    return link.json()["token"]


def test_public_evidence_valid_token(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    ctx = app_client.get(f"/api/public/design-approval/{token}")
    assert ctx.status_code == 200
    body = ctx.json()
    assert body["preview_supported"] is True
    assert body["design_filename"] == "design.pdf"
    assert body["decision"] == "pending"

    evid = app_client.get(f"/api/public/design-approval/{token}/evidence")
    assert evid.status_code == 200, evid.text
    assert evid.headers["content-type"].startswith("application/pdf")
    assert "no-store" in evid.headers.get("cache-control", "")
    assert evid.content.startswith(b"%PDF")


def test_public_evidence_invalid_token(app_client: TestClient, db: Database) -> None:
    assert (
        app_client.get("/api/public/design-approval/token-palsu/evidence").status_code
        == 404
    )


def test_public_evidence_revoked_token(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    th = design.hash_token(token)
    db["design_approval_links"].update_one(
        {"token_hash": th},
        {"$set": {"revoked_at": datetime.now(timezone.utc)}},
    )
    assert (
        app_client.get(f"/api/public/design-approval/{token}/evidence").status_code
        == 404
    )


def test_public_evidence_missing_document(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    sub = design.latest_submission(db, PO_ID)
    assert sub
    db["evidence_files"].delete_one({"evidence_id": sub["evidence_id"]})
    assert (
        app_client.get(f"/api/public/design-approval/{token}/evidence").status_code
        == 404
    )


def test_public_evidence_unsupported_mime(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(
        app_client,
        db,
        filename="notes.txt",
        content=b"hello",
        content_type="text/plain",
    )
    ctx = app_client.get(f"/api/public/design-approval/{token}")
    assert ctx.json()["preview_supported"] is False
    evid = app_client.get(f"/api/public/design-approval/{token}/evidence")
    assert evid.status_code == 403


def test_reject_requires_reason(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    res = app_client.post(
        f"/api/public/design-approval/{token}/reject",
        json={"reject_reason": "ab"},
    )
    assert res.status_code == 422


def test_reject_valid_and_gate_review(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    # Derive design task then reject should reopen path
    _seed_users(db)
    csrf = _login(app_client, "owner@eternal.local")
    app_client.post(
        f"/api/workflow/{PO_ID}/derive-tasks", headers={"X-CSRF-Token": csrf}
    )
    app_client.cookies.clear()

    res = app_client.post(
        f"/api/public/design-approval/{token}/reject",
        json={"reject_reason": "Warna tidak sesuai brief"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["rejected"] is True
    assert body["duplicate"] is False
    assert body["gates"]["design"]["state"] == "review"
    assert "Revisi diperlukan" in body["gates"]["design"]["reason"]

    ctx = app_client.get(f"/api/public/design-approval/{token}")
    assert ctx.json()["decision"] == "rejected"

    # scheduling stays locked
    assert gates.both_gates_open(db, PO_ID) is False

    task = db["operational_tasks"].find_one({"task_key": f"task:design:po:{PO_ID}"})
    if task:
        assert task["status"] == TASK_STATUS_OPEN
        assert task.get("context", {}).get("revision_required") is True

    # evidence still readable on valid (non-expired) link after reject
    evid = app_client.get(f"/api/public/design-approval/{token}/evidence")
    assert evid.status_code == 200


def test_reject_duplicate_idempotent(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    first = app_client.post(
        f"/api/public/design-approval/{token}/reject",
        json={"reject_reason": "Perlu revisi layout"},
    )
    assert first.status_code == 200
    second = app_client.post(
        f"/api/public/design-approval/{token}/reject",
        json={"reject_reason": "Alasan lain diabaikan"},
    )
    assert second.status_code == 200
    assert second.json()["duplicate"] is True


def test_reject_denied_after_approve(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    approved = app_client.post(
        f"/api/public/design-approval/{token}/approve",
        json={"signer_name": "Pelanggan", "signature_png_base64": SIGNATURE},
    )
    assert approved.status_code == 200
    denied = app_client.post(
        f"/api/public/design-approval/{token}/reject",
        json={"reject_reason": "Terlambat menolak"},
    )
    assert denied.status_code == 409


def test_approve_denied_after_reject(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    rejected = app_client.post(
        f"/api/public/design-approval/{token}/reject",
        json={"reject_reason": "Tidak cocok"},
    )
    assert rejected.status_code == 200
    denied = app_client.post(
        f"/api/public/design-approval/{token}/approve",
        json={"signer_name": "Pelanggan", "signature_png_base64": SIGNATURE},
    )
    assert denied.status_code == 409


def test_revised_design_reapproval(app_client: TestClient, db: Database) -> None:
    token_v1 = _prepare_link(app_client, db)
    assert (
        app_client.post(
            f"/api/public/design-approval/{token_v1}/reject",
            json={"reject_reason": "Perlu revisi warna"},
        ).status_code
        == 200
    )

    csrf = _login(app_client, "designer@eternal.local")
    upload = app_client.post(
        f"/api/design/{PO_ID}/submission",
        files={"file": ("design-v2.pdf", b"%PDF-1.4 v2", "application/pdf")},
        headers={"X-CSRF-Token": csrf},
    )
    assert upload.status_code == 200
    assert upload.json()["submission"]["version"] == 2
    assert (
        app_client.post(
            f"/api/design/{PO_ID}/confirm",
            json={"version": 2},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 200
    )
    link2 = app_client.post(
        f"/api/design/{PO_ID}/approval-link", headers={"X-CSRF-Token": csrf}
    )
    assert link2.status_code == 200
    token_v2 = link2.json()["token"]
    app_client.cookies.clear()

    approved = app_client.post(
        f"/api/public/design-approval/{token_v2}/approve",
        json={"signer_name": "Pelanggan", "signature_png_base64": SIGNATURE},
    )
    assert approved.status_code == 200
    assert approved.json()["gates"]["design"]["state"] == "open"
    assert design.get_approval(db, accurate_po_id=PO_ID, design_version=2) is not None


def test_cross_po_evidence_denied(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    sub = design.latest_submission(db, PO_ID)
    assert sub
    evid_id = sub["evidence_id"]
    # Ownership check: scope_id must match link PO — foreign scope_id → 403.
    updated = db["evidence_files"].update_one(
        {"evidence_id": evid_id},
        {"$set": {"scope_id": "other-po"}},
    )
    assert updated.modified_count == 1
    res = app_client.get(f"/api/public/design-approval/{token}/evidence")
    assert res.status_code == 403


def test_reject_audit_event(app_client: TestClient, db: Database) -> None:
    token = _prepare_link(app_client, db)
    res = app_client.post(
        f"/api/public/design-approval/{token}/reject",
        json={"reject_reason": "Motif salah"},
    )
    assert res.status_code == 200
    event = db["audit_logs"].find_one({"action": "design.rejection.customer"})
    assert event is not None
    detail = event.get("detail") or {}
    assert detail.get("accurate_po_id") == PO_ID
    assert detail.get("reject_reason") == "Motif salah"
    assert detail.get("design_version") == 1