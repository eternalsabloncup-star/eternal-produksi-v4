"""M8.5 — scheduling write branch ACL + board KPI Policy A alignment."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import design_repository as design
from app.repositories import gate_repository as gates
from app.repositories.po_repository import upsert_sales_order
from app.services import scheduling_service
from tests.po_accept_helpers import mark_po_accepted
from tests.scheduling_operator_fixtures import (
    ensure_default_scheduling_operators,
    upsert_test_user,
)

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_A = "106318"


def _seed(db: Database) -> None:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"branch_id": "HQ", "branch_code": "HQ"}},
    )
    mark_po_accepted(db)
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )
    upsert_test_user(
        db,
        "kp-hq@eternal.local",
        operational_role="production-head",
        display_name="KP HQ",
        password=PASSWORD,
        # Non-empty list required: [] is falsy and fixture defaults to all_branches.
        effective_module_permissions=["reports.view"],
        branch_ids=["HQ"],
    )
    upsert_test_user(
        db,
        "kp-jog@eternal.local",
        operational_role="production-head",
        display_name="KP JOG",
        password=PASSWORD,
        effective_module_permissions=["reports.view"],
        branch_ids=["JOG"],
    )
    upsert_test_user(
        db,
        "designer@eternal.local",
        operational_role="designer",
        display_name="Designer",
        password=PASSWORD,
    )
    ensure_default_scheduling_operators(db)


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _open_gates(db: Database) -> None:
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji m85"
        )


def test_m85_create_allocation_cross_branch_denied(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp-jog@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 10}],
            "idempotency_key": "m85-cross-create-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 403, res.text
    assert (
        db["production_allocations"].count_documents(
            {"accurate_po_id": PO_ID, "status": "active"}
        )
        == 0
    )


def test_m85_create_allocation_same_branch_ok(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp-hq@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 25}],
            "idempotency_key": "m85-same-create-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["allocations"]) == 1


def test_m85_revise_and_rescan_cross_branch_denied(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf_hq = _login(app_client, "kp-hq@eternal.local")
    created = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 40}],
            "idempotency_key": "m85-revise-seed-1",
            "schedule_date": "2026-11-17",
        },
        headers={"X-CSRF-Token": csrf_hq},
    )
    assert created.status_code == 200, created.text
    alloc_id = created.json()["allocations"][0]["allocation_id"]

    csrf_jog = _login(app_client, "kp-jog@eternal.local")
    revised = app_client.put(
        f"/api/scheduling/allocations/{alloc_id}/qty",
        json={"qty": 10, "reason": "cross branch attempt"},
        headers={"X-CSRF-Token": csrf_jog},
    )
    assert revised.status_code == 403, revised.text
    live = db["production_allocations"].find_one({"allocation_id": alloc_id})
    assert live is not None
    assert float(live["qty"]) == 40.0

    scan = app_client.post(
        f"/api/scheduling/{PO_ID}/reschedule-scan",
        json={"as_of": "2026-11-18"},
        headers={"X-CSRF-Token": csrf_jog},
    )
    assert scan.status_code == 403, scan.text


def test_m85_board_kpi_rows_unlocked_respects_policy_a(db: Database) -> None:
    """OPEN design+payment with unapproved latest revision must not inflate KPI."""
    _seed(db)
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="payment",
        state=gates.GATE_OPEN,
        reason="payment open",
    )
    # Simulate OPEN design while a newer unapproved submission exists
    # (defensive Policy A case — write path already uses both_gates_open).
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="design",
        state=gates.GATE_OPEN,
        reason="stale open",
        detail={"design_version": 1},
    )
    design.create_submission(
        db,
        accurate_po_id=PO_ID,
        po_no="PO/TEST/95106",
        evidence_id="ev-m85-file-1",
        filename="v1.pdf",
        submitted_by="designer@eternal.local",
    )
    design.confirm_submission(
        db, accurate_po_id=PO_ID, version=1, confirmed_by="designer@eternal.local"
    )
    design.record_approval(
        db,
        accurate_po_id=PO_ID,
        design_version=1,
        signer_name="Customer",
        signature_evidence_id="ev-m85-1",
        token_hash="hash-v1",
    )
    design.create_submission(
        db,
        accurate_po_id=PO_ID,
        po_no="PO/TEST/95106",
        evidence_id="ev-m85-file-2",
        filename="v2.pdf",
        submitted_by="designer@eternal.local",
    )
    design.confirm_submission(
        db, accurate_po_id=PO_ID, version=2, confirmed_by="designer@eternal.local"
    )
    # Keep gate state OPEN artificially (corrupted/legacy) while v2 unapproved.
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="design",
        state=gates.GATE_OPEN,
        reason="legacy open without fresh approval",
        detail={"design_version": 1},
    )
    assert gates.get_gates(db, PO_ID)["design"]["state"] == gates.GATE_OPEN
    assert gates.design_unlocks_scheduling(db, PO_ID) is False
    assert gates.both_gates_open(db, PO_ID) is False

    board = scheduling_service.build_accepted_schedule_board(db, page_size=50)
    # Without Policy A check, rows_unlocked would be > 0 for this PO's Cetak lines.
    assert board["kpi"]["rows_unlocked"] == 0
    for item in board["items"]:
        if item.get("accurate_po_id") == PO_ID:
            assert item.get("scheduling_unlocked") is False


def test_m85_board_kpi_unlocked_when_both_gates_truly_open(db: Database) -> None:
    _seed(db)
    _open_gates(db)
    assert gates.both_gates_open(db, PO_ID) is True
    board = scheduling_service.build_accepted_schedule_board(db, page_size=50)
    assert board["kpi"]["rows_unlocked"] >= 1
    matching = [i for i in board["items"] if i.get("accurate_po_id") == PO_ID]
    assert matching
    assert all(i.get("scheduling_unlocked") is True for i in matching)


def test_m85_payment_bypass_still_requires_auth_and_branch(
    app_client: TestClient, db: Database
) -> None:
    """Regression: approved bypass remains gated; not removed by M8.5."""
    _seed(db)
    csrf = _login(app_client, "kp-jog@eternal.local")
    denied = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "cross branch bypass"},
        headers={"X-CSRF-Token": csrf},
    )
    assert denied.status_code == 403, denied.text

    # Same-branch KP without payment.bypass / ops.full → still denied.
    csrf_hq = _login(app_client, "kp-hq@eternal.local")
    no_perm = app_client.post(
        f"/api/payment/{PO_ID}/bypass",
        json={"reason": "no grant"},
        headers={"X-CSRF-Token": csrf_hq},
    )
    assert no_perm.status_code == 403, no_perm.text
