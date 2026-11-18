"""API Checkpoint C via HTTP: gate, RBAC, idempotency, revisi, persistensi."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories import gate_repository as gates
from app.repositories.po_repository import upsert_sales_order

PASSWORD = "password-kuat-123"
PO_ID = "95106"
LINE_A = "106318"


def _seed(db: Database) -> None:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
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
        }
    )
    db["users"].insert_one(
        {
            "email_normalized": normalize_email("operator@eternal.local"),
            "display_name": "Operator",
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": "operator",
            "is_active": True,
        }
    )


def _login(client: TestClient, email: str) -> str:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


def _open_gates(db: Database) -> None:
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji api"
        )


def test_schedule_view_requires_session(app_client: TestClient, db: Database) -> None:
    _seed(db)
    assert app_client.get(f"/api/scheduling/{PO_ID}").status_code == 401


def test_schedule_blocked_until_gates_open(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    csrf = _login(app_client, "kp@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 100}],
            "idempotency_key": "api-sched-blocked-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 409
    assert "Gate" in res.json()["detail"]


def test_allocation_flow_and_idempotency_over_http(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")

    payload = {
        "allocations": [
            {"operator_email": "op1@eternal.local", "qty": 600},
            {"operator_email": "op2@eternal.local", "qty": 400},
        ],
        "idempotency_key": "api-sched-split-1",
    }
    first = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()["allocations"]) == 2

    second = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert second.json()["duplicate"] is True

    # Persistensi: state dibaca ulang seperti setelah refresh browser.
    state = app_client.get(f"/api/scheduling/{PO_ID}/lines/{LINE_A}")
    assert state.status_code == 200
    body = state.json()
    assert body["qty_allocated"] == 1000
    assert body["qty_unallocated"] == 0
    assert len(body["allocations"]) == 2


def test_over_allocation_rejected_over_http(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 1500}],
            "idempotency_key": "api-sched-over-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 422
    assert app_client.get(f"/api/scheduling/{PO_ID}/lines/{LINE_A}").json()[
        "qty_allocated"
    ] == 0


def test_operator_cannot_schedule(app_client: TestClient, db: Database) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "operator@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 10}],
            "idempotency_key": "api-sched-denied-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 403


def test_revise_allocation_and_history_over_http(
    app_client: TestClient, db: Database
) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")
    created = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 900}],
            "idempotency_key": "api-sched-revise-1",
        },
        headers={"X-CSRF-Token": csrf},
    )
    alloc_id = created.json()["allocations"][0]["allocation_id"]

    revised = app_client.put(
        f"/api/scheduling/allocations/{alloc_id}/qty",
        json={"qty": 500, "reason": "Operator dipindah"},
        headers={"X-CSRF-Token": csrf},
    )
    assert revised.status_code == 200
    assert revised.json()["qty"] == 500

    history = app_client.get(f"/api/scheduling/allocations/{alloc_id}/history")
    assert history.status_code == 200
    rows = history.json()["items"]
    assert [r["action"] for r in rows] == ["create", "update_qty"]
    assert rows[1]["reason"] == "Operator dipindah"


def test_reschedule_scan_over_http(app_client: TestClient, db: Database) -> None:
    _seed(db)
    _open_gates(db)
    csrf = _login(app_client, "kp@eternal.local")
    app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 700}],
            "idempotency_key": "api-sched-resched-1",
            "schedule_date": "2026-11-17",
        },
        headers={"X-CSRF-Token": csrf},
    )
    scan = app_client.post(
        f"/api/scheduling/{PO_ID}/reschedule-scan",
        json={"as_of": "2026-11-18"},
        headers={"X-CSRF-Token": csrf},
    )
    assert scan.status_code == 200
    keys = scan.json()["created_task_keys"]
    assert len(keys) == 1

    again = app_client.post(
        f"/api/scheduling/{PO_ID}/reschedule-scan",
        json={"as_of": "2026-11-18"},
        headers={"X-CSRF-Token": csrf},
    )
    assert again.json()["created_task_keys"] == keys

    listed = app_client.get("/api/tasks?task_type=penjadwalan-ulang")
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["qty_target"] == 700


def test_schedule_write_requires_csrf(app_client: TestClient, db: Database) -> None:
    _seed(db)
    _open_gates(db)
    _login(app_client, "kp@eternal.local")
    res = app_client.post(
        f"/api/scheduling/{PO_ID}/lines/{LINE_A}/allocations",
        json={
            "allocations": [{"operator_email": "op1@eternal.local", "qty": 10}],
            "idempotency_key": "api-sched-csrf-1",
        },
    )
    assert res.status_code == 403
