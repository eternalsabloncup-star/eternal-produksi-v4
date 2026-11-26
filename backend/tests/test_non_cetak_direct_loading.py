"""Non-Cetak Direct Loading — Owner 2026-11-27 (supersedes Packing T1 prerequisite).

MUDITA mixed SJ: Cetak packing OK + Non-Cetak tanpa packing ledger → Loading eligible.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.domain.operational import TaskRef
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import po_tasks, production_service as prod
from app.services.object_auth import ObjectAuthError
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED
from app.services.task_engine import TaskPermissionError
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NC = "106319"
SJ_ID = "120913"
SJ_NO = "DO/2026/11/23/000030"

HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["access.all_branches"],
}
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}
OTHER_BRANCH = {
    "email_normalized": "other@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": [],
    "branch_ids": ["999"],
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


def _seed_mudita_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    mark_po_accepted(db)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {
            "$set": {
                "branch_id": "50",
                "operational.acceptance.status": ACCEPTANCE_ACCEPTED,
                "operational.acceptance.accepted_at": datetime(
                    2026, 11, 19, 10, 32, 20, tzinfo=timezone.utc
                ),
            }
        },
    )
    return get_by_accurate_id(db, PO_ID)


def _mudita_sj(*, cetak_qty: float = 1000, nc_qty: float = 1000) -> dict:
    return {
        "id": SJ_ID,
        "number": SJ_NO,
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-23",
        "toAddress": "Yogyakarta",
        "shipmentName": "OUTLET",
        "branchId": "50",
        "detailItem": [
            {
                "id": "128572",
                "salesOrderDetailId": LINE_PRINT,
                "itemNo": "100359",
                "detailName": "12 oz Oval Inject Bening Super",
                "quantity": cetak_qty,
                "itemUnitName": "PCS",
            },
            {
                "id": "128573",
                "salesOrderDetailId": LINE_NC,
                "itemNo": "100115",
                "detailName": "LID Connect Inject Bening Victory",
                "quantity": nc_qty,
                "itemUnitName": "PCS",
            },
        ],
    }


def _grant_loading(db: Database) -> None:
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
    master.set_role_actions(
        db,
        role="production-head",
        task_type="qty-loading-sj-berbeda",
        actions=["view", "confirm"],
    )


def _seed_cetak_packing(db: Database, qty: float = 1000) -> None:
    tasks.upsert_task(
        db,
        task_type="qc-packing",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(
            accurate_po_id=PO_ID,
            accurate_line_id=LINE_PRINT,
            process_kind="Cetak",
        ),
        qty_target=qty,
        label="QC & Packing",
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": prod.qc_task_key(PO_ID, LINE_PRINT)},
        {"$set": {"qty_confirmed": qty, "status": tasks.TASK_STATUS_DONE}},
    )


def _prepare_loading_actor(db: Database, note: dict) -> None:
    delivery.ensure_loading_sj_task(db, note)
    delivery.assign_loading_sj_task(
        db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
    )


def test_mudita_mixed_sj_non_cetak_without_packing_eligible(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repro MUDITA: enforced flag ON, NC packing absent → still eligible."""
    _env(
        monkeypatch,
        V4_LOADING_PACKING_LEDGER_ENFORCED=True,
        V4_LOADING_SJ_TASK_ENABLED=True,
        V4_NON_CETAK_PACKING_ACTIVATED_AT="2026-11-20T17:16:01+00:00",
    )
    _seed_mudita_po(db)
    _grant_loading(db)
    _seed_cetak_packing(db, 1000)
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC)) is None

    note = deliveries.upsert_delivery_note(db, _mudita_sj())["delivery_note"]
    v = delivery.validate_loading(db, SJ_ID)
    by_line = {str(i["accurate_line_id"]): i for i in v["items"]}
    assert by_line[LINE_PRINT]["matched"] is True
    assert by_line[LINE_PRINT]["process_kind"] == "Cetak"
    assert float(by_line[LINE_PRINT]["qty_available"]) == 1000
    assert by_line[LINE_NC]["matched"] is True
    assert by_line[LINE_NC]["ledger_mode"] == "direct_physical"
    assert by_line[LINE_NC]["physical_verification_required"] is True
    assert "Packing Non-Cetak belum ada" not in (by_line[LINE_NC].get("reason") or "")
    assert v["all_matched"] is True

    _prepare_loading_actor(db, note)
    result = delivery.confirm_loading_match(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="mudita-sesuai-1"
    )
    assert result["duplicate"] is False
    loading = result["loading"]
    items = {str(i["accurate_line_id"]): i for i in loading["items"]}
    assert items[LINE_PRINT]["packing_consumed"] is True
    assert items[LINE_NC]["packing_consumed"] is False
    assert items[LINE_NC]["physical_verified"] is True
    assert items[LINE_NC]["ledger_mode"] == "direct_physical"
    # Cetak packing consumed; NC packing task never existed / not consumed.
    cetak = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT))
    assert float(cetak.get("qty_consumed") or 0) == 1000
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC)) is None


def test_non_cetak_tidak_sesuai_creates_kp_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_mudita_po(db)
    _grant_loading(db)
    _seed_cetak_packing(db, 1000)
    note = deliveries.upsert_delivery_note(db, _mudita_sj())["delivery_note"]
    _prepare_loading_actor(db, note)
    res = delivery.reject_loading_mismatch(
        db, HELPER, accurate_sj_id=SJ_ID, reason="Qty fisik lid kurang"
    )
    assert res["discrepancy_task"]["task_type"] == "qty-loading-sj-berbeda"
    assert deliveries.get_loading_event(db, SJ_ID) is None


