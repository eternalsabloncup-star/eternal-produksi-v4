"""Preliminary Loading setelah Cetak + fix ingest SJ first-insert."""

from __future__ import annotations

import copy

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import gate_repository as gates
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import production_service as prod
from app.services import scheduling_service
from app.services.task_engine import TaskPermissionError, confirm_task
from tests.po_accept_helpers import mark_po_accepted
from tests.sample_flow_helpers import approve_sample

PO_ID = "95106"
LINE_PRINT = "106318"
SJ_ID = "SJ-PRELIM-1"
SJ_ID_B = "SJ-PRELIM-2"

HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches"],
}
HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
    "effective_module_permissions": ["access.all_branches"],
}
SCREENER = {
    "email_normalized": "screener@eternal.local",
    "role": "user",
    "operational_role": "screener",
    "effective_module_permissions": ["access.all_branches"],
}
OPERATOR = {
    "email_normalized": "op1@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": ["access.all_branches"],
}
OPERATOR2 = {
    "email_normalized": "op2@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": ["access.all_branches"],
}
OWNER = {
    "email_normalized": "owner@eternal.local",
    "role": "super-admin",
    "operational_role": "production-head",
    "effective_module_permissions": ["access.all_branches", "operations.full"],
}


def _env(monkeypatch: pytest.MonkeyPatch, **flags: bool) -> None:
    for k, v in flags.items():
        monkeypatch.setenv(k, "1" if v else "0")
    clear_settings_cache()


def _seed_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)))
    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji"
        )
    return get_by_accurate_id(db, PO_ID)


def _sj(sj_id: str = SJ_ID, qty: float = 200) -> dict:
    return {
        "id": sj_id,
        "number": f"SJ/{sj_id}",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-20",
        "toAddress": "ALAMAT",
        "shipmentName": "OUTLET",
        "shipDate": "01/12/2026",
        "detailItem": [
            {
                "id": f"SJL-{sj_id}",
                "salesOrderDetailId": LINE_PRINT,
                "itemNo": "BRG-SIM-001",
                "detailName": "Item Cetak",
                "quantity": qty,
                "itemUnitName": "PCS",
            }
        ],
    }


def _ready_print(
    db: Database, *, alloc_qty: float = 1000, prep_qty: float = 500
) -> str:
    po_doc = _seed_po(db)
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_PRINT,
        allocations=[{"operator_email": "op1@eternal.local", "qty": alloc_qty}],
        idempotency_key="prelim-sched-1",
    )
    alloc = res["allocations"][0]["allocation_id"]
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=prep_qty,
        idempotency_key="prelim-prep",
    )
    prod.confirm_screen(
        db, SCREENER, allocation_id=alloc, qty=50, idempotency_key="prelim-screen"
    )
    approve_sample(db, OPERATOR, HEAD, alloc)
    return alloc


def test_print_creates_preliminary_loading(db: Database) -> None:
    alloc = _ready_print(db, prep_qty=300)
    result = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=200, idempotency_key="print-1"
    )
    key = prod.preliminary_loading_task_key(PO_ID, LINE_PRINT)
    assert result.get("preliminary_loading_task_key") == key
    task = tasks.get_task(db, key)
    assert task is not None
    assert task["task_type"] == "loading-menunggu-sj"
    assert task["scope"] == "po_line"
    assert task["qty_target"] == 200
    assert task["context"]["waiting_for_sj"] is True
    assert task["context"]["display_status"] == "Menunggu Surat Jalan"
    assert task["context"]["qty_printed"] == 200
    assert db["loading_events"].count_documents({}) == 0
    assert db["delivery_events"].count_documents({}) == 0


def test_partial_print_stable_one_preliminary(db: Database) -> None:
    alloc = _ready_print(db, prep_qty=500)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="p-a"
    )
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=150, idempotency_key="p-b"
    )
    rows = tasks.list_tasks(db, task_type="loading-menunggu-sj")
    assert len(rows) == 1
    assert rows[0]["qty_target"] == 250
    assert rows[0]["context"]["qty_printed"] == 250


