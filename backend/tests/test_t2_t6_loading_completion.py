"""T2–T6 focused: packing ledger Loading, loading-sj, completion, membership."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.domain.operational import TaskRef
from app.services import delivery_service as delivery
from app.services import po_tasks, production_service as prod
from app.services import read_models
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED
from app.services.po_completion import evaluate_po_fulfillment
from app.services.task_engine import TaskPermissionError
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NC = "106319"
SJ_ID = "SJ-T2-MIX"
ACTIVATION = "2026-11-01T00:00:00+00:00"

HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["access.all_branches"],
}
DRIVER = {
    "email_normalized": "driver@eternal.local",
    "role": "user",
    "operational_role": "driver",
    "effective_module_permissions": ["access.all_branches"],
}
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}


def _env(monkeypatch: pytest.MonkeyPatch, **flags: str | bool) -> None:
    for k, v in flags.items():
        if v is False or v == "":
            monkeypatch.setenv(k, "0" if isinstance(v, bool) else "")
        elif v is True:
            monkeypatch.setenv(k, "1")
        else:
            monkeypatch.setenv(k, str(v))
    clear_settings_cache()


def _seed_accepted(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    mark_po_accepted(db)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {
            "$set": {
                "operational.acceptance.status": ACCEPTANCE_ACCEPTED,
                "operational.acceptance.accepted_at": datetime(
                    2026, 11, 20, tzinfo=timezone.utc
                ),
            }
        },
    )
    return get_by_accurate_id(db, PO_ID)


def _sj(*, items: list[dict] | None = None) -> dict:
    return {
        "id": SJ_ID,
        "number": "SJ/T2/001",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-20",
        "toAddress": "ALAMAT",
        "shipmentName": "OUTLET",
        "detailItem": items
        or [
            {
                "id": "SJL-C",
                "salesOrderDetailId": LINE_PRINT,
                "itemNo": "BRG-SIM-001",
                "quantity": 10,
                "itemUnitName": "PCS",
            },
            {
                "id": "SJL-N",
                "salesOrderDetailId": LINE_NC,
                "itemNo": "BRG-SIM-002",
                "quantity": 20,
                "itemUnitName": "PACK",
            },
        ],
    }


def test_t2_legacy_non_cetak_self_compare_when_flag_off(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_PACKING_LEDGER_ENFORCED=False)
    _seed_accepted(db)
    deliveries.upsert_delivery_note(db, _sj(items=[
        {
            "id": "SJL-N",
            "salesOrderDetailId": LINE_NC,
            "itemNo": "BRG-SIM-002",
            "quantity": 50,
            "itemUnitName": "PACK",
        }
    ]))
    # Driver needs pengiriman confirm
    master.set_role_actions(
        db, role="driver", task_type="pengiriman",
        actions=["view", "input", "confirm", "manage_evidence", "cancel"],
    )
    v = delivery.validate_loading(db, SJ_ID)
    assert v["all_matched"] is True
    assert v["items"][0]["ledger_mode"] == "legacy_sj_self"


def test_t2_enforced_rejects_without_packing_ledger(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_PACKING_LEDGER_ENFORCED=True)
    _seed_accepted(db)
    deliveries.upsert_delivery_note(db, _sj(items=[
        {
            "id": "SJL-N",
            "salesOrderDetailId": LINE_NC,
            "itemNo": "BRG-SIM-002",
            "quantity": 10,
            "itemUnitName": "PACK",
        }
    ]))
    v = delivery.validate_loading(db, SJ_ID)
    assert v["all_matched"] is False
    assert v["items"][0]["ledger_mode"] == "packing_required"


def test_t2_enforced_mixed_sj_consumes_non_cetak_packing(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(
        monkeypatch,
        V4_LOADING_PACKING_LEDGER_ENFORCED=True,
        V4_NON_CETAK_PACKING_ACTIVATED_AT=ACTIVATION,
    )
    po = _seed_accepted(db)
    po_tasks.derive_tasks_for_po(db, po)
    # Pack Non-Cetak
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_NC,
        qty=30,
        idempotency_key="nc-pack-t2",
    )
    # Minimal Cetak packing: create qc task manually with confirmed
    tasks.upsert_task(
        db,
        task_type="qc-packing",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_PRINT),
        qty_target=1000,
        label="QC",
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": prod.qc_task_key(PO_ID, LINE_PRINT)},
        {"$set": {"qty_confirmed": 100}},
    )
    master.set_role_actions(
        db, role="driver", task_type="pengiriman",
        actions=["view", "input", "confirm", "manage_evidence", "cancel"],
    )
    deliveries.upsert_delivery_note(db, _sj())
    v = delivery.validate_loading(db, SJ_ID)
    assert v["all_matched"] is True
    result = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-t2-mix"
    )
    assert result["duplicate"] is False
    nc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert float(nc.get("qty_consumed") or 0) == 20
    cetak = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT))
    assert float(cetak.get("qty_consumed") or 0) == 10


def test_t3_loading_sj_task_assign_and_assignee_gate(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_accepted(db)
    master.set_role_actions(
        db, role="driver", task_type="pengiriman",
        actions=["view", "input", "confirm", "manage_evidence", "cancel"],
    )
    master.set_role_actions(
        db, role="helper", task_type="loading-sj",
        actions=["view", "confirm", "edit"],
    )
    upserted = deliveries.upsert_delivery_note(db, _sj(items=[
        {
            "id": "SJL-N",
            "salesOrderDetailId": LINE_NC,
            "itemNo": "BRG-SIM-002",
            "quantity": 5,
            "itemUnitName": "PACK",
        }
    ]))
    note = upserted.get("delivery_note") or upserted
    delivery.ensure_loading_sj_task(db, note)
    delivery.assign_loading_sj_task(
        db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
    )
    with pytest.raises(TaskPermissionError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="wrong-assignee"
        )


def test_t3_mismatch_creates_kp_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=False)
    _seed_accepted(db)
    master.set_role_actions(
        db, role="driver", task_type="pengiriman",
        actions=["view", "input", "confirm", "manage_evidence", "cancel"],
    )
    deliveries.upsert_delivery_note(db, _sj(items=[
        {
            "id": "SJL-N",
            "salesOrderDetailId": LINE_NC,
            "itemNo": "BRG-SIM-002",
            "quantity": 5,
            "itemUnitName": "PACK",
        }
    ]))
    res = delivery.reject_loading_mismatch(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Qty fisik beda"
    )
    assert res["discrepancy_task"]["task_type"] == "qty-loading-sj-berbeda"
    # retry idempotent upsert
    res2 = delivery.reject_loading_mismatch(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Qty fisik beda"
    )
    keys = [
        t["task_key"]
        for t in tasks.list_tasks(db, task_type="qty-loading-sj-berbeda")
    ]
    assert len(keys) == 1


def test_t5_completion_incomplete_without_delivery(db: Database) -> None:
    po = _seed_accepted(db)
    result = evaluate_po_fulfillment(db, po)
    assert result["complete"] is False
    assert result["reasons"]


def test_t6_non_cetak_packing_membership(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_NON_CETAK_PACKING_ACTIVATED_AT=ACTIVATION)
    po = _seed_accepted(db)
    # Open gates so membership exits design/payment
    from app.repositories import gate_repository as gates

    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="t"
    )
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="t"
    )
    po_tasks.derive_tasks_for_po(db, po)
    flow = read_models.production_flow_state(db, get_by_accurate_id(db, PO_ID))
    assert "flow-packing" in (flow.get("active_stages") or []) or any(
        n["id"] == "flow-packing" and n["status"] != "tidak_berlaku"
        for n in flow["nodes"]
    )
