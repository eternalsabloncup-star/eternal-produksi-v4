"""M5 — Loading SJ task behaviour (existing workflow; tidak ubah PR #26)."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import loading_sj_bootstrap as boot
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED
from app.services.task_engine import TaskPermissionError
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_NC = "106319"
SJ_ID = "SJ-M5-LOAD"

HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["access.all_branches"],
}
OTHER_HELPER = {
    "email_normalized": "helper2@eternal.local",
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


def _env(monkeypatch: pytest.MonkeyPatch, **flags: str | bool) -> None:
    for k, v in flags.items():
        if v is False or v == "":
            monkeypatch.setenv(k, "0" if isinstance(v, bool) else "")
        elif v is True:
            monkeypatch.setenv(k, "1")
        else:
            monkeypatch.setenv(k, str(v))
    clear_settings_cache()


def _seed_po(db: Database) -> dict:
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


def _sj(*, qty: float = 5) -> dict:
    return {
        "id": SJ_ID,
        "number": "SJ/M5/001",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-20",
        "toAddress": "ALAMAT",
        "shipmentName": "OUTLET",
        "detailItem": [
            {
                "id": "SJL-N",
                "salesOrderDetailId": LINE_NC,
                "itemNo": "BRG-SIM-002",
                "quantity": qty,
                "itemUnitName": "PCS",
            }
        ],
    }


def _prepare(db: Database, monkeypatch: pytest.MonkeyPatch) -> dict:
    _env(
        monkeypatch,
        V4_LOADING_SJ_TASK_ENABLED=True,
        V4_LOADING_PACKING_LEDGER_ENFORCED=False,
    )
    _seed_po(db)
    # db fixture sudah full-seed; pastikan kontrak loading-sj valid.
    boot.verify_loading_sj_contract(db)
    return deliveries.upsert_delivery_note(db, _sj())["delivery_note"]


def test_one_sj_one_loading_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch)
    t1 = delivery.ensure_loading_sj_task(db, note)
    t2 = delivery.ensure_loading_sj_task(db, note)
    assert t1 is not None and t2 is not None
    assert t1["task_key"] == t2["task_key"]
    rows = tasks.list_tasks(db, task_type="loading-sj")
    assert len(rows) == 1


def test_resync_does_not_duplicate(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch)
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    # Resync SJ (revisi qty) lalu ensure lagi.
    again = deliveries.upsert_delivery_note(db, _sj(qty=7))["delivery_note"]
    delivery.ensure_loading_sj_task(db, again)
    rows = tasks.list_tasks(db, task_type="loading-sj")
    assert len(rows) == 1
    task = tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID))
    assert (task.get("context") or {}).get(
        "assigned_user_email"
    ) == "helper@eternal.local"


def test_assign_preserves_existing_assignee(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch)
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    # ensure ulang tanpa email tidak boleh menghapus assignee.
    delivery.ensure_loading_sj_task(db, note)
    task = tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID))
    assert (task.get("context") or {}).get(
        "assigned_user_email"
    ) == "helper@eternal.local"


def test_confirm_by_assignee_ok(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch)
    delivery.ensure_loading_sj_task(db, note)
    delivery.assign_loading_sj_task(
        db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
    )
    result = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m5-ok"
    )
    assert result["duplicate"] is False


def test_confirm_by_other_user_denied(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch)
    delivery.ensure_loading_sj_task(db, note)
    delivery.assign_loading_sj_task(
        db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
    )
    with pytest.raises(TaskPermissionError):
        delivery.confirm_loading(
            db,
            OTHER_HELPER,
            accurate_sj_id=SJ_ID,
            idempotency_key="m5-denied",
        )


def test_cross_branch_access_denied(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.object_auth import ObjectAuthError

    note = _prepare(db, monkeypatch)
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    db["delivery_notes"].update_one(
        {"accurate_sj_id": SJ_ID}, {"$set": {"branch_id": "150"}}
    )
    foreign = {
        **HELPER,
        "branch_ids": ["999"],
        "effective_module_permissions": [],
    }
    with pytest.raises(ObjectAuthError):
        delivery.confirm_loading(
            db, foreign, accurate_sj_id=SJ_ID, idempotency_key="m5-branch"
        )


def test_audit_history_on_confirm(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch)
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m5-audit"
    )
    assert db["loading_events"].count_documents({"accurate_sj_id": SJ_ID}) == 1
    history = deliveries.list_loading_events(db, SJ_ID)
    assert len(history) == 1
    assert len(tasks.list_tasks(db, task_type="loading-sj")) == 1


def test_sesuai_tidak_sesuai_paths(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch)
    delivery.ensure_loading_sj_task(
        db, note, assigned_user_email="helper@eternal.local"
    )
    # Sesuai → confirm_loading_match.
    matched = delivery.confirm_loading_match(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m5-sesuai"
    )
    assert matched["duplicate"] is False
    task = tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID))
    assert task is not None
    assert task.get("status") == tasks.TASK_STATUS_DONE

    # SJ kedua untuk jalur Tidak Sesuai.
    sj2 = "SJ-M5-MISMATCH"
    note2 = deliveries.upsert_delivery_note(
        db,
        {
            **_sj(qty=99),
            "id": sj2,
            "number": "SJ/M5/002",
        },
    )["delivery_note"]
    delivery.ensure_loading_sj_task(
        db, note2, assigned_user_email="helper@eternal.local"
    )
    mismatch = delivery.reject_loading_mismatch(
        db, HELPER, accurate_sj_id=sj2, reason="qty beda uji M5"
    )
    assert mismatch["discrepancy_task"]["task_type"] == "qty-loading-sj-berbeda"
    assert len(tasks.list_tasks(db, task_type="qty-loading-sj-berbeda")) >= 1