def test_no_new_non_cetak_packing_tasks(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(
        monkeypatch,
        V4_NON_CETAK_PACKING_ACTIVATED_AT="2026-11-01T00:00:00+00:00",
    )
    po = _seed_mudita_po(db)
    # Even if accepted "after" old activation, Owner direct-loading → no NC packing.
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {
            "$set": {
                "operational.acceptance.accepted_at": datetime(
                    2026, 11, 21, tzinfo=timezone.utc
                )
            }
        },
    )
    po = get_by_accurate_id(db, PO_ID)
    derived = po_tasks.derive_tasks_for_po(db, po)
    assert derived["non_cetak_packing_eligible"] is False
    assert "qc-packing:non-cetak:owner-direct-loading" in derived["skipped"]
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC)) is None


def test_cetak_packing_shortfall_fail_closed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_PACKING_LEDGER_ENFORCED=True)
    _seed_mudita_po(db)
    _seed_cetak_packing(db, 500)  # shortfall vs SJ 1000
    deliveries.upsert_delivery_note(db, _mudita_sj())
    v = delivery.validate_loading(db, SJ_ID)
    by_line = {str(i["accurate_line_id"]): i for i in v["items"]}
    assert by_line[LINE_PRINT]["matched"] is False
    assert by_line[LINE_NC]["matched"] is True
    assert v["all_matched"] is False


def test_non_accepted_po_assign_fail_closed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    _grant_loading(db)
    note = deliveries.upsert_delivery_note(db, _mudita_sj())["delivery_note"]
    delivery.ensure_loading_sj_task(db, note)
    with pytest.raises(Exception):
        delivery.assign_loading_sj_task(
            db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
        )


def test_wrong_branch_fail_closed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_mudita_po(db)
    _grant_loading(db)
    _seed_cetak_packing(db, 1000)
    note = deliveries.upsert_delivery_note(db, _mudita_sj())["delivery_note"]
    # Stamp branch on note for ACL
    db["accurate_delivery_notes"].update_one(
        {"accurate_sj_id": SJ_ID},
        {"$set": {"branch_id": "50"}},
    )
    note = deliveries.get_delivery_note(db, SJ_ID)
    delivery.ensure_loading_sj_task(db, note)
    delivery.assign_loading_sj_task(
        db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
    )
    with pytest.raises((ObjectAuthError, PermissionError, Exception)):
        delivery.confirm_loading(
            db, OTHER_BRANCH, accurate_sj_id=SJ_ID, idempotency_key="x-branch"
        )


def test_duplicate_loading_idempotent(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_mudita_po(db)
    _grant_loading(db)
    _seed_cetak_packing(db, 1000)
    note = deliveries.upsert_delivery_note(db, _mudita_sj())["delivery_note"]
    _prepare_loading_actor(db, note)
    r1 = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="dup-1"
    )
    r2 = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="dup-2"
    )
    assert r1["duplicate"] is False
    assert r2["duplicate"] is True
    assert r1["loading"]["loading_id"] == r2["loading"]["loading_id"]


def test_invalid_sj_line_mapping_fail_closed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_PACKING_LEDGER_ENFORCED=True)
    _seed_mudita_po(db)
    _seed_cetak_packing(db, 1000)
    bad = _mudita_sj()
    bad["detailItem"][1]["salesOrderDetailId"] = "999999"
    deliveries.upsert_delivery_note(db, bad)
    v = delivery.validate_loading(db, SJ_ID)
    assert v["all_matched"] is False
    assert any(not i["matched"] for i in v["items"] if str(i.get("accurate_line_id")) == "999999" or not i["matched"])


def test_historical_nc_packing_task_preserved(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing historical NC packing task is not deleted; Loading still direct."""
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_mudita_po(db)
    _grant_loading(db)
    _seed_cetak_packing(db, 1000)
    # Simulate historical NC packing leftover
    tasks.upsert_task(
        db,
        task_type="qc-packing",
        scope="po_line",
        assigned_role="helper",
        ref=TaskRef(
            accurate_po_id=PO_ID,
            accurate_line_id=LINE_NC,
            process_kind="Non-Cetak",
        ),
        qty_target=1000,
        label="Packing Non-Cetak",
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": prod.qc_task_key(PO_ID, LINE_NC)},
        {"$set": {"qty_confirmed": 1000, "status": tasks.TASK_STATUS_DONE}},
    )
    note = deliveries.upsert_delivery_note(db, _mudita_sj())["delivery_note"]
    v = delivery.validate_loading(db, SJ_ID)
    assert v["all_matched"] is True
    nc = next(i for i in v["items"] if i["accurate_line_id"] == LINE_NC)
    assert nc["ledger_mode"] == "direct_physical"
    _prepare_loading_actor(db, note)
    result = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="hist-nc"
    )
    # Historical NC packing not consumed
    hist = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert hist is not None
    assert float(hist.get("qty_consumed") or 0) == 0
    assert result["loading"] is not None


def test_assignee_gate_still_enforced(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_mudita_po(db)
    _grant_loading(db)
    _seed_cetak_packing(db, 1000)
    note = deliveries.upsert_delivery_note(db, _mudita_sj())["delivery_note"]
    delivery.ensure_loading_sj_task(db, note)
    delivery.assign_loading_sj_task(
        db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
    )
    stranger = {
        "email_normalized": "stranger@eternal.local",
        "role": "user",
        "operational_role": "helper",
        "effective_module_permissions": ["access.all_branches"],
    }
    with pytest.raises(TaskPermissionError):
        delivery.confirm_loading(
            db, stranger, accurate_sj_id=SJ_ID, idempotency_key="stranger"
        )
