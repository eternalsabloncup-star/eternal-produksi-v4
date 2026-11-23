"""M8-P1-01 — Design gate Policy A: every revision needs fresh customer approval."""

from __future__ import annotations

import base64

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
from app.services import design_service

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


def _upload_confirm_link(
    client: TestClient,
    *,
    csrf: str,
    version_expected: int,
    filename: str,
    content: bytes,
) -> str:
    upload = client.post(
        f"/api/design/{PO_ID}/submission",
        files={"file": (filename, content, "application/pdf")},
        headers={"X-CSRF-Token": csrf},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["submission"]["version"] == version_expected
    confirmed = client.post(
        f"/api/design/{PO_ID}/confirm",
        json={"version": version_expected},
        headers={"X-CSRF-Token": csrf},
    )
    assert confirmed.status_code == 200, confirmed.text
    link = client.post(
        f"/api/design/{PO_ID}/approval-link", headers={"X-CSRF-Token": csrf}
    )
    assert link.status_code == 200, link.text
    return link.json()["token"]


def _approve(client: TestClient, token: str) -> dict:
    res = client.post(
        f"/api/public/design-approval/{token}/approve",
        json={"signer_name": "Customer Uji", "signature_png_base64": SIGNATURE},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_policy_a_v1_approve_open_v2_upload_locks_old_approve_cannot_open(
    app_client: TestClient, db: Database
) -> None:
    _seed_po(db)
    _seed_users(db)
    csrf = _login(app_client, "designer@eternal.local")

    # Payment open so both_gates_open hinges on design version freshness.
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="payment",
        state=gates.GATE_OPEN,
        reason="test payment",
    )

    token_v1 = _upload_confirm_link(
        app_client,
        csrf=csrf,
        version_expected=1,
        filename="v1.pdf",
        content=b"%PDF-1.4 v1",
    )
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_CLOSED
    assert gates.both_gates_open(db, PO_ID) is False

    body = _approve(app_client, token_v1)
    assert body.get("gate_opened") is True
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_OPEN
    assert gates.design_unlocks_scheduling(db, PO_ID) is True
    assert gates.both_gates_open(db, PO_ID) is True

    # V2 upload must lock even though V1 approval remains on record.
    token_v2 = _upload_confirm_link(
        app_client,
        csrf=csrf,
        version_expected=2,
        filename="v2.pdf",
        content=b"%PDF-1.4 v2",
    )
    design_gate = gates.get_gates(db, PO_ID)["design"]
    assert design_gate["state"] == gates.GATE_REVIEW
    assert gates.design_unlocks_scheduling(db, PO_ID) is False
    assert gates.both_gates_open(db, PO_ID) is False
    # Historical V1 approval preserved.
    assert design.get_approval(db, accurate_po_id=PO_ID, design_version=1) is not None

    # Stale V1 token must not unlock after V2 exists.
    stale = app_client.post(
        f"/api/public/design-approval/{token_v1}/approve",
        json={"signer_name": "Customer Uji", "signature_png_base64": SIGNATURE},
    )
    assert stale.status_code == 200, stale.text
    # Duplicate approval of V1 — gate must stay locked while V2 unapproved.
    assert gates.get_gates(db, PO_ID)["design"]["state"] != gates.GATE_OPEN or (
        gates.design_unlocks_scheduling(db, PO_ID) is False
    )
    assert gates.both_gates_open(db, PO_ID) is False

    # Reject V2 → still locked.
    rejected = app_client.post(
        f"/api/public/design-approval/{token_v2}/reject",
        json={"reject_reason": "warna tidak sesuai brief"},
    )
    assert rejected.status_code == 200, rejected.text
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_REVIEW
    assert gates.both_gates_open(db, PO_ID) is False

    # Fresh V3 after reject → still locked until approve.
    csrf = _login(app_client, "designer@eternal.local")
    token_v3 = _upload_confirm_link(
        app_client,
        csrf=csrf,
        version_expected=3,
        filename="v3.pdf",
        content=b"%PDF-1.4 v3",
    )
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_REVIEW
    assert gates.both_gates_open(db, PO_ID) is False

    body3 = _approve(app_client, token_v3)
    assert body3.get("gate_opened") is True
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_OPEN
    assert gates.design_unlocks_scheduling(db, PO_ID) is True
    assert gates.both_gates_open(db, PO_ID) is True
    # Prior versions remain.
    assert design.get_approval(db, accurate_po_id=PO_ID, design_version=1) is not None
    assert design.latest_submission(db, PO_ID)["version"] == 3


def test_stale_version_approval_records_but_does_not_open(
    app_client: TestClient, db: Database
) -> None:
    _seed_po(db)
    _seed_users(db)
    csrf = _login(app_client, "designer@eternal.local")
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="payment",
        state=gates.GATE_OPEN,
        reason="test payment",
    )
    token_v1 = _upload_confirm_link(
        app_client,
        csrf=csrf,
        version_expected=1,
        filename="v1.pdf",
        content=b"%PDF-1.4 v1",
    )
    # Upload V2 before approving V1.
    _upload_confirm_link(
        app_client,
        csrf=csrf,
        version_expected=2,
        filename="v2.pdf",
        content=b"%PDF-1.4 v2",
    )
    body = _approve(app_client, token_v1)
    assert body.get("gate_opened") is False
    assert design.get_approval(db, accurate_po_id=PO_ID, design_version=1) is not None
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_REVIEW
    assert gates.both_gates_open(db, PO_ID) is False


def test_null_design_gate_with_payment_open_stays_locked(db: Database) -> None:
    """Legacy null design state merges to closed — fail-closed for Cetak path."""
    _seed_po(db)
    db["po_gates"].insert_one(
        {
            "accurate_po_id": PO_ID,
            "payment": {
                "state": gates.GATE_OPEN,
                "reason": "legacy payment",
            },
            # design omitted / null → defaults to closed
        }
    )
    merged = gates.get_gates(db, PO_ID)
    assert merged["design"]["state"] == gates.GATE_CLOSED
    assert gates.design_unlocks_scheduling(db, PO_ID) is False
    assert gates.both_gates_open(db, PO_ID) is False


def test_non_cetak_open_without_submissions_still_unlocks(db: Database) -> None:
    """Non-Cetak auto-sync exemption: OPEN + no submissions → unlocks design side."""
    gates.set_gate(
        db,
        accurate_po_id="non-cetak-1",
        gate="design",
        state=gates.GATE_OPEN,
        reason="PO Non-Cetak — Gate Design dibuka otomatis.",
    )
    gates.set_gate(
        db,
        accurate_po_id="non-cetak-1",
        gate="payment",
        state=gates.GATE_OPEN,
        reason="test",
    )
    assert design.latest_submission(db, "non-cetak-1") is None
    assert gates.design_unlocks_scheduling(db, "non-cetak-1") is True
    assert gates.both_gates_open(db, "non-cetak-1") is True


def test_service_lock_helper_on_revision(db: Database) -> None:
    _seed_po(db)
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="design",
        state=gates.GATE_OPEN,
        reason="approved v1",
        detail={"design_version": 1},
    )
    locked = design_service._lock_design_gate_for_new_version(
        db, accurate_po_id=PO_ID, version=2, actor_email="designer@eternal.local"
    )
    assert locked is not None
    assert locked["design"]["state"] == gates.GATE_REVIEW
