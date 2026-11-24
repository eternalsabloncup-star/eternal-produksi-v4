"""M8.1 Loading SJ Integrity — focused matrix T01–T20.

Preserves existing mismatch/coordination workflow; does not invent new policies.
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
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
from app.services.object_auth import (
    ObjectAuthError,
    filter_tasks_for_actor,
    task_source_po_accepted,
)
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, PoAcceptanceError
from app.services.task_engine import TaskPermissionError
from app.services.task_open_counts import count_actionable_open_by_role
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_NC = "106319"
SJ_ID = "SJ-M81-LOAD"

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
    "effective_module_permissions": ["access.all_branches", "operations.full"],
}
FOREIGN = {
    "email_normalized": "foreign@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "branch_ids": ["999"],
    "effective_module_permissions": [],
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


def _sj(*, qty: float = 5, sj_id: str = SJ_ID, po_id: str = PO_ID) -> dict:
    return {
        "id": sj_id,
        "number": f"SJ/M81/{sj_id}",
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


def _seed_po(db: Database, *, accepted: bool) -> dict:
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    if accepted:
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
    else:
        db["accurate_sales_orders"].update_one(
            {"accurate_id": PO_ID},
            {"$set": {"operational.acceptance.status": "pending"}},
        )
    return get_by_accurate_id(db, PO_ID)


def _prepare(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    *,
    accepted: bool = True,
    qty: float = 5,
) -> dict:
    _env(
        monkeypatch,
        V4_LOADING_SJ_TASK_ENABLED=True,
        V4_LOADING_PACKING_LEDGER_ENFORCED=False,
    )
    _seed_po(db, accepted=accepted)
    boot.verify_loading_sj_contract(db)
    return deliveries.upsert_delivery_note(db, _sj(qty=qty))["delivery_note"]


def _stub_loading_ok(monkeypatch: pytest.MonkeyPatch, note: dict) -> None:
    items = note.get("items") or []
    line0 = items[0] if items else {"accurate_line_id": LINE_NC, "qty": 5, "item_code": "X"}
    monkeypatch.setattr(
        delivery,
        "validate_loading",
        lambda *_a, **_k: {
            "all_matched": True,
            "items": [
                {
                    "sj_line_id": str(line0.get("sj_line_id") or "SJL-N"),
                    "accurate_line_id": str(line0.get("accurate_line_id") or LINE_NC),
                    "item_code": str(line0.get("item_code") or "BRG"),
                    "qty": float(line0.get("qty") or 5),
                    "unit": str(line0.get("unit") or "PCS"),
                    "process_kind": "Non-Cetak",
                    "ledger_mode": "legacy_non_cetak",
                    "matched": True,
                    "qty_available": float(line0.get("qty") or 5),
                    "accurate_po_id": PO_ID,
                }
            ],
        },
    )


# --- T01 / T02 / T20: non-accepted visibility + counts + legacy store -------


def test_t01_t02_t20_non_accepted_hidden_and_excluded(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=False)
    task = delivery.ensure_loading_sj_task(db, note)
    assert task is not None
    assert task_source_po_accepted(db, task) is False
    visible = filter_tasks_for_actor(db, HELPER, [task])
    assert visible == []
    counts = count_actionable_open_by_role(db, HELPER)
    assert int(counts.get("helper") or 0) == 0


def test_t03_accepted_task_visible(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    task = delivery.ensure_loading_sj_task(db, note)
    assert task is not None
    assert task_source_po_accepted(db, task) is True
    visible = filter_tasks_for_actor(db, HELPER, [task])
    assert len(visible) == 1


def test_t04_t05_branch_and_unauthorized_loading(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    db["accurate_delivery_notes"].update_one(
        {"accurate_sj_id": SJ_ID}, {"$set": {"branch_id": "150"}}
    )
    _stub_loading_ok(monkeypatch, note)
    with pytest.raises(ObjectAuthError):
        delivery.confirm_loading(
            db, FOREIGN, accurate_sj_id=SJ_ID, idempotency_key="m81-branch"
        )


def test_t05_unauthorized_role_rejected(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    _stub_loading_ok(monkeypatch, note)
    stranger = {
        "email_normalized": "stranger@eternal.local",
        "role": "user",
        "operational_role": "screener",
        "effective_module_permissions": ["access.all_branches"],
    }
    with pytest.raises(TaskPermissionError):
        delivery.confirm_loading(
            db, stranger, accurate_sj_id=SJ_ID, idempotency_key="m81-role"
        )


def test_t06_missing_po_parent_fail_closed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True, V4_LOADING_PACKING_LEDGER_ENFORCED=False)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    with pytest.raises(LookupError, match="tidak ditemukan"):
        delivery._require_accepted_po_for_note(db, note)


def test_t07_missing_sj_reference_fail_closed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare(db, monkeypatch, accepted=True)
    with pytest.raises(LookupError, match="tidak ditemukan"):
        delivery.confirm_loading(
            db, HELPER, accurate_sj_id="SJ-MISSING", idempotency_key="m81-miss"
        )


def test_t08_t09_sj_ingest_and_task_idempotent(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    t1 = delivery.ensure_loading_sj_task(db, note)
    t2 = delivery.ensure_loading_sj_task(db, note)
    assert t1 is not None and t2 is not None
    assert t1["task_key"] == t2["task_key"]
    assert len(tasks.list_tasks(db, task_type="loading-sj")) == 1
    # Re-upsert same SJ payload does not duplicate loading-sj.
    deliveries.upsert_delivery_note(db, _sj())
    delivery.ensure_loading_sj_task(db, deliveries.get_delivery_note(db, SJ_ID) or note)
    assert len(tasks.list_tasks(db, task_type="loading-sj")) == 1


def test_t10_quantity_match_confirm(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    _stub_loading_ok(monkeypatch, note)
    out = delivery.confirm_loading_match(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-match"
    )
    assert out["duplicate"] is False
    assert deliveries.get_loading_event(db, SJ_ID) is not None
    task = tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID))
    assert task is not None
    assert task["status"] == tasks.TASK_STATUS_DONE


def test_t11_t12_quantity_mismatch_and_coordination(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True, qty=5)
    delivery.ensure_loading_sj_task(db, note)
    monkeypatch.setattr(
        delivery,
        "validate_loading",
        lambda *_a, **_k: {
            "all_matched": False,
            "items": [
                {
                    "sj_line_id": "SJL-N",
                    "accurate_line_id": LINE_NC,
                    "item_code": "BRG-SIM-002",
                    "item_name": "Item Uji",
                    "qty": 5.0,
                    "qty_available": 1.0,
                    "matched": False,
                    "reason": "Qty QC & Packing tersedia 1, Qty SJ 5.",
                    "unit": "PCS",
                    "process_kind": "Cetak",
                    "accurate_po_id": PO_ID,
                }
            ],
        },
    )
    with pytest.raises(delivery.LoadingValidationError, match="tidak cocok"):
        delivery.confirm_loading(
            db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-bad"
        )
    assert deliveries.get_loading_event(db, SJ_ID) is None

    res = delivery.reject_loading_mismatch(
        db, HELPER, accurate_sj_id=SJ_ID, reason="qty beda"
    )
    disc = res["discrepancy_task"]
    assert disc["task_type"] == "qty-loading-sj-berbeda"
    items = (disc.get("context") or {}).get("mismatched_items") or []
    assert len(items) == 1
    assert float(items[0]["qty_sj"]) == 5.0
    assert float(items[0]["qty_available"]) == 1.0
    assert res["duplicate"] is False

    # T12 — coordination confirm closes discrepancy without opening delivery.
    coord = delivery.confirm_coordination(
        db,
        HEAD,
        task_key=disc["task_key"],
        idempotency_key="m81-coord",
    )
    assert coord["duplicate"] is False
    done = tasks.get_task(db, disc["task_key"])
    assert done is not None and done["status"] == tasks.TASK_STATUS_DONE
    assert deliveries.get_loading_event(db, SJ_ID) is None
    assert deliveries.list_delivery_events(db, SJ_ID) == []


def test_t13_t14_duplicate_and_concurrent_confirm(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    _stub_loading_ok(monkeypatch, note)
    first = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-a"
    )
    second = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-b"
    )
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert db["loading_events"].count_documents({"accurate_sj_id": SJ_ID}) == 1

    # Concurrent path: second confirm after race uses unique index / duplicate True.
    note2 = deliveries.upsert_delivery_note(db, _sj(sj_id="SJ-M81-RACE"))["delivery_note"]
    delivery.ensure_loading_sj_task(db, note2)
    _stub_loading_ok(monkeypatch, note2)

    def _once(i: int) -> bool:
        out = delivery.confirm_loading(
            db,
            HELPER,
            accurate_sj_id="SJ-M81-RACE",
            idempotency_key=f"m81-race-{i}",
        )
        return bool(out.get("duplicate"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        flags = list(pool.map(_once, range(4)))
    assert flags.count(False) == 1
    assert flags.count(True) == 3
    assert db["loading_events"].count_documents({"accurate_sj_id": "SJ-M81-RACE"}) == 1


def test_t15_partial_sj_supported(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial qty SJ (subset of packing) remains valid when matched."""
    note = _prepare(db, monkeypatch, accepted=True, qty=2)
    delivery.ensure_loading_sj_task(db, note)
    _stub_loading_ok(monkeypatch, note)
    out = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-partial"
    )
    assert out["duplicate"] is False
    loading = deliveries.get_loading_event(db, SJ_ID)
    assert loading is not None
    assert float(loading["items"][0]["qty"]) == 2.0


