"""Regresi FINAL AUDIT REMEDIATION — FIND T5/T3/T4/CFG (P0/P1).

Mongo test terisolasi saja. Tidak menyentuh production.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache, get_settings
from app.domain.operational import TaskRef, build_task_key
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import evidence_repository as evidence
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import read_models
from app.services.object_auth import ObjectAuthError
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED
from app.services.po_completion import (
    _obligation_qty,
    evaluate_po_fulfillment,
    sj_has_active_delivery_evidence,
)
from app.services.task_engine import TaskPermissionError
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NC = "106319"
SJ_ID = "SJ-AUDIT-REM"
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


def _seed_po(db: Database, *, line_qty: float | None = None) -> dict:
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
    if line_qty is not None:
        db["accurate_sales_orders"].update_one(
            {"accurate_id": PO_ID},
            {"$set": {"header.items.$[].qty": float(line_qty)}},
        )
        # Pastikan setiap line punya qty target eksplisit (array filter aman).
        po = get_by_accurate_id(db, PO_ID)
        assert po is not None
        items = list((po.get("header") or {}).get("items") or [])
        for it in items:
            it["qty"] = float(line_qty)
        db["accurate_sales_orders"].update_one(
            {"accurate_id": PO_ID},
            {"$set": {"header.items": items}},
        )
    return get_by_accurate_id(db, PO_ID)


def _sj(*, qty: float = 10, line_id: str = LINE_PRINT) -> dict:
    return {
        "id": SJ_ID,
        "number": "SJ/AUDIT/001",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-20",
        "toAddress": "ALAMAT",
        "shipmentName": "OUTLET",
        "branchId": 150,
        "detailItem": [
            {
                "id": "SJL-A",
                "salesOrderDetailId": line_id,
                "itemNo": "BRG-SIM-001",
                "quantity": qty,
                "itemUnitName": "PCS",
            }
        ],
    }


def _all_nodes_done() -> dict[str, str]:
    return {nid: read_models.NODE_DONE for nid, _ in read_models.PRODUCTION_FLOW_NODES}


def _insert_evidence(
    db: Database,
    *,
    evidence_id: str,
    scope: str,
    scope_id: str,
    deleted: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    evidence.evidence_collection(db).insert_one(
        {
            "evidence_id": evidence_id,
            "scope": scope,
            "scope_id": scope_id,
            "version": 1,
            "filename": f"{evidence_id}.png",
            "content_type": "image/png",
            "size_bytes": 8,
            "sha256": "a" * 64,
            "gridfs_id": evidence_id,
            "uploaded_by": "driver@eternal.local",
            "created_at": now,
            "deleted_at": now if deleted else None,
            "deleted_by": "driver@eternal.local" if deleted else None,
        }
    )


def _fulfill_line_with_evidence(
    db: Database,
    *,
    line_id: str,
    qty: float,
    sj_id: str,
    soft_delete: bool = False,
    recipient: str = "Penerima Uji",
) -> None:
    """SJ sukses + qty line + bukti (opsional soft-delete)."""
    payload = _sj(qty=qty, line_id=line_id)
    payload["id"] = sj_id
    payload["number"] = f"SJ/{sj_id}"
    deliveries.upsert_delivery_note(db, payload)
    note = deliveries.get_delivery_note(db, sj_id)
    assert note is not None
    db["delivery_notes"].update_one(
        {"accurate_sj_id": sj_id},
        {"$set": {"branch_id": "150"}},
    )
    photos = [f"evid:delivery-photo:{sj_id}:1", f"evid:delivery-photo:{sj_id}:2"]
    sig = f"evid:delivery-signature:{sj_id}:1"
    db["delivery_events"].insert_one(
        {
            "delivery_event_id": f"delivery:{sj_id}:attempt-1",
            "accurate_sj_id": sj_id,
            "accurate_po_id": PO_ID,
            "attempt": 1,
            "status": deliveries.DELIVERY_SUCCESS,
            "recipient_name": recipient,
            "photo_evidence_ids": photos,
            "signature_evidence_id": sig,
            "created_at": datetime.now(timezone.utc),
        }
    )
    for pid in photos:
        _insert_evidence(
            db, evidence_id=pid, scope="delivery-photo", scope_id=sj_id, deleted=soft_delete
        )
    _insert_evidence(
        db, evidence_id=sig, scope="delivery-signature", scope_id=sj_id, deleted=soft_delete
    )


# --- 1–2: FIND-T5-01 strict membership ---


def test_01_strict_on_all_nodes_passed_invalid_evidence_not_selesai(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_STRICT_PO_COMPLETION_ENABLED=True)
    po = _seed_po(db, line_qty=100)
    # SJ sukses tanpa bukti valid (tanpa evidence docs).
    deliveries.upsert_delivery_note(db, _sj(qty=100))
    db["delivery_events"].insert_one(
        {
            "delivery_event_id": f"delivery:{SJ_ID}:attempt-1",
            "accurate_sj_id": SJ_ID,
            "accurate_po_id": PO_ID,
            "attempt": 1,
            "status": deliveries.DELIVERY_SUCCESS,
            "recipient_name": "X",
            "photo_evidence_ids": [],
            "signature_evidence_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    assert evaluate_po_fulfillment(db, po)["complete"] is False
    stages = read_models.active_stage_membership_from_facts(
        db,
        statuses=_all_nodes_done(),
        has_print=True,
        accurate_po_id=PO_ID,
        print_lines=[],
        allocations_by_line={},
        non_print_lines=[],
        po_doc=po,
    )
    assert read_models.FLOW_SELESAI_ID not in stages


def test_02_strict_on_soft_deleted_evidence_not_selesai(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_STRICT_PO_COMPLETION_ENABLED=True)
    po = _seed_po(db, line_qty=50)
    # Satu line saja: set items hanya NC agar kewajiban tunggal.
    items = [i for i in (po["header"]["items"]) if i.get("accurate_line_id") == LINE_NC]
    assert items
    items[0]["qty"] = 50.0
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID}, {"$set": {"header.items": items}}
    )
    po = get_by_accurate_id(db, PO_ID)
    _fulfill_line_with_evidence(
        db, line_id=LINE_NC, qty=50, sj_id="SJ-SOFT-DEL", soft_delete=True
    )
    assert sj_has_active_delivery_evidence(db, "SJ-SOFT-DEL") is False
    assert evaluate_po_fulfillment(db, po)["complete"] is False
    stages = read_models.active_stage_membership_from_facts(
        db,
        statuses=_all_nodes_done(),
        has_print=False,
        accurate_po_id=PO_ID,
        print_lines=[],
        allocations_by_line={},
        non_print_lines=[],
        po_doc=po,
    )
    assert read_models.FLOW_SELESAI_ID not in stages


# --- 3–5: FIND-T5-02 obligation ---


def test_03_packing_partial_obligation_stays_po_qty(db: Database) -> None:
    po = _seed_po(db, line_qty=5000)
    item = next(
        i for i in po["header"]["items"] if i.get("accurate_line_id") == LINE_PRINT
    )
    # Packing 3000 tersimpan di task — kewajiban tetap 5000.
    tasks.upsert_task(
        db,
        task_type="qc-packing",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_PRINT),
        qty_target=5000,
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": build_task_key("qc-packing", "po_line", TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_PRINT))},
        {"$set": {"qty_confirmed": 3000}},
    )
    assert _obligation_qty(item, db, PO_ID) == 5000.0


def test_04_delivered_partial_not_complete(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_STRICT_PO_COMPLETION_ENABLED=True)
    po = _seed_po(db)
    items = [i for i in po["header"]["items"] if i.get("accurate_line_id") == LINE_PRINT]
    items[0]["qty"] = 5000.0
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID}, {"$set": {"header.items": items}}
    )
    po = get_by_accurate_id(db, PO_ID)
    _fulfill_line_with_evidence(
        db, line_id=LINE_PRINT, qty=3000, sj_id="SJ-PARTIAL-DEL"
    )
    result = evaluate_po_fulfillment(db, po)
    assert result["complete"] is False
    line = next(r for r in result["lines"] if r["accurate_line_id"] == LINE_PRINT)
    assert line["qty_obligation"] == 5000.0
    assert line["qty_shipped"] == 3000.0


def test_05_full_qty_valid_evidence_complete(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_STRICT_PO_COMPLETION_ENABLED=True)
    po = _seed_po(db)
    items = [i for i in po["header"]["items"] if i.get("accurate_line_id") == LINE_PRINT]
    items[0]["qty"] = 5000.0
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID}, {"$set": {"header.items": items}}
    )
    po = get_by_accurate_id(db, PO_ID)
    _fulfill_line_with_evidence(
        db, line_id=LINE_PRINT, qty=5000, sj_id="SJ-FULL-OK"
    )
    result = evaluate_po_fulfillment(db, po)
    assert result["complete"] is True
    stages = read_models.active_stage_membership_from_facts(
        db,
        statuses=_all_nodes_done(),
        has_print=True,
        accurate_po_id=PO_ID,
        print_lines=[],
        allocations_by_line={},
        non_print_lines=[],
        po_doc=po,
    )
    assert stages == [read_models.FLOW_SELESAI_ID]


# --- 6–7: FIND-T3-01 assignee preserve ---


def test_06_sj_resync_preserves_assignee(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    # Resync tanpa assignee eksplisit.
    again = deliveries.upsert_delivery_note(
        db,
        {
            **_sj(qty=12),
            "toAddress": "ALAMAT-REVISI",
        },
    )["delivery_note"]
    delivery.ensure_loading_sj_task(db, again)
    task = tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID))
    assert task is not None
    assert (task.get("context") or {}).get("assigned_user_email") == "helper@eternal.local"


def test_07_resync_then_reassignment_authoritative(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    master.set_role_actions(
        db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    delivery.ensure_loading_sj_task(db, note)  # resync
    delivery.assign_loading_sj_task(
        db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="driver@eternal.local"
    )
    task = tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID))
    assert (task.get("context") or {}).get("assigned_user_email") == "driver@eternal.local"
    # Resync setelah reassignment tidak mengembalikan assignee lama.
    delivery.ensure_loading_sj_task(db, note)
    task2 = tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID))
    assert (task2.get("context") or {}).get("assigned_user_email") == "driver@eternal.local"


# --- 8–10: FIND-T4-01 loading-sj auth ---


def test_08_loading_sj_on_pengiriman_confirm_only_403(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    # Cabut loading-sj helper; biarkan hanya pengiriman pada driver.
    master.set_role_actions(
        db, role="helper", task_type="loading-sj", actions=["view"]
    )
    master.set_role_actions(
        db,
        role="driver",
        task_type="pengiriman",
        actions=["view", "input", "confirm", "manage_evidence", "cancel"],
    )
    master.set_role_actions(
        db, role="driver", task_type="loading-sj", actions=[]
    )
    deliveries.upsert_delivery_note(db, _sj())
    with pytest.raises(TaskPermissionError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="no-loading-grant"
        )


def test_09_loading_sj_on_grant_and_assignment_ok(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(
        monkeypatch,
        V4_LOADING_SJ_TASK_ENABLED=True,
        V4_LOADING_PACKING_LEDGER_ENFORCED=False,
    )
    _seed_po(db)
    master.set_role_actions(
        db,
        role="helper",
        task_type="loading-sj",
        actions=["view", "confirm", "edit"],
    )
    master.set_role_actions(
        db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    note = deliveries.upsert_delivery_note(db, _sj(qty=5, line_id=LINE_NC))[
        "delivery_note"
    ]
    delivery.ensure_loading_sj_task(db, note)
    delivery.assign_loading_sj_task(
        db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
    )
    result = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="ok-loading-grant"
    )
    assert result["duplicate"] is False


def test_10_loading_sj_on_pengiriman_cancel_only_403(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(
        monkeypatch,
        V4_LOADING_SJ_TASK_ENABLED=True,
        V4_LOADING_PACKING_LEDGER_ENFORCED=False,
    )
    _seed_po(db)
    master.set_role_actions(
        db,
        role="helper",
        task_type="loading-sj",
        actions=["view", "confirm", "edit"],
    )
    master.set_role_actions(
        db,
        role="driver",
        task_type="pengiriman",
        actions=["view", "input", "confirm", "manage_evidence", "cancel"],
    )
    master.set_role_actions(db, role="driver", task_type="loading-sj", actions=[])
    note = deliveries.upsert_delivery_note(db, _sj(qty=5, line_id=LINE_NC))[
        "delivery_note"
    ]
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="load-then-cancel"
    )
    with pytest.raises(TaskPermissionError):
        delivery.cancel_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, reason="coba cancel via pengiriman"
        )


# --- 11–13: FIND-T4-02 discrepancy ---


def test_11_discrepancy_cross_branch_denied(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    master.set_role_actions(
        db,
        role="helper",
        task_type="loading-sj",
        actions=["view", "confirm", "edit"],
    )
    note = deliveries.upsert_delivery_note(db, _sj(qty=99))["delivery_note"]
    db["delivery_notes"].update_one(
        {"accurate_sj_id": SJ_ID}, {"$set": {"branch_id": "150"}}
    )
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    foreign = {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "operational_role": "helper",
        "branch_ids": ["999"],
        "effective_module_permissions": [],
    }
    with pytest.raises(ObjectAuthError):
        delivery.raise_quantity_discrepancy(db, foreign, accurate_sj_id=SJ_ID)


def test_12_discrepancy_without_permission_denied(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    master.set_role_actions(db, role="helper", task_type="loading-sj", actions=["view"])
    deliveries.upsert_delivery_note(db, _sj(qty=99))
    with pytest.raises(TaskPermissionError):
        delivery.raise_quantity_discrepancy(db, HELPER, accurate_sj_id=SJ_ID)


def test_13_duplicate_discrepancy_one_logical_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    master.set_role_actions(
        db,
        role="helper",
        task_type="loading-sj",
        actions=["view", "confirm", "edit"],
    )
    note = deliveries.upsert_delivery_note(db, _sj(qty=99))["delivery_note"]
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    first = delivery.raise_quantity_discrepancy(db, HELPER, accurate_sj_id=SJ_ID)
    second = delivery.raise_quantity_discrepancy(db, HELPER, accurate_sj_id=SJ_ID)
    assert first["task_key"] == second["task_key"]
    keys = [
        t["task_key"]
        for t in tasks.list_tasks(db, task_type="qty-loading-sj-berbeda")
    ]
    assert len(keys) == 1


# --- 14–15: flags ON / OFF ---


def test_14_four_flags_on_isolated_integration(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(
        monkeypatch,
        V4_NON_CETAK_PACKING_ACTIVATED_AT=ACTIVATION,
        V4_LOADING_PACKING_LEDGER_ENFORCED=True,
        V4_LOADING_SJ_TASK_ENABLED=True,
        V4_STRICT_PO_COMPLETION_ENABLED=True,
    )
    s = get_settings()
    assert s.loading_packing_ledger_enforced is True
    assert s.loading_sj_task_enabled is True
    assert s.strict_po_completion_enabled is True
    assert bool(str(s.non_cetak_packing_activated_at or "").strip())
    po = _seed_po(db)
    assert evaluate_po_fulfillment(db, po)["complete"] is False
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    task = delivery.ensure_loading_sj_task(db, note)
    assert task is not None
    assert task["task_type"] == "loading-sj"
    v = delivery.validate_loading(db, SJ_ID)
    # Default SJ line = Cetak → packing ledger mode (fail-closed tanpa confirmed packing).
    assert v["items"][0]["ledger_mode"] == "packing"
    assert v["items"][0]["process_kind"] == "Cetak"
    # Non-Cetak path (Owner direct Loading) tetap direct_physical.
    deliveries.upsert_delivery_note(db, _sj(qty=5, line_id=LINE_NC))
    v_nc = delivery.validate_loading(db, SJ_ID)
    assert v_nc["items"][0]["ledger_mode"] == "direct_physical"
    assert v_nc["all_matched"] is True


def test_15_all_flags_off_legacy_regression(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(
        monkeypatch,
        V4_NON_CETAK_PACKING_ACTIVATED_AT="",
        V4_LOADING_PACKING_LEDGER_ENFORCED=False,
        V4_LOADING_SJ_TASK_ENABLED=False,
        V4_STRICT_PO_COMPLETION_ENABLED=False,
    )
    s = get_settings()
    assert s.loading_packing_ledger_enforced is False
    assert s.loading_sj_task_enabled is False
    assert s.strict_po_completion_enabled is False
    po = _seed_po(db)
    # Legacy: all nodes done → Selesai meski fulfillment checker False.
    assert evaluate_po_fulfillment(db, po)["complete"] is False
    stages = read_models.active_stage_membership_from_facts(
        db,
        statuses=_all_nodes_done(),
        has_print=True,
        accurate_po_id=PO_ID,
        print_lines=[],
        allocations_by_line={},
        non_print_lines=[],
        po_doc=po,
    )
    assert stages == [read_models.FLOW_SELESAI_ID]
    deliveries.upsert_delivery_note(db, _sj(qty=5, line_id=LINE_NC))
    v = delivery.validate_loading(db, SJ_ID)
    assert v["all_matched"] is True
    assert v["items"][0]["ledger_mode"] == "direct_physical"
    assert delivery.ensure_loading_sj_task(db, deliveries.get_delivery_note(db, SJ_ID)) is None


# --- 16: FIND-CFG-01 ---


def test_16_invalid_activation_timestamp_off_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("V4_NON_CETAK_PACKING_ACTIVATED_AT", "not-a-timestamp")
    clear_settings_cache()
    summary = get_settings().safe_summary()
    assert summary["non_cetak_packing_activated"] is False
    assert summary["non_cetak_packing_activation_status"] == "invalid"
    blob = str(summary)
    assert "ACCURATE_API_TOKEN" not in blob
    assert not any(
        isinstance(v, str) and "secret" in str(v).lower() and len(str(v)) > 20
        for v in summary.values()
    )