def test_print_retry_idempotent_no_duplicate(db: Database) -> None:
    alloc = _ready_print(db, prep_qty=200)
    first = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="same-key"
    )
    second = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="same-key"
    )
    assert first.get("duplicate") is not True
    assert second.get("duplicate") is True
    assert len(tasks.list_tasks(db, task_type="loading-menunggu-sj")) == 1


def test_multi_operator_aggregates_qty_one_task(db: Database) -> None:
    po_doc = _seed_po(db)
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_PRINT,
        allocations=[
            {"operator_email": "op1@eternal.local", "qty": 400},
            {"operator_email": "op2@eternal.local", "qty": 400},
        ],
        idempotency_key="multi-op-sched",
    )
    a1 = res["allocations"][0]["allocation_id"]
    a2 = res["allocations"][1]["allocation_id"]
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=400,
        idempotency_key="multi-prep",
    )
    for alloc, op, screen_key, sample_op in (
        (a1, OPERATOR, "scr-1", OPERATOR),
        (a2, OPERATOR2, "scr-2", OPERATOR2),
    ):
        prod.confirm_screen(
            db, SCREENER, allocation_id=alloc, qty=50, idempotency_key=screen_key
        )
        approve_sample(db, sample_op, HEAD, alloc)
    prod.confirm_print(
        db, OPERATOR, allocation_id=a1, qty=100, idempotency_key="op1-print"
    )
    prod.confirm_print(
        db, OPERATOR2, allocation_id=a2, qty=120, idempotency_key="op2-print"
    )
    rows = tasks.list_tasks(db, task_type="loading-menunggu-sj")
    assert len(rows) == 1
    assert rows[0]["qty_target"] == 220


def test_no_sj_blocks_actual_loading_and_preliminary_confirm(db: Database) -> None:
    alloc = _ready_print(db, prep_qty=200)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="block-print"
    )
    key = prod.preliminary_loading_task_key(PO_ID, LINE_PRINT)
    with pytest.raises(TaskPermissionError, match="informatif"):
        confirm_task(
            db,
            OWNER,
            task_key=key,
            qty=100,
            idempotency_key="fake-confirm",
        )
    with pytest.raises(LookupError):
        delivery.confirm_loading(
            db, HELPER, accurate_sj_id="SJ-DOES-NOT-EXIST", idempotency_key="x"
        )


