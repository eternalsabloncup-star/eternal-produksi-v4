"""M9 consolidated — F08 create-gate + acceptance resume + soft-archive contract."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.domain.operational import TaskRef, build_task_key
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import loading_sj_archive as archive
from app.services import loading_sj_bootstrap as boot
from app.services.object_auth import filter_tasks_for_actor, is_actionable_open_task
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, accept_po
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
PO_B = "95107"
LINE_NC = "106319"
SJ_ID = "SJ-M9-F08"

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
    "effective_module_permissions": ["access.all_branches", "operations.full"],
}


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_LOADING_SJ_TASK_ENABLED", "1")
    monkeypatch.setenv("V4_LOADING_PACKING_LEDGER_ENFORCED", "0")
    clear_settings_cache()


def _sj(*, sj_id: str = SJ_ID, po_id: str = PO_ID, qty: float = 5) -> dict:
    return {
        "id": sj_id,
        "number": f"SJ/M9/{sj_id}",
        "salesOrderId": po_id,
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


def _seed_po(db: Database, *, accepted: bool, po_id: str = PO_ID) -> None:
    if po_id == PO_ID:
        upsert_sales_order(
            db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE))
        )
    else:
        # Minimal second parent for multi-PO gate tests (avoid clobbering sample id).
        db["accurate_sales_orders"].insert_one(
            {
                "accurate_id": str(po_id),
                "po_no": f"PO/M9/{po_id}",
                "branch_id": "50",
                "header": {"items": []},
                "operational": {},
                "origin": "accurate",
            }
        )
    if accepted:
        mark_po_accepted(db, accurate_po_id=str(po_id))
    else:
        db["accurate_sales_orders"].update_one(
            {"accurate_id": str(po_id)},
            {"$set": {"operational.acceptance.status": "pending"}},
        )


def test_f08_ensure_defers_until_accepted(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=False)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    assert delivery.ensure_loading_sj_task(db, note) is None
    assert tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID)) is None

    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"operational.acceptance.status": ACCEPTANCE_ACCEPTED}},
    )
    task = delivery.ensure_loading_sj_task(db, note)
    assert task is not None
    assert task["task_key"] == delivery.loading_sj_task_key(SJ_ID)
    # idempotent
    assert delivery.ensure_loading_sj_task(db, note)["task_key"] == task["task_key"]


def test_f08_multi_parent_requires_all_accepted(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=True, po_id=PO_ID)
    _seed_po(db, accepted=False, po_id=PO_B)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    db["accurate_delivery_notes"].update_one(
        {"accurate_sj_id": SJ_ID},
        {
            "$set": {
                "accurate_po_ids": [PO_ID, PO_B],
                "is_multi_po": True,
            }
        },
    )
    note = deliveries.get_delivery_note(db, SJ_ID)
    assert note is not None
    assert delivery.note_all_parent_pos_accepted(db, note) is False
    assert delivery.ensure_loading_sj_task(db, note) is None

    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_B},
        {"$set": {"operational.acceptance.status": ACCEPTANCE_ACCEPTED}},
    )
    note = deliveries.get_delivery_note(db, SJ_ID)
    assert delivery.note_all_parent_pos_accepted(db, note) is True
    assert delivery.ensure_loading_sj_task(db, note) is not None


def test_f08_accept_po_resumes_deferred_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=False)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    assert delivery.ensure_loading_sj_task(db, note) is None

    # accept via service (uses operational.acceptance SoT)
    result = accept_po(db, HEAD, accurate_po_id=PO_ID)
    assert result.get("acceptance", {}).get("status") == ACCEPTANCE_ACCEPTED
    keys = result.get("loading_sj_resume_task_keys") or []
    assert delivery.loading_sj_task_key(SJ_ID) in keys
    assert tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID)) is not None


def test_soft_archive_restore_visibility(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=False)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    # Seed historical orphan task (pre-F08)
    tasks.upsert_task(
        db,
        task_type="loading-sj",
        scope="delivery_note",
        assigned_role="helper",
        ref=TaskRef(
            accurate_po_id=PO_ID,
            accurate_delivery_note_id=SJ_ID,
            sj_no=str(note.get("sj_no") or ""),
        ),
        label="Loading Surat Jalan",
        context={"accurate_po_ids": [PO_ID]},
    )
    key = build_task_key(
        "loading-sj",
        "delivery_note",
        TaskRef(
            accurate_po_id=PO_ID,
            accurate_delivery_note_id=SJ_ID,
            sj_no=str(note.get("sj_no") or ""),
        ),
    )
    task = tasks.get_task(db, key)
    assert task is not None
    verdict = archive.classify_loading_sj_archive_candidate(db, task)
    assert verdict["eligible"] is True

    archived = archive.soft_archive_loading_sj_task(
        db,
        task_key=key,
        actor_email="owner@eternal.local",
        batch_id="m9-dry-test-batch",
    )
    assert archived["duplicate"] is False
    task2 = tasks.get_task(db, key)
    assert archive.is_soft_archived(task2)
    assert is_actionable_open_task(task2) is False
    assert filter_tasks_for_actor(db, HELPER, [task2]) == []

    # ensure must not unarchive on reimport
    assert delivery.ensure_loading_sj_task(db, note) is not None
    assert archive.is_soft_archived(tasks.get_task(db, key))

    restored = archive.restore_loading_sj_task(
        db, task_key=key, actor_email="owner@eternal.local", batch_id="m9-dry-test-batch"
    )
    assert restored["duplicate"] is False
    assert archive.is_soft_archived(tasks.get_task(db, key)) is False

    dry = archive.soft_archive_batch_dry_run(db, batch_id="m9-dry-test-batch")
    assert dry["mode"] == "DRY_RUN"
    assert dry["apply_status"] == "NOT_EXECUTED"
