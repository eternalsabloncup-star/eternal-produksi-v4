"""T1 Packing Non-Cetak — derive, confirm, activation boundary, D1 resync."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import po_tasks, production_service as prod
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_NC = "106319"  # Non-Cetak pada sample payload
LINE_CETAK = "106318"

HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["access.all_branches"],
}

ACTIVATION = "2026-11-20T10:00:00+00:00"


def _set_activation(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("V4_NON_CETAK_PACKING_ACTIVATED_AT", value)
    clear_settings_cache()


def _clear_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("V4_NON_CETAK_PACKING_ACTIVATED_AT", raising=False)
    monkeypatch.setenv("V4_NON_CETAK_PACKING_ACTIVATED_AT", "")
    clear_settings_cache()


def _seed_po(db: Database, *, accept: bool = True) -> dict:
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    if accept:
        mark_po_accepted(db)
    return get_by_accurate_id(db, PO_ID)


def _set_accepted_at(db: Database, when: datetime) -> None:
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {
            "$set": {
                "operational.acceptance.status": ACCEPTANCE_ACCEPTED,
                "operational.acceptance.accepted_at": when,
                "operational.inbox_status": "PO Diterima",
            }
        },
    )


def test_activation_unset_means_feature_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_activation(monkeypatch)
    assert po_tasks.non_cetak_packing_activation_at() is None


def test_pending_non_cetak_no_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db, accept=False)
    derived = po_tasks.derive_tasks_for_po(db, po)
    assert derived["non_cetak_packing_eligible"] is False
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC)) is None


def test_accepted_after_activation_creates_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    derived = po_tasks.derive_tasks_for_po(db, po)
    assert derived["non_cetak_packing_eligible"] is True
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert task is not None
    assert task["task_type"] == "qc-packing"
    assert task["assigned_role"] == "helper"
    assert float(task["qty_target"]) == 50  # sample Non-Cetak qty
    assert task["ref"].get("process_kind") == "Non-Cetak"
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_CETAK)) is None
    assert all(
        "line:106318" not in k or "qc-packing" not in k
        for k in derived["created_task_keys"]
    )


def test_accepted_before_activation_no_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC)) is None


def test_equal_activation_timestamp_eligible(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    assert po_tasks.is_non_cetak_packing_eligible(po) is True
    po_tasks.derive_tasks_for_po(db, po)
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC)) is not None


def test_resync_old_accepted_still_ineligible(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resync mengubah header tetapi accepted_at lama → tetap tidak eligible."""
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    old = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    _set_accepted_at(db, old)
    # Simulasi resync: ubah qty Non-Cetak, accepted_at tetap.
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["detailItem"][1]["quantity"] = 80
    upsert_sales_order(db, normalize_sales_order(payload))
    _set_accepted_at(db, old)  # pastikan tidak tertimpa
    po = get_by_accurate_id(db, PO_ID)
    assert po_tasks.is_non_cetak_packing_eligible(po) is False
    po_tasks.derive_tasks_for_po(db, po)
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC)) is None


def test_no_duplicate_task_on_rederive(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)
    po_tasks.derive_tasks_for_po(db, po)
    keys = [
        t["task_key"]
        for t in tasks.list_tasks(db, task_type="qc-packing", accurate_po_id=PO_ID)
        if t["ref"].get("accurate_line_id") == LINE_NC
    ]
    assert len(keys) == 1


def test_partial_then_complete_packing(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)

    # Naikkan target ke 5000 lewat update line qty untuk skenario Owner 3000/5000
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID, "header.items.accurate_line_id": LINE_NC},
        {"$set": {"header.items.$.qty": 5000}},
    )
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)

    first = prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_NC,
        qty=3000,
        idempotency_key="nc-pack-3000",
    )
    assert first["qty_ready_to_ship"] == 3000
    assert first["process_kind"] == "Non-Cetak"
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert float(task["qty_target"]) == 5000
    assert float(task["qty_confirmed"]) == 3000

    second = prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_NC,
        qty=2000,
        idempotency_key="nc-pack-2000",
    )
    assert second["qty_ready_to_ship"] == 5000
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert task["status"] == tasks.TASK_STATUS_DONE


def test_over_target_rejected(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)
    with pytest.raises(prod.ProductionQuantityError):
        prod.confirm_qc_packing(
            db,
            HELPER,
            accurate_po_id=PO_ID,
            accurate_line_id=LINE_NC,
            qty=9999,
            idempotency_key="nc-over",
        )
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert float(task["qty_confirmed"]) == 0


def test_idempotent_duplicate(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)
    a = prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_NC,
        qty=10,
        idempotency_key="nc-idem",
    )
    b = prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_NC,
        qty=10,
        idempotency_key="nc-idem",
    )
    assert a["qty_ready_to_ship"] == 10
    assert b.get("duplicate") is True or float(b["task"]["qty_confirmed"]) == 10
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert float(task["qty_confirmed"]) == 10


def test_concurrent_no_overshoot(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)
    # Target 50; dua konfirmasi 40 paralel → hanya satu yang boleh menang total ≤50
    errors: list[BaseException] = []

    def _go(key: str) -> None:
        try:
            prod.confirm_qc_packing(
                db,
                HELPER,
                accurate_po_id=PO_ID,
                accurate_line_id=LINE_NC,
                qty=40,
                idempotency_key=key,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(_go, ["nc-c1", "nc-c2"]))
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert float(task["qty_confirmed"]) <= 50
    assert float(task["qty_confirmed"]) in (40, 50)
    assert len(errors) >= 1


def test_d1_qty_down_keeps_history_blocks_extra(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_NC,
        qty=40,
        idempotency_key="nc-before-rev",
    )
    # Accurate qty turun ke 30 (< confirmed 40) — sync diterima
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["detailItem"][1]["quantity"] = 30
    upsert_sales_order(db, normalize_sales_order(payload))
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    # History tetap
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert float(task["qty_confirmed"]) == 40
    confs = tasks.list_confirmations(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert len(confs) == 1
    # Confirm tambahan ditolak
    with pytest.raises(prod.ProductionQuantityError):
        prod.confirm_qc_packing(
            db,
            HELPER,
            accurate_po_id=PO_ID,
            accurate_line_id=LINE_NC,
            qty=1,
            idempotency_key="nc-after-rev",
        )
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert float(task["qty_confirmed"]) == 40
    assert float(task.get("qty_consumed") or 0) == 0


def test_d1_qty_up_allows_remaining(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_activation(monkeypatch, ACTIVATION)
    po = _seed_po(db)
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    po = get_by_accurate_id(db, PO_ID)
    po_tasks.derive_tasks_for_po(db, po)
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_NC,
        qty=40,
        idempotency_key="nc-up-1",
    )
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["detailItem"][1]["quantity"] = 70
    upsert_sales_order(db, normalize_sales_order(payload))
    _set_accepted_at(db, datetime(2026, 11, 21, tzinfo=timezone.utc))
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_NC,
        qty=30,
        idempotency_key="nc-up-2",
    )
    task = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_NC))
    assert float(task["qty_confirmed"]) == 70
    assert float(task["qty_target"]) == 70


def test_invalid_activation_treated_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_activation(monkeypatch, "bukan-tanggal")
    assert po_tasks.non_cetak_packing_activation_at() is None
