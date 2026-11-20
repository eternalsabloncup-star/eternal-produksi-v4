"""Alur Produksi: accepted gate + membership tahap aktif + by-stage."""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient
from pymongo.database import Database

from app.bootstrap import create_admin_user
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import read_models
from app.services.po_acceptance import ACCEPTANCE_PENDING
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
PASSWORD = "password-kuat-123"


def _seed_owner(db: Database) -> None:
    create_admin_user(
        db,
        email="owner@eternal.local",
        display_name="Owner",
        password=PASSWORD,
        allow_if_admin_exists=True,
    )


def _login(client: TestClient) -> None:
    res = client.post(
        "/api/auth/login",
        json={"email": "owner@eternal.local", "password": PASSWORD},
    )
    assert res.status_code == 200, res.text


def _seed_pending(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    return get_by_accurate_id(db, PO_ID)


def _seed_accepted(db: Database) -> dict:
    po = _seed_pending(db)
    mark_po_accepted(db)
    return get_by_accurate_id(db, PO_ID)


def test_pending_po_not_in_production_flow_list(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    _seed_pending(db)
    _login(app_client)
    res = app_client.get("/api/production-flow")
    assert res.status_code == 200
    ids = [i["accurate_po_id"] for i in res.json()["items"]]
    assert PO_ID not in ids


def test_pending_po_by_id_returns_404(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_pending(db)
    _login(app_client)
    res = app_client.get(f"/api/production-flow?accurate_po_id={PO_ID}")
    assert res.status_code == 404


def test_accepted_po_eligible_in_list(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_accepted(db)
    _login(app_client)
    res = app_client.get("/api/production-flow")
    assert res.status_code == 200
    body = res.json()
    assert body.get("accepted_only") is True
    ids = [i["accurate_po_id"] for i in body["items"]]
    assert PO_ID in ids
    flow = next(i for i in body["items"] if i["accurate_po_id"] == PO_ID)
    assert "active_stages" in flow
    assert "flow-order-masuk" in flow["active_stages"]


def test_by_stage_order_masuk_excludes_pending(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    _seed_pending(db)
    _login(app_client)
    res = app_client.get(
        "/api/production-flow/by-stage",
        params={"node_id": "flow-order-masuk", "page": 1, "pageSize": 50},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0
    assert body["accepted_only"] is True
    assert all(i["accurate_po_id"] != PO_ID for i in body["items"])


def test_by_stage_order_masuk_includes_accepted(
    app_client: TestClient, db: Database
) -> None:
    _seed_owner(db)
    _seed_accepted(db)
    _login(app_client)
    res = app_client.get(
        "/api/production-flow/by-stage",
        params={"node_id": "flow-order-masuk", "page": 1, "pageSize": 50},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] >= 1
    assert body.get("scan_capped") is False
    row = next(i for i in body["items"] if i["accurate_po_id"] == PO_ID)
    assert row["acceptance"]["status"] == "accepted"
    assert row["node_id"] == "flow-order-masuk"
    assert "po" in row
    assert row["po"]["po_no"]
    # Node SoT Order Masuk = selesai, label monitoring = Aktif (bukan "Selesai").
    assert row["stage_status"] == read_models.NODE_DONE
    assert row["monitoring_label"] == read_models.MONITOR_LABEL_AKTIF


def test_monitoring_label_dalam_proses_when_running() -> None:
    assert (
        read_models.monitoring_label(
            node_id="flow-design", node_status=read_models.NODE_RUNNING
        )
        == read_models.MONITOR_LABEL_DALAM_PROSES
    )
    assert (
        read_models.monitoring_label(
            node_id="flow-selesai", node_status=read_models.NODE_DONE
        )
        == read_models.MONITOR_LABEL_SELESAI
    )


def test_multi_item_parallel_membership_union(db: Database) -> None:
    """Dua line Cetak paralel: satu belum dijadwalkan, satu sudah → union tanpa duplikat."""
    from app.repositories import gate_repository as gates
    from app.services import po_tasks, scheduling_service

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
    po = get_by_accurate_id(db, PO_ID)
    head = {
        "email_normalized": "kp@eternal.local",
        "role": "user",
        "operational_role": "production-head",
        "effective_module_permissions": ["access.all_branches"],
    }
    scheduling_service.create_allocations(
        db,
        head,
        po_doc=po,
        accurate_line_id="106318",
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="flow-multi-line-a",
    )
    flow = read_models.production_flow_state(db, get_by_accurate_id(db, PO_ID))
    active = flow["active_stages"]
    assert "flow-penjadwalan" in active  # line 106319 belum dijadwalkan
    assert "flow-screener" in active or "flow-persiapan" in active  # line A terjadwal
    assert len(active) == len(set(active))
    assert "flow-order-masuk" not in active


def test_by_stage_pagination_beyond_former_scan_cap(
    app_client: TestClient, db: Database
) -> None:
    """Isolasi: >500 accepted di Order Masuk — total & halaman akhir tidak hilang."""
    _seed_owner(db)
    _login(app_client)
    n = 520
    now_base = "2026-11-20T10:00:00+00:00"
    docs = []
    for i in range(n):
        aid = f"pf-bulk-{i:04d}"
        docs.append(
            {
                "accurate_id": aid,
                "po_no": f"SO-BULK/{i:04d}",
                "origin": "accurate",
                "updated_at": now_base,
                "header": {
                    "customer": f"Cust {i}",
                    "urgent": False,
                    "items": [
                        {
                            "accurate_line_id": f"L{i}",
                            "item_code": "X",
                            "item_name": "Item",
                            "qty": 1,
                            "unit": "PCS",
                            "process_kind": "Cetak",
                        }
                    ],
                },
                "operational": {
                    "acceptance": {
                        "status": "accepted",
                        "accepted_by": "owner@eternal.local",
                        "accepted_at": now_base,
                    },
                    "inbox_status": "PO Diterima",
                },
            }
        )
    db["accurate_sales_orders"].insert_many(docs)

    res = app_client.get(
        "/api/production-flow/by-stage",
        params={"node_id": "flow-order-masuk", "page": 1, "pageSize": 50},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] >= n
    assert body["scanned"] >= n
    assert body.get("scan_capped") is False
    assert body.get("scan_cap") is None
    assert len(body["items"]) == 50

    last_page = (body["total"] + 49) // 50
    res2 = app_client.get(
        "/api/production-flow/by-stage",
        params={
            "node_id": "flow-order-masuk",
            "page": last_page,
            "pageSize": 50,
        },
    )
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["total"] == body["total"]
    assert len(body2["items"]) >= 1
    # ID paling akhir (sort accurate_id desc dengan updated_at sama) tetap muncul.
    ids = {i["accurate_po_id"] for i in body2["items"]}
    assert any(aid.startswith("pf-bulk-") for aid in ids)


def test_membership_waiting_not_all_stages_active(db: Database) -> None:
    po = _seed_accepted(db)
    flow = read_models.production_flow_state(db, po)
    active = flow["active_stages"]
    assert active == ["flow-order-masuk"]
    # Menunggu di Design/Payment tidak membuat semua tahap aktif.
    assert "flow-design" not in active
    assert "flow-cetak" not in active
    assert "flow-pengiriman" not in active


def test_membership_no_duplicate(db: Database) -> None:
    po = _seed_accepted(db)
    flow = read_models.production_flow_state(db, po)
    active = flow["active_stages"]
    assert len(active) == len(set(active))


def test_non_print_skips_print_stages_in_membership(db: Database) -> None:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    for line in payload["detailItem"]:
        line["charField1"] = "Tidak Cetak"
    upsert_sales_order(db, normalize_sales_order(payload))
    mark_po_accepted(db)
    # Derive gate design non-cetak seperti accept nyata
    from app.services.po_tasks import derive_tasks_for_po

    po = get_by_accurate_id(db, PO_ID)
    derive_tasks_for_po(db, po)
    po = get_by_accurate_id(db, PO_ID)
    flow = read_models.production_flow_state(db, po)
    nodes = {n["id"]: n["status"] for n in flow["nodes"]}
    assert nodes["flow-design"] == read_models.NODE_NOT_APPLICABLE
    # Tidak boleh memaksa tahap cetak sebagai aktif.
    for nid in (
        "flow-penjadwalan",
        "flow-screener",
        "flow-persiapan",
        "flow-operator",
        "flow-cetak",
        "flow-packing",
    ):
        assert nid not in flow["active_stages"]


def test_by_stage_unknown_node_422(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _login(app_client)
    res = app_client.get(
        "/api/production-flow/by-stage",
        params={"node_id": "flow-bukan-tahap"},
    )
    assert res.status_code == 422


def test_by_stage_is_read_only(app_client: TestClient, db: Database) -> None:
    _seed_owner(db)
    _seed_accepted(db)
    _login(app_client)
    before = db["accurate_sales_orders"].find_one({"accurate_id": PO_ID})
    app_client.get(
        "/api/production-flow/by-stage",
        params={"node_id": "flow-order-masuk"},
    )
    after = db["accurate_sales_orders"].find_one({"accurate_id": PO_ID})
    assert before.get("operational") == after.get("operational")
    assert before.get("updated_at") == after.get("updated_at")


def test_pending_marker_constant() -> None:
    assert ACCEPTANCE_PENDING == "pending"
