"""Alur Produksi: stage-counts satu scan + parity by-stage + flow-all."""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.auth.passwords import hash_password
from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.models.user import normalize_email
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import read_models
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
PASSWORD = "password-kuat-123"
BRANCH_A = "50"
BRANCH_B = "150"


def _seed_owner(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _login(client: TestClient, email: str = "owner@eternal.local") -> None:
    res = client.post(
        "/api/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert res.status_code == 200, res.text


def _seed_user(
    db: Database,
    email: str,
    *,
    branch_ids: list[str],
) -> None:
    db["users"].insert_one(
        {
            "email_normalized": normalize_email(email),
            "display_name": email,
            "password_hash": hash_password(PASSWORD),
            "role": "user",
            "operational_role": "helper",
            "is_active": True,
            "branch_ids": branch_ids,
            "effective_module_permissions": [],
        }
    )


def _seed_accepted(
    db: Database,
    *,
    accurate_id: str = PO_ID,
    po_no: str | None = None,
    branch_id: str | None = None,
    urgent: bool = False,
    customer: str = "Cust Uji",
) -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id) if str(accurate_id).isdigit() else payload["id"]
    payload["number"] = po_no or f"SO-{accurate_id}"
    # Line id unik per PO agar tidak bentrok antar fixture.
    for idx, line in enumerate(payload.get("detailItem") or []):
        line["id"] = int(f"{accurate_id}{idx + 1}")
    if branch_id is not None and str(branch_id).isdigit():
        payload["branchId"] = int(branch_id)
    model = normalize_sales_order(payload)
    upsert_sales_order(db, model)
    patch: dict = {
        "header.customer": customer,
        "header.urgent": bool(urgent),
        "po_no": po_no or f"SO-{accurate_id}",
    }
    if branch_id is not None:
        patch["branch_id"] = str(branch_id)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": str(accurate_id)},
        {"$set": patch},
    )
    mark_po_accepted(db, accurate_po_id=str(accurate_id))
    return get_by_accurate_id(db, str(accurate_id))


def test_stage_counts_unique_po_and_parity_with_by_stage(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    _seed_accepted(db)
    _login(app_client)

    counts_res = app_client.get("/api/production-flow/stage-counts")
    assert counts_res.status_code == 200, counts_res.text
    body = counts_res.json()
    assert body.get("accepted_only") is True
    assert body.get("source") == "mongodb"
    counts = body["counts"]
    assert counts["flow-all"] >= 1
    assert counts["flow-order-masuk"] >= 1
    assert counts["flow-all"] == body["scanned"]

    for node_id, expected in counts.items():
        stage = app_client.get(
            "/api/production-flow/by-stage",
            params={"node_id": node_id, "page": 1, "pageSize": 50},
        )
        assert stage.status_code == 200, stage.text
        assert stage.json()["total"] == expected


def test_stage_counts_all_unique_no_parallel_inflate(
    app_client: TestClient, db: Database
) -> None:
    """PO paralel: tiap tahap +1, Semua tetap 1."""
    from app.repositories import gate_repository as gates
    from app.services import po_tasks, scheduling_service

    _seed_owner(db)
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["detailItem"][1]["charField1"] = "Cetak"
    payload["detailItem"][1]["item"] = dict(payload["detailItem"][0]["item"])
    payload["detailItem"][1]["quantity"] = 400
    upsert_sales_order(db, normalize_sales_order(payload))
    mark_po_accepted(db)
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="uji"
    )
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="uji"
    )
    head = {
        "email_normalized": "kp@eternal.local",
        "role": "user",
        "operational_role": "production-head",
        "effective_module_permissions": ["access.all_branches"],
    }
    scheduling_service.create_allocations(
        db,
        head,
        po_doc=get_by_accurate_id(db, PO_ID),
        accurate_line_id="106318",
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="counts-parallel-a",
    )

    flow = read_models.production_flow_state(db, get_by_accurate_id(db, PO_ID))
    membership = list(flow.get("active_stages") or [])
    assert len(membership) >= 2

    _login(app_client)
    body = app_client.get("/api/production-flow/stage-counts").json()
    counts = body["counts"]
    assert counts["flow-all"] == 1
    for nid in membership:
        assert counts.get(nid, 0) == 1
    stage_sum = sum(v for k, v in counts.items() if k != "flow-all")
    assert stage_sum >= counts["flow-all"]


