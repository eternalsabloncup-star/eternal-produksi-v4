"""M8 Loading-SJ hygiene — visibility/open-counts stay fail-closed on non-accepted PO.

Does not mutate production. Does not delete historic tasks.
"""

from __future__ import annotations

from pymongo.database import Database

from app.domain.operational import TaskRef, build_task_key
from app.repositories import task_repository as tasks
from app.services.object_auth import filter_tasks_for_actor, task_source_po_accepted
from app.services.task_open_counts import count_actionable_open_by_role


def _user(role: str = "helper") -> dict:
    return {
        "email_normalized": "helper@eternal.local",
        "role": "user",
        "operational_role": role,
        "is_active": True,
        "branch_ids": [],
        "effective_module_permissions": ["operations.full"],
    }


def test_loading_sj_on_non_accepted_po_hidden_and_excluded_from_counts(
    db: Database,
) -> None:
    # PO present but NOT accepted.
    db["accurate_sales_orders"].insert_one(
        {
            "accurate_id": "po-non-acc-1",
            "po_no": "PO-NA-1",
            "header": {"items": []},
            "operational": {"acceptance": {"status": "pending"}},
        }
    )
    db["accurate_delivery_notes"].insert_one(
        {
            "accurate_sj_id": "sj-na-1",
            "sj_no": "SJ-NA-1",
            "accurate_po_id": "po-non-acc-1",
            "accurate_po_ids": ["po-non-acc-1"],
        }
    )
    ref = TaskRef(accurate_delivery_note_id="sj-na-1", sj_no="SJ-NA-1")
    tasks.upsert_task(
        db,
        task_type="loading-sj",
        scope="delivery_note",
        assigned_role="helper",
        ref=ref,
        label="Loading Surat Jalan",
        context={"accurate_po_ids": ["po-non-acc-1"]},
    )
    task = tasks.get_task(db, build_task_key("loading-sj", "delivery_note", ref))
    assert task is not None
    assert task_source_po_accepted(db, task) is False

    visible = filter_tasks_for_actor(db, _user(), [task])
    assert visible == []

    counts = count_actionable_open_by_role(db, _user())
    # loading-sj (helper) must not inflate open counts for non-accepted parents.
    assert int(counts.get("helper") or 0) == 0


def test_loading_sj_becomes_visible_after_acceptance(db: Database) -> None:
    db["accurate_sales_orders"].insert_one(
        {
            "accurate_id": "po-acc-1",
            "po_no": "PO-A-1",
            "header": {"items": []},
            "operational": {"acceptance": {"status": "pending"}},
        }
    )
    db["accurate_delivery_notes"].insert_one(
        {
            "accurate_sj_id": "sj-a-1",
            "sj_no": "SJ-A-1",
            "accurate_po_id": "po-acc-1",
            "accurate_po_ids": ["po-acc-1"],
        }
    )
    tasks.upsert_task(
        db,
        task_type="loading-sj",
        scope="delivery_note",
        assigned_role="helper",
        ref=TaskRef(accurate_delivery_note_id="sj-a-1", sj_no="SJ-A-1"),
        label="Loading Surat Jalan",
        context={"accurate_po_ids": ["po-acc-1"]},
    )
    task = tasks.get_task(
        db,
        build_task_key(
            "loading-sj",
            "delivery_note",
            TaskRef(accurate_delivery_note_id="sj-a-1", sj_no="SJ-A-1"),
        ),
    )
    assert task_source_po_accepted(db, task) is False

    db["accurate_sales_orders"].update_one(
        {"accurate_id": "po-acc-1"},
        {"$set": {"operational.acceptance.status": "accepted"}},
    )
    assert task_source_po_accepted(db, task) is True
    visible = filter_tasks_for_actor(db, _user(), [task])
    assert len(visible) == 1
