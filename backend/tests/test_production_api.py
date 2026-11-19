"""API Checkpoint D: auth, CSRF, validasi file, gate, idempotency."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import gate_repository as gates
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import scheduling_service

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_A = "106318"
PNG = b"\x89PNG sample-api"


def _user(db: Database, email: str, role: str) -> None:
    db["users"].insert_one(
        {
            "email_normalized": normalize_email(email),
            "display_name": email,
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": role,
            "is_active": True,
        }
    )


def _seed(db: Database) -> str:
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
    for email, role in (
        ("kp@eternal.local", "production-head"),
        ("helper@eternal.local", "helper"),
        ("screener@eternal.local", "screener"),
        ("op1@eternal.local", "operator"),
    ):
        _user(db, email, role)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji api"
        )
    po_doc = get_by_accurate_id(db, PO_ID)
    res = scheduling_service.create_allocations(
        db,
        {"email_normalized": "kp@eternal.local", "role": "user", "operational_role": "production-head"},
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 500}],
        idempotency_key="api-d-setup-1",
    )
    return res["allocations"][0]["allocation_id"]


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def test_production_endpoints_require_session(
    app_client: TestClient, db: Database
) -> None:
    alloc = _seed(db)
    assert app_client.get(f"/api/production/{PO_ID}/lines/{LINE_A}").status_code == 401
    assert (
        app_client.post(
            f"/api/production/allocations/{alloc}/print",
            json={"qty": 1, "idempotency_key": "x" * 10},
        ).status_code
        == 401
    )


def test_preparation_and_screen_over_http(app_client: TestClient, db: Database) -> None:
    alloc = _seed(db)
    helper_csrf = _login(app_client, "helper@eternal.local")
    prep = app_client.post(
        f"/api/production/{PO_ID}/lines/{LINE_A}/preparation",
        json={"qty": 400, "idempotency_key": "api-prep-400"},
        headers={"X-CSRF-Token": helper_csrf},
    )
    assert prep.status_code == 200, prep.text
    assert prep.json()["quota"]["qty_prepared"] == 400
    assert prep.json()["quota"]["qty_belum_disiapkan"] == 600

    screener_csrf = _login(app_client, "screener@eternal.local")
    screen = app_client.post(
        f"/api/production/allocations/{alloc}/screen",
        json={"qty": 120, "idempotency_key": "api-screen-120"},
        headers={"X-CSRF-Token": screener_csrf},
    )
    assert screen.status_code == 200
    assert screen.json()["task"]["qty_confirmed"] == 120


def test_helper_cannot_confirm_screen(app_client: TestClient, db: Database) -> None:
    alloc = _seed(db)
    csrf = _login(app_client, "helper@eternal.local")
    res = app_client.post(
        f"/api/production/allocations/{alloc}/screen",
        json={"qty": 10, "idempotency_key": "api-screen-denied"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 403


def test_sample_upload_rejects_unsupported_type(
    app_client: TestClient, db: Database
) -> None:
    alloc = _seed(db)
    csrf = _login(app_client, "op1@eternal.local")
    res = app_client.post(
        f"/api/production/allocations/{alloc}/samples",
        files={"file": ("sample.exe", b"MZ", "application/x-msdownload")},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 415


def test_sample_requires_csrf(app_client: TestClient, db: Database) -> None:
    alloc = _seed(db)
    _login(app_client, "op1@eternal.local")
    res = app_client.post(
        f"/api/production/allocations/{alloc}/samples",
        files={"file": ("sample.png", PNG, "image/png")},
    )
    assert res.status_code == 403


def test_full_production_flow_over_http(app_client: TestClient, db: Database) -> None:
    alloc = _seed(db)

    helper_csrf = _login(app_client, "helper@eternal.local")
    app_client.post(
        f"/api/production/{PO_ID}/lines/{LINE_A}/preparation",
        json={"qty": 500, "idempotency_key": "flow-prep-500"},
        headers={"X-CSRF-Token": helper_csrf},
    )

    screener_csrf = _login(app_client, "screener@eternal.local")
    app_client.post(
        f"/api/production/allocations/{alloc}/screen",
        json={"qty": 100, "idempotency_key": "flow-screen-100"},
        headers={"X-CSRF-Token": screener_csrf},
    )

    op_csrf = _login(app_client, "op1@eternal.local")
    # Cetak terkunci sebelum sampel disetujui.
    locked = app_client.post(
        f"/api/production/allocations/{alloc}/print",
        json={"qty": 10, "idempotency_key": "flow-print-locked"},
        headers={"X-CSRF-Token": op_csrf},
    )
    assert locked.status_code == 409

    uploaded = app_client.post(
        f"/api/production/allocations/{alloc}/samples",
        files={"file": ("sample.png", PNG, "image/png")},
        headers={"X-CSRF-Token": op_csrf},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["sample"]["status"] == "draft"
    assert uploaded.json()["sample"]["version"] == 1

    confirmed = app_client.post(
        f"/api/production/allocations/{alloc}/samples/confirm",
        headers={"X-CSRF-Token": op_csrf},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["sample"]["status"] == "menunggu_approval"

    kp_csrf = _login(app_client, "kp@eternal.local")
    rejected = app_client.post(
        f"/api/production/allocations/{alloc}/samples/decision",
        json={"version": 1, "approved": False, "reason": "Warna tidak sesuai"},
        headers={"X-CSRF-Token": kp_csrf},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "ditolak"

    op_csrf = _login(app_client, "op1@eternal.local")
    app_client.post(
        f"/api/production/allocations/{alloc}/samples",
        files={"file": ("sample-v2.png", b"\x89PNG v2", "image/png")},
        headers={"X-CSRF-Token": op_csrf},
    )
    confirmed_v2 = app_client.post(
        f"/api/production/allocations/{alloc}/samples/confirm",
        headers={"X-CSRF-Token": op_csrf},
    )
    assert confirmed_v2.status_code == 200
    assert confirmed_v2.json()["sample"]["version"] == 2
    kp_csrf = _login(app_client, "kp@eternal.local")
    approved = app_client.post(
        f"/api/production/allocations/{alloc}/samples/decision",
        json={"version": 2, "approved": True},
        headers={"X-CSRF-Token": kp_csrf},
    )
    assert approved.json()["status"] == "disetujui"

    op_csrf = _login(app_client, "op1@eternal.local")
    printed = app_client.post(
        f"/api/production/allocations/{alloc}/print",
        json={"qty": 250, "idempotency_key": "flow-print-250"},
        headers={"X-CSRF-Token": op_csrf},
    )
    assert printed.status_code == 200
    assert printed.json()["task"]["qty_confirmed"] == 250

    retry = app_client.post(
        f"/api/production/allocations/{alloc}/print",
        json={"qty": 250, "idempotency_key": "flow-print-250"},
        headers={"X-CSRF-Token": op_csrf},
    )
    assert retry.json()["duplicate"] is True
    assert retry.json()["quota"]["qty_consumed"] == 250

    helper_csrf = _login(app_client, "helper@eternal.local")
    packed = app_client.post(
        f"/api/production/{PO_ID}/lines/{LINE_A}/qc-packing",
        json={"qty": 150, "idempotency_key": "flow-qc-150"},
        headers={"X-CSRF-Token": helper_csrf},
    )
    assert packed.status_code == 200
    assert packed.json()["qty_ready_to_ship"] == 150

    over = app_client.post(
        f"/api/production/{PO_ID}/lines/{LINE_A}/qc-packing",
        json={"qty": 200, "idempotency_key": "flow-qc-over"},
        headers={"X-CSRF-Token": helper_csrf},
    )
    assert over.status_code == 409

    # Persistensi: state dibaca ulang seperti setelah refresh browser.
    state = app_client.get(f"/api/production/{PO_ID}/lines/{LINE_A}")
    body = state.json()
    assert body["qty_printed_total"] == 250
    assert body["qty_packed"] == 150
    assert body["qty_waiting_qc"] == 100
    assert body["helper_quota"]["qty_available"] == 250
    assert body["allocations"][0]["sample_approved"] is True


def test_sample_history_endpoint(app_client: TestClient, db: Database) -> None:
    alloc = _seed(db)
    helper_csrf = _login(app_client, "helper@eternal.local")
    app_client.post(
        f"/api/production/{PO_ID}/lines/{LINE_A}/preparation",
        json={"qty": 100, "idempotency_key": "hist-prep"},
        headers={"X-CSRF-Token": helper_csrf},
    )
    screener_csrf = _login(app_client, "screener@eternal.local")
    app_client.post(
        f"/api/production/allocations/{alloc}/screen",
        json={"qty": 10, "idempotency_key": "hist-screen"},
        headers={"X-CSRF-Token": screener_csrf},
    )
    op_csrf = _login(app_client, "op1@eternal.local")
    app_client.post(
        f"/api/production/allocations/{alloc}/samples",
        files={"file": ("s.png", PNG, "image/png")},
        headers={"X-CSRF-Token": op_csrf},
    )
    listed = app_client.get(f"/api/production/allocations/{alloc}/samples")
    assert listed.status_code == 200
    assert [s["version"] for s in listed.json()["items"]] == [1]