def test_t16_t17_delivery_task_once_on_loading(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    _stub_loading_ok(monkeypatch, note)
    out = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-drv"
    )
    assert out["duplicate"] is False
    drivers = tasks.list_tasks(db, task_type="pengiriman")
    assert len(drivers) == 1
    # Retry confirm must not create a second driver task.
    out2 = delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-drv-2"
    )
    assert out2["duplicate"] is True
    assert len(tasks.list_tasks(db, task_type="pengiriman")) == 1


def test_t18_audit_evidence_preserved(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    _stub_loading_ok(monkeypatch, note)
    delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-audit"
    )
    assert (
        db["audit_logs"].count_documents({"action": "delivery.loading.confirm"}) >= 1
    )


def test_t19_design_sample_gates_untouched_by_loading_path(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading confirm must not mutate design/payment gate documents."""
    from app.repositories import gate_repository as gates

    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    before = gates.get_gates(db, PO_ID)
    _stub_loading_ok(monkeypatch, note)
    delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-gates"
    )
    after = gates.get_gates(db, PO_ID)
    assert before.get("design") == after.get("design")
    assert before.get("payment") == after.get("payment")


def test_assign_blocked_before_po_accepted(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=False)
    delivery.ensure_loading_sj_task(db, note)
    with pytest.raises(PoAcceptanceError, match="belum diterima"):
        delivery.assign_loading_sj_task(
            db,
            HEAD,
            accurate_sj_id=SJ_ID,
            assigned_user_email="helper@eternal.local",
        )
    task = tasks.get_task(db, delivery.loading_sj_task_key(SJ_ID))
    assert task is not None
    assert not (task.get("context") or {}).get("assigned_user_email")


def test_confirm_duplicate_still_requires_branch(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    _stub_loading_ok(monkeypatch, note)
    delivery.confirm_loading(
        db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m81-dup0"
    )
    db["accurate_delivery_notes"].update_one(
        {"accurate_sj_id": SJ_ID}, {"$set": {"branch_id": "150"}}
    )
    with pytest.raises(ObjectAuthError):
        delivery.confirm_loading(
            db, FOREIGN, accurate_sj_id=SJ_ID, idempotency_key="m81-dup1"
        )


def test_mismatch_retry_reports_duplicate(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _prepare(db, monkeypatch, accepted=True)
    delivery.ensure_loading_sj_task(db, note)
    monkeypatch.setattr(
        delivery,
        "validate_loading",
        lambda *_a, **_k: {
            "all_matched": False,
            "items": [
                {
                    "sj_line_id": "SJL-N",
                    "accurate_line_id": LINE_NC,
                    "item_code": "BRG",
                    "qty": 5.0,
                    "qty_available": 0.0,
                    "matched": False,
                    "reason": "kurang",
                    "unit": "PCS",
                    "process_kind": "Cetak",
                    "accurate_po_id": PO_ID,
                }
            ],
        },
    )
    first = delivery.reject_loading_mismatch(db, HELPER, accurate_sj_id=SJ_ID)
    second = delivery.reject_loading_mismatch(db, HELPER, accurate_sj_id=SJ_ID)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert (
        db["operational_tasks"].count_documents(
            {"task_type": "qty-loading-sj-berbeda"}
        )
        == 1
    )