def test_sj_first_insert_creates_loading_sj(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    r1 = delivery.ingest_delivery_note(db, OWNER, _sj(qty=100))
    assert r1["was_insert"] is True
    assert r1["source_changed"] is False
    assert r1.get("loading_task_key") == f"task:loading-sj:sj:{SJ_ID}"
    rows = tasks.list_tasks(db, task_type="loading-sj")
    assert len(rows) == 1
    assert rows[0]["task_key"] == f"task:loading-sj:sj:{SJ_ID}"


def test_sj_identical_replay_no_duplicate(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    delivery.ingest_delivery_note(db, OWNER, _sj(qty=100))
    r2 = delivery.ingest_delivery_note(db, OWNER, _sj(qty=100))
    assert r2["was_insert"] is False
    assert r2["source_changed"] is False
    assert r2.get("loading_task_key") is None
    assert len(tasks.list_tasks(db, task_type="loading-sj")) == 1


def test_sj_revision_stable_identity(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    delivery.ingest_delivery_note(db, OWNER, _sj(qty=100))
    r3 = delivery.ingest_delivery_note(db, OWNER, _sj(qty=150))
    assert r3["source_changed"] is True
    assert r3.get("loading_task_key") == f"task:loading-sj:sj:{SJ_ID}"
    assert len(tasks.list_tasks(db, task_type="loading-sj")) == 1


def test_multi_sj_independent_tasks(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    _seed_po(db)
    delivery.ingest_delivery_note(db, OWNER, _sj(SJ_ID, 80))
    delivery.ingest_delivery_note(db, OWNER, _sj(SJ_ID_B, 90))
    keys = {t["task_key"] for t in tasks.list_tasks(db, task_type="loading-sj")}
    assert keys == {
        f"task:loading-sj:sj:{SJ_ID}",
        f"task:loading-sj:sj:{SJ_ID_B}",
    }


def test_sj_links_preliminary_by_line_id(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print-before-SJ: menunggu → tautan → selesai informatif."""
    from app.services.object_auth import is_actionable_open_task

    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    alloc = _ready_print(db, prep_qty=200)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="link-print"
    )
    prelim = tasks.get_task(db, prod.preliminary_loading_task_key(PO_ID, LINE_PRINT))
    assert prelim is not None
    assert prelim["context"]["waiting_for_sj"] is True
    assert prelim["status"] == tasks.TASK_STATUS_OPEN
    assert is_actionable_open_task(prelim) is True

    delivery.ingest_delivery_note(db, OWNER, _sj(qty=50))
    prelim2 = tasks.get_task(db, prod.preliminary_loading_task_key(PO_ID, LINE_PRINT))
    assert prelim2 is not None
    assert prelim2["context"]["waiting_for_sj"] is False
    assert prelim2["context"]["loading_task_ready"] is True
    assert SJ_ID in (prelim2["context"].get("linked_sj_ids") or [])
    assert prelim2["context"].get("association") == "line_id_match"
    assert prelim2["status"] == tasks.TASK_STATUS_DONE
    assert is_actionable_open_task(prelim2) is False
    # Actionable confirm remains SJ-scoped only.
    assert len(tasks.list_tasks(db, task_type="loading-sj")) == 1
    loading = tasks.list_tasks(db, task_type="loading-sj")[0]
    assert loading["status"] != tasks.TASK_STATUS_DONE


def test_sj_before_print_associates_immediately(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.object_auth import is_actionable_open_task

    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    delivery.ingest_delivery_note(db, OWNER, _sj(qty=80))
    alloc = _ready_print(db, prep_qty=200)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="sj-first-print"
    )
    prelim = tasks.get_task(db, prod.preliminary_loading_task_key(PO_ID, LINE_PRINT))
    assert prelim is not None
    assert prelim["context"]["waiting_for_sj"] is False
    assert prelim["context"]["loading_task_ready"] is True
    assert SJ_ID in (prelim["context"].get("linked_sj_ids") or [])
    assert prelim["status"] == tasks.TASK_STATUS_DONE
    assert is_actionable_open_task(prelim) is False
    assert len(tasks.list_tasks(db, task_type="loading-sj")) == 1


def test_sj_exists_without_loading_task_keeps_preliminary_visible(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Historical SJ dokumen tanpa loading-sj → preliminary tetap visible."""
    from app.services.object_auth import is_actionable_open_task

    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=False)
    _seed_po(db)
    # Insert SJ without creating loading-sj (flag off).
    delivery.ingest_delivery_note(db, OWNER, _sj(qty=80))
    assert tasks.list_tasks(db, task_type="loading-sj") == []
    assert deliveries.get_delivery_note(db, SJ_ID) is not None

    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    alloc = _ready_print(db, prep_qty=200)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="hist-sj-print"
    )
    prelim = tasks.get_task(db, prod.preliminary_loading_task_key(PO_ID, LINE_PRINT))
    assert prelim is not None
    assert prelim["context"]["waiting_for_sj"] is False
    assert prelim["context"]["sj_exists"] is True
    assert prelim["context"]["loading_task_ready"] is False
    assert SJ_ID in (prelim["context"].get("missing_loading_sj_ids") or [])
    assert prelim["status"] == tasks.TASK_STATUS_OPEN
    assert is_actionable_open_task(prelim) is True
    assert "belum siap" in str(prelim["context"].get("display_status") or "").lower()


def test_multi_sj_one_loading_missing_keeps_visible(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.object_auth import is_actionable_open_task

    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    alloc = _ready_print(db, prep_qty=300)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=200, idempotency_key="partial-ready-print"
    )
    # First SJ creates loading-sj.
    delivery.ingest_delivery_note(db, OWNER, _sj(SJ_ID, 50))
    # Second SJ document without loading-sj (direct upsert, no ingest ensure).
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=False)
    deliveries.upsert_delivery_note(db, _sj(SJ_ID_B, 60))
    assert tasks.get_task(db, f"task:loading-sj:sj:{SJ_ID_B}") is None

    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    # Re-sync preliminary via another print partial.
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=10, idempotency_key="partial-ready-print-2"
    )
    prelim = tasks.get_task(db, prod.preliminary_loading_task_key(PO_ID, LINE_PRINT))
    assert prelim is not None
    linked = set(prelim["context"].get("linked_sj_ids") or [])
    assert SJ_ID in linked and SJ_ID_B in linked
    assert SJ_ID_B in (prelim["context"].get("missing_loading_sj_ids") or [])
    assert prelim["context"]["loading_task_ready"] is False
    assert prelim["status"] == tasks.TASK_STATUS_OPEN
    assert is_actionable_open_task(prelim) is True


def test_no_sj_stays_waiting_actionable(db: Database) -> None:
    from app.services.object_auth import is_actionable_open_task

    alloc = _ready_print(db, prep_qty=200)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="wait-print"
    )
    prelim = tasks.get_task(db, prod.preliminary_loading_task_key(PO_ID, LINE_PRINT))
    assert prelim is not None
    assert prelim["context"]["waiting_for_sj"] is True
    assert prelim["context"].get("linked_sj_ids") in (None, [])
    assert prelim["status"] == tasks.TASK_STATUS_OPEN
    assert is_actionable_open_task(prelim) is True