def test_stage_counts_pending_excluded(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    _login(app_client)
    body = app_client.get("/api/production-flow/stage-counts").json()
    assert body["counts"]["flow-all"] == 0
    assert body["counts"]["flow-order-masuk"] == 0


def test_by_stage_flow_all_unique(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_accepted(db, accurate_id="95106", po_no="SO-A")
    _seed_accepted(db, accurate_id="95107", po_no="SO-B")
    _login(app_client)
    res = app_client.get(
        "/api/production-flow/by-stage",
        params={"node_id": "flow-all", "page": 1, "pageSize": 50},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    ids = {i["accurate_po_id"] for i in body["items"]}
    assert ids == {"95106", "95107"}
    counts = app_client.get("/api/production-flow/stage-counts").json()["counts"]
    assert counts["flow-all"] == body["total"]


def test_stage_counts_search_and_urgent_parity(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    _seed_accepted(
        db, accurate_id="95106", po_no="SO-ALPHA", customer="Alpha Cust", urgent=True
    )
    _seed_accepted(
        db, accurate_id="95107", po_no="SO-BETA", customer="Beta Cust", urgent=False
    )
    _login(app_client)

    q_counts = app_client.get(
        "/api/production-flow/stage-counts", params={"q": "ALPHA"}
    ).json()["counts"]
    q_stage = app_client.get(
        "/api/production-flow/by-stage",
        params={"node_id": "flow-all", "q": "ALPHA"},
    ).json()
    assert q_counts["flow-all"] == 1
    assert q_stage["total"] == 1

    urg_counts = app_client.get(
        "/api/production-flow/stage-counts", params={"urgent": "true"}
    ).json()["counts"]
    urg_stage = app_client.get(
        "/api/production-flow/by-stage",
        params={"node_id": "flow-order-masuk", "urgent": "true"},
    ).json()
    assert urg_counts["flow-all"] == 1
    assert urg_counts["flow-order-masuk"] == urg_stage["total"]


def test_stage_counts_branch_isolation(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_accepted(db, accurate_id="95106", branch_id=BRANCH_A, po_no="SO-A")
    _seed_accepted(db, accurate_id="95107", branch_id=BRANCH_B, po_no="SO-B")
    _seed_user(db, "cabang-a@eternal.local", branch_ids=[BRANCH_A])
    _seed_user(db, "cabang-b@eternal.local", branch_ids=[BRANCH_B])

    _login(app_client, "cabang-a@eternal.local")
    a = app_client.get("/api/production-flow/stage-counts").json()["counts"]
    assert a["flow-all"] == 1

    app_client.post("/api/auth/logout")
    _login(app_client, "cabang-b@eternal.local")
    b = app_client.get("/api/production-flow/stage-counts").json()["counts"]
    assert b["flow-all"] == 1

    app_client.post("/api/auth/logout")
    _login(app_client, "owner@eternal.local")
    all_counts = app_client.get("/api/production-flow/stage-counts").json()["counts"]
    assert all_counts["flow-all"] == 2


def test_stage_counts_read_only(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_accepted(db)
    _login(app_client)
    before = db["accurate_sales_orders"].find_one({"accurate_id": PO_ID})
    app_client.get("/api/production-flow/stage-counts")
    after = db["accurate_sales_orders"].find_one({"accurate_id": PO_ID})
    assert before.get("operational") == after.get("operational")
    assert before.get("updated_at") == after.get("updated_at")


def test_stage_counts_service_direct_parity(db: Database) -> None:
    _seed_accepted(db)
    counts = read_models.list_production_flow_stage_counts(db)
    stage = read_models.list_production_flow_by_stage(
        db, node_id="flow-order-masuk", page=1, page_size=50
    )
    assert counts["counts"]["flow-order-masuk"] == stage["total"]
    all_stage = read_models.list_production_flow_by_stage(
        db, node_id="flow-all", page=1, page_size=50
    )
    assert counts["counts"]["flow-all"] == all_stage["total"]
    assert set(counts["counts"].keys()) == set(read_models.production_flow_stage_ids())
