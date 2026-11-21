"""Field opsional alokasi: print_color, screen_kind; operator Master; evidence desain."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import gate_repository as gates
from app.repositories import schedule_repository as schedules
from app.repositories.po_repository import upsert_sales_order
from app.services import design_service, scheduling_service
from app.services.design_service import DESIGN_SCOPE

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_A = "106318"


def _seed(db: Database) -> None:
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
    from tests.scheduling_operator_fixtures import upsert_test_user

    upsert_test_user(
        db,
        "kp@eternal.local",
        operational_role="production-head",
        display_name="Kepala Produksi",
        password=PASSWORD,
    )
    upsert_test_user(
        db,
        "op.warna@eternal.local",
        operational_role="operator",
        display_name="Budi Operator",
        password=PASSWORD,
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


def test_create_allocation_persists_print_color_and_screen_kind(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [
                {
                    "operator_email": "op.warna@eternal.local",
                    "qty": 250,
                    "print_color": "Hitam",
                    "screen_kind": "Besar",
                }
            ],
            "idempotency_key": "alloc-fields-1",
            "schedule_date": "2026-11-22",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    created = res.json()["allocations"][0]
    assert created["print_color"] == "Hitam"
    assert created["screen_kind"] == "Besar"
    assert created["schedule_date"] == "2026-11-22"
    assert created["operator_email"] == "op.warna@eternal.local"

    state = app_client.get(f"/api/scheduling/{PO_ID}/lines/{LINE_A}")
    assert state.status_code == 200
    row = state.json()["allocations"][0]
    assert row["print_color"] == "Hitam"
    assert row["screen_kind"] == "Besar"
    assert row["operator_display_name"] == "Budi Operator"
    assert row["schedule_date"] == "2026-11-22"

    stored = schedules.get_allocation(db, created["allocation_id"])
    assert stored is not None
    assert stored.get("print_color") == "Hitam"
    assert stored.get("screen_kind") == "Besar"


def test_legacy_allocation_without_new_fields_still_reads(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")
    # Alokasi tanpa print_color/screen_kind tetap sah bila operator Master valid.
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [
                {"operator_email": "op.warna@eternal.local", "qty": 100}
            ],
            "idempotency_key": "alloc-legacy-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    state = app_client.get(f"/api/scheduling/{PO_ID}/lines/{LINE_A}").json()
    row = state["allocations"][0]
    assert row["print_color"] is None
    assert row["screen_kind"] is None
    assert "print_color" not in (schedules.get_allocation(db, row["allocation_id"]) or {})


def test_invalid_screen_kind_rejected(app_client: TestClient, db: Database) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [
                {
                    "operator_email": "op.warna@eternal.local",
                    "qty": 50,
                    "screen_kind": "Sedang",
                }
            ],
            "idempotency_key": "alloc-bad-screen",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 422


def test_revise_qty_preserves_print_color_and_screen_kind(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")
    created = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [
                {
                    "operator_email": "op.warna@eternal.local",
                    "qty": 200,
                    "print_color": "Merah",
                    "screen_kind": "Kecil",
                }
            ],
            "idempotency_key": "alloc-revise-keep-1",
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["allocations"][0]
    alloc_id = created["allocation_id"]
    revised = app_client.put(
        f"/api/scheduling/allocations/{alloc_id}/qty",
        json={"qty": 180, "reason": "koreksi kapasitas"},
        headers={"X-CSRF-Token": csrf},
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["qty"] == 180
    stored = schedules.get_allocation(db, alloc_id)
    assert stored is not None
    assert stored.get("print_color") == "Merah"
    assert stored.get("screen_kind") == "Kecil"


def test_operators_endpoint_lists_master_names(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _login(app_client, "kp@eternal.local")
    res = app_client.get("/api/scheduling/operators")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert any(
        i["email"] == "op.warna@eternal.local" and i["display_name"] == "Budi Operator"
        for i in items
    )


def test_design_evidence_content_stream_scoped(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    owner = {
        "email_normalized": "owner@eternal.local",
        "role": "super_admin",
        "operational_role": None,
        "effective_module_permissions": ["access.all_branches"]
    }
    design_service.submit_design(
        db,
        owner,
        accurate_po_id=PO_ID,
        po_no="SO-TEST",
        filename="desain.png",
        content_type="image/png",
        content=b"\x89PNG\r\n\x1a\nfake",
    )
    latest = app_client.get(f"/api/design/{PO_ID}")
    # need session
    _login(app_client, "kp@eternal.local")
    latest = app_client.get(f"/api/design/{PO_ID}")
    assert latest.status_code == 200
    evid = latest.json()["latest"]["evidence_id"]
    content = app_client.get(f"/api/design/{PO_ID}/evidence/{evid}/content")
    assert content.status_code == 200
    assert content.content.startswith(b"\x89PNG")
    assert content.headers["content-type"].startswith("image/png")

    # Scope mismatch / foreign evidence_id
    bad = app_client.get(f"/api/design/{PO_ID}/evidence/evid:fake/content")
    assert bad.status_code == 404

    assert DESIGN_SCOPE == "design"
