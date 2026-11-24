"""M9 PR #63 safety hardening — archive CAS/txn + resume honesty."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.domain.operational import TaskRef, build_task_key
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import task_repository as tasks
from app.repositories.po_repository import upsert_sales_order
from app.services import delivery_service as delivery
from app.services import loading_sj_archive as archive
from app.services import loading_sj_bootstrap as boot
from app.services.object_auth import (
    filter_tasks_for_actor,
    is_actionable_open_task,
    task_source_po_accepted,
)
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, accept_po
from app.services.task_open_counts import count_actionable_open_by_role
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
PO_B = "95107"
LINE_NC = "106319"
SJ_ID = "SJ-M9-SAFE"

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


def _sj(*, sj_id: str = SJ_ID, po_id: str = PO_ID) -> dict:
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
                "quantity": 5,
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


def _seed_historical_task(db: Database, monkeypatch: pytest.MonkeyPatch) -> str:
    _env(monkeypatch)
    _seed_po(db, accepted=False)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    ref = TaskRef(
        accurate_po_id=PO_ID,
        accurate_delivery_note_id=SJ_ID,
        sj_no=str(note.get("sj_no") or ""),
    )
    tasks.upsert_task(
        db,
        task_type="loading-sj",
        scope="delivery_note",
        assigned_role="helper",
        ref=ref,
        label="Loading Surat Jalan",
        context={
            "accurate_po_ids": [PO_ID],
            "marker": "keep-me",
            "assigned_user_email": "helper@eternal.local",
        },
    )
    return build_task_key("loading-sj", "delivery_note", ref)


def test_archive_preserves_concurrent_context_fields(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _seed_historical_task(db, monkeypatch)
    # Concurrent context mutation on a sibling nested field.
    tasks.tasks_collection(db).update_one(
        {"task_key": key},
        {"$set": {"context.concurrent_flag": "alive"}},
    )
    out = archive.soft_archive_loading_sj_task(
        db,
        task_key=key,
        actor_email="owner@eternal.local",
        batch_id="m9-safe-batch",
    )
    assert out["duplicate"] is False
    task = tasks.get_task(db, key)
    assert archive.is_soft_archived(task)
    assert (task.get("context") or {}).get("marker") == "keep-me"
    assert (task.get("context") or {}).get("concurrent_flag") == "alive"
    assert (task.get("context") or {}).get("assigned_user_email") == (
        "helper@eternal.local"
    )


def test_concurrent_archive_attempts_idempotent(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _seed_historical_task(db, monkeypatch)

    def _archive() -> dict:
        return archive.soft_archive_loading_sj_task(
            db,
            task_key=key,
            actor_email="owner@eternal.local",
            batch_id="m9-safe-batch",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _archive(), range(2)))
    assert sum(1 for r in results if not r["duplicate"]) == 1
    assert sum(1 for r in results if r["duplicate"]) == 1
    assert (
        db["audit_logs"].count_documents({"action": "loading_sj.soft_archive"}) == 1
    )


def test_archive_rolls_back_when_audit_fails(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _seed_historical_task(db, monkeypatch)

    def _boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise RuntimeError("audit inject fail")

    monkeypatch.setattr(archive, "append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="audit inject fail"):
        archive.soft_archive_loading_sj_task(
            db,
            task_key=key,
            actor_email="owner@eternal.local",
            batch_id="m9-safe-batch",
        )
    assert archive.is_soft_archived(tasks.get_task(db, key)) is False
    assert db["audit_logs"].count_documents({"action": "loading_sj.soft_archive"}) == 0


def test_force_bypass_removed(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _seed_historical_task(db, monkeypatch)
    # Accept parent → becomes ineligible; force kwarg must not exist.
    mark_po_accepted(db, accurate_po_id=PO_ID)
    with pytest.raises(TypeError):
        archive.soft_archive_loading_sj_task(  # type: ignore[call-arg]
            db,
            task_key=key,
            actor_email="owner@eternal.local",
            batch_id="m9-safe-batch",
            force=True,
        )
    with pytest.raises(ValueError, match="not eligible"):
        archive.soft_archive_loading_sj_task(
            db,
            task_key=key,
            actor_email="owner@eternal.local",
            batch_id="m9-safe-batch",
        )


def test_restore_unaccepted_not_actionable(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _seed_historical_task(db, monkeypatch)
    archive.soft_archive_loading_sj_task(
        db,
        task_key=key,
        actor_email="owner@eternal.local",
        batch_id="m9-safe-batch",
    )
    archive.restore_loading_sj_task(
        db, task_key=key, actor_email="owner@eternal.local", batch_id="m9-safe-batch"
    )
    task = tasks.get_task(db, key)
    assert archive.is_soft_archived(task) is False
    assert task_source_po_accepted(db, task) is False
    assert filter_tasks_for_actor(db, HELPER, [task]) == []
    counts = count_actionable_open_by_role(db, HELPER)
    assert int(counts.get("helper") or 0) == 0


def test_archive_before_accept_then_counters(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _seed_historical_task(db, monkeypatch)
    archive.soft_archive_loading_sj_task(
        db,
        task_key=key,
        actor_email="owner@eternal.local",
        batch_id="m9-safe-batch",
    )
    mark_po_accepted(db, accurate_po_id=PO_ID)
    task = tasks.get_task(db, key)
    assert is_actionable_open_task(task) is False
    assert filter_tasks_for_actor(db, HELPER, [task]) == []
    assert int(count_actionable_open_by_role(db, HELPER).get("helper") or 0) == 0


def test_resume_failure_then_safe_retry(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=False)
    boot.verify_loading_sj_contract(db)
    deliveries.upsert_delivery_note(db, _sj())
    mark_po_accepted(db, accurate_po_id=PO_ID)

    calls = {"n": 0}
    real = delivery.ensure_loading_sj_task

    def _flaky(db_, note, **kwargs):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient ensure fail")
        return real(db_, note, **kwargs)

    monkeypatch.setattr(delivery, "ensure_loading_sj_task", _flaky)
    with pytest.raises(RuntimeError, match="transient"):
        delivery.resume_loading_sj_tasks_for_accepted_po(db, PO_ID)
    assert tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID)) is None

    monkeypatch.setattr(delivery, "ensure_loading_sj_task", real)
    second = delivery.resume_loading_sj_tasks_for_accepted_po(db, PO_ID)
    assert second["ok"] is True
    assert delivery.loading_sj_task_key(SJ_ID) in second["task_keys"]
    assert tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID)) is not None

    # accept_po path maps resume failure to ok=False without inventing keys
    monkeypatch.setattr(delivery, "ensure_loading_sj_task", _flaky)
    calls["n"] = 0
    from app.services import po_acceptance as po_acc

    out = {"accurate_po_id": PO_ID}
    po_acc._attach_loading_sj_resume(db, out, PO_ID)
    assert out["loading_sj_resume_ok"] is False
    assert out["loading_sj_resume_task_keys"] == []
    assert "loading_sj_resume_error" in out


def test_accept_resume_reports_deferred_multi_parent(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=False, po_id=PO_ID)
    _seed_po(db, accepted=False, po_id=PO_B)
    boot.verify_loading_sj_contract(db)
    deliveries.upsert_delivery_note(db, _sj())
    db["accurate_delivery_notes"].update_one(
        {"accurate_sj_id": SJ_ID},
        {"$set": {"accurate_po_ids": [PO_ID, PO_B], "is_multi_po": True}},
    )
    result = accept_po(db, HEAD, accurate_po_id=PO_ID)
    assert result["loading_sj_resume_ok"] is True
    assert result["loading_sj_resume_task_keys"] == []
    assert SJ_ID in (result.get("loading_sj_resume_deferred_sj_ids") or [])
    assert tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID)) is None

    # Accept second parent → resume creates.
    result2 = accept_po(db, HEAD, accurate_po_id=PO_B)
    assert result2["loading_sj_resume_ok"] is True
    assert delivery.loading_sj_task_key(SJ_ID) in result2["loading_sj_resume_task_keys"]


def test_ambiguous_parent_excluded_from_archive(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    boot.verify_loading_sj_contract(db)
    ref = TaskRef(accurate_delivery_note_id="SJ-NO-NOTE", sj_no="X")
    tasks.upsert_task(
        db,
        task_type="loading-sj",
        scope="delivery_note",
        assigned_role="helper",
        ref=ref,
        label="Loading Surat Jalan",
        context={},
    )
    key = build_task_key("loading-sj", "delivery_note", ref)
    verdict = archive.classify_loading_sj_archive_candidate(db, tasks.get_task(db, key))
    assert verdict["eligible"] is False
    assert archive.CLASS_AMBIGUOUS in verdict["exclude_reasons"]


def test_reimport_does_not_unarchive(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _seed_historical_task(db, monkeypatch)
    note = deliveries.get_delivery_note(db, SJ_ID)
    archive.soft_archive_loading_sj_task(
        db,
        task_key=key,
        actor_email="owner@eternal.local",
        batch_id="m9-safe-batch",
    )
    mark_po_accepted(db, accurate_po_id=PO_ID)
    out = delivery.ensure_loading_sj_task(db, note)
    assert archive.is_soft_archived(out)
    assert archive.is_soft_archived(tasks.get_task(db, key))