def test_multi_sj_keeps_loading_sj_independent(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beberapa SJ → preliminary selesai informatif; Loading SJ tetap terpisah/open."""
    from app.services.object_auth import is_actionable_open_task

    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=True)
    alloc = _ready_print(db, prep_qty=300)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=200, idempotency_key="multi-sj-print"
    )
    delivery.ingest_delivery_note(db, OWNER, _sj(SJ_ID, 50))
    delivery.ingest_delivery_note(db, OWNER, _sj(SJ_ID_B, 60))
    prelim = tasks.get_task(db, prod.preliminary_loading_task_key(PO_ID, LINE_PRINT))
    assert prelim is not None
    assert prelim["context"]["loading_task_ready"] is True
    assert prelim["context"].get("missing_loading_sj_ids") in (None, [])
    assert prelim["status"] == tasks.TASK_STATUS_DONE
    assert is_actionable_open_task(prelim) is False
    linked = set(prelim["context"].get("linked_sj_ids") or [])
    assert linked == {SJ_ID, SJ_ID_B}
    loading_rows = tasks.list_tasks(db, task_type="loading-sj")
    assert len(loading_rows) == 2
    assert all(r["status"] != tasks.TASK_STATUS_DONE for r in loading_rows)


def test_packing_gate_still_blocks(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(
        monkeypatch,
        V4_LOADING_SJ_TASK_ENABLED=True,
        V4_LOADING_PACKING_LEDGER_ENFORCED=True,
    )
    _seed_po(db)
    delivery.ingest_delivery_note(db, OWNER, _sj(qty=100))
    validation = delivery.validate_loading(db, SJ_ID)
    assert validation["all_matched"] is False
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_loading(
            db, OWNER, accurate_sj_id=SJ_ID, idempotency_key="pack-block"
        )


def test_flag_off_skips_sj_task_on_insert(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, V4_LOADING_SJ_TASK_ENABLED=False)
    _seed_po(db)
    r = delivery.ingest_delivery_note(db, OWNER, _sj(qty=40))
    assert r["was_insert"] is True
    assert r.get("loading_task_key") is None
    assert tasks.list_tasks(db, task_type="loading-sj") == []
