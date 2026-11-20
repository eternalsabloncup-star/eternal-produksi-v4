"""P0-A: revisi alokasi vs cetak + kapasitas line (Mongo RS :27018)."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from pymongo import MongoClient
from pymongo.database import Database

from app.domain.operational import TaskRef, build_task_key
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import schedule_repository as schedules
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import production_service as prod
from app.services import scheduling_service
from tests.sample_flow_helpers import approve_sample

PO_ID = "95106"
LINE_A = "106318"
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
}
OP = {
    "email_normalized": "op@eternal.local",
    "role": "user",
    "operational_role": "operator",
}


def _seed(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted

    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji"
        )
    master.seed_master_defaults(db)
    return get_by_accurate_id(db, PO_ID)


def _prep(db: Database, qty: float = 1000) -> None:
    ref = TaskRef(accurate_po_id=PO_ID, accurate_line_id=LINE_A)
    tasks.upsert_task(
        db,
        task_type="persiapan-cup",
        scope="po_line",
        assigned_role="helper",
        ref=ref,
        qty_target=qty,
        label="Persiapan",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("persiapan-cup", "po_line", ref),
        qty=qty,
        actor_email="helper@eternal.local",
        actor_role="helper",
        idempotency_key=f"prep-{uuid.uuid4().hex}",
    )


def _screen(db: Database, alloc_id: str, qty: float = 10) -> None:
    ref = TaskRef(
        accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc_id
    )
    tasks.upsert_task(
        db,
        task_type="screen",
        scope="po_line_allocation",
        assigned_role="screener",
        ref=ref,
        qty_target=qty,
        label="Screen",
    )
    tasks.confirm_task_qty(
        db,
        task_key=build_task_key("screen", "po_line_allocation", ref),
        qty=qty,
        actor_email="sc@eternal.local",
        actor_role="screener",
        idempotency_key=f"screen-{alloc_id}-{uuid.uuid4().hex}",
    )


def _ready_print(db: Database, po: dict, alloc_qty: float = 400) -> str:
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op@eternal.local", "qty": alloc_qty}],
        idempotency_key=f"alloc-{uuid.uuid4().hex}",
    )
    alloc = created["allocations"][0]["allocation_id"]
    _prep(db, qty=1000)
    _screen(db, alloc)
    approve_sample(db, OP, HEAD, alloc)
    return alloc


def test_print_cannot_exceed_allocation_after_revise(db: Database) -> None:
    po = _seed(db)
    alloc = _ready_print(db, po, alloc_qty=400)
    scheduling_service.revise_allocation_qty(
        db, HEAD, po_doc=po, allocation_id=alloc, qty=150, reason="turun kapasitas"
    )
    with pytest.raises(Exception):
        prod.confirm_print(
            db,
            OP,
            allocation_id=alloc,
            qty=200,
            idempotency_key=f"print-over-{uuid.uuid4().hex}",
        )
    printed = scheduling_service.printed_qty_for_allocation(
        db, accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc
    )
    assert printed <= 150


def test_concurrent_print_and_reduce_allocation(db: Database) -> None:
    po = _seed(db)
    alloc = _ready_print(db, po, alloc_qty=500)
    results: list[str] = []

    def do_print() -> str:
        try:
            prod.confirm_print(
                db,
                OP,
                allocation_id=alloc,
                qty=300,
                idempotency_key=f"p-conc-{uuid.uuid4().hex}",
            )
            return "print_ok"
        except Exception as exc:  # noqa: BLE001
            return f"print_err:{type(exc).__name__}"

    def do_revise() -> str:
        try:
            scheduling_service.revise_allocation_qty(
                db,
                HEAD,
                po_doc=po,
                allocation_id=alloc,
                qty=200,
                reason="concurrent reduce",
            )
            return "revise_ok"
        except Exception as exc:  # noqa: BLE001
            return f"revise_err:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(do_print), pool.submit(do_revise)]
        for fut in as_completed(futs):
            results.append(fut.result())

    alloc_doc = schedules.get_allocation(db, alloc)
    assert alloc_doc is not None
    printed = scheduling_service.printed_qty_for_allocation(
        db, accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc
    )
    assert printed <= float(alloc_doc["qty"])
    assert any(r.startswith("print_") for r in results)
    assert any(r.startswith("revise_") for r in results)


def test_two_allocation_increases_cannot_exceed_po_qty(db: Database) -> None:
    po = _seed(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[
            {"operator_email": "op-a@eternal.local", "qty": 80},
            {"operator_email": "op-b@eternal.local", "qty": 80},
        ],
        idempotency_key=f"two-{uuid.uuid4().hex}",
    )
    a = created["allocations"][0]["allocation_id"]
    b = created["allocations"][1]["allocation_id"]
    outcomes: list[str] = []

    def bump(alloc_id: str) -> str:
        try:
            scheduling_service.revise_allocation_qty(
                db,
                HEAD,
                po_doc=po,
                allocation_id=alloc_id,
                qty=120,
                reason="bump",
            )
            return "ok"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(bump, a), pool.submit(bump, b)]
        for fut in as_completed(futs):
            outcomes.append(fut.result())

    total = schedules.active_allocated_qty(db, LINE_A)
    assert total <= 1000
    # Line sample qty is 1000 — 120+120=240 still under. Use tighter capacity via
    # pre-filling more allocations when sample line is large; assert invariant only.
    assert total == sum(
        float(x["qty"])
        for x in schedules.list_allocations(db, accurate_line_id=LINE_A)
        if x.get("status") == schedules.STATUS_ACTIVE
    )


def test_two_allocation_increases_near_capacity(db: Database) -> None:
    """PO line 1000; seed 400+400 then concurrent +200/+200 must not exceed."""
    po = _seed(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[
            {"operator_email": "op-a@eternal.local", "qty": 400},
            {"operator_email": "op-b@eternal.local", "qty": 400},
        ],
        idempotency_key=f"cap-{uuid.uuid4().hex}",
    )
    a = created["allocations"][0]["allocation_id"]
    b = created["allocations"][1]["allocation_id"]

    def bump(alloc_id: str) -> str:
        try:
            scheduling_service.revise_allocation_qty(
                db,
                HEAD,
                po_doc=po,
                allocation_id=alloc_id,
                qty=600,
                reason="near cap",
            )
            return "ok"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in as_completed([pool.submit(bump, a), pool.submit(bump, b)])]

    total = schedules.active_allocated_qty(db, LINE_A)
    assert total <= 1000
    assert results.count("ok") <= 1


def test_print_idempotency_retry(db: Database) -> None:
    po = _seed(db)
    alloc = _ready_print(db, po, alloc_qty=300)
    key = f"idem-{uuid.uuid4().hex}"
    first = prod.confirm_print(
        db, OP, allocation_id=alloc, qty=50, idempotency_key=key
    )
    second = prod.confirm_print(
        db, OP, allocation_id=alloc, qty=50, idempotency_key=key
    )
    assert first.get("duplicate") is not True
    assert second.get("duplicate") is True
    printed = scheduling_service.printed_qty_for_allocation(
        db, accurate_po_id=PO_ID, accurate_line_id=LINE_A, allocation_id=alloc
    )
    assert printed == 50


def test_shared_helper_quota_multi_operator(db: Database) -> None:
    po = _seed(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[
            {"operator_email": "op-a@eternal.local", "qty": 300},
            {"operator_email": "op-b@eternal.local", "qty": 300},
        ],
        idempotency_key=f"quota-{uuid.uuid4().hex}",
    )
    a = created["allocations"][0]["allocation_id"]
    b = created["allocations"][1]["allocation_id"]
    _prep(db, qty=100)
    for alloc in (a, b):
        _screen(db, alloc)
        approve_sample(
            db,
            {
                "email_normalized": "op-a@eternal.local"
                if alloc == a
                else "op-b@eternal.local",
                "role": "user",
                "operational_role": "operator",
            },
            HEAD,
            alloc,
        )
    ok = prod.confirm_print(
        db,
        {"email_normalized": "op-a@eternal.local", "role": "user", "operational_role": "operator"},
        allocation_id=a,
        qty=80,
        idempotency_key=f"qa-{uuid.uuid4().hex}",
    )
    assert ok.get("duplicate") is not True
    with pytest.raises(Exception):
        prod.confirm_print(
            db,
            {
                "email_normalized": "op-b@eternal.local",
                "role": "user",
                "operational_role": "operator",
            },
            allocation_id=b,
            qty=40,
            idempotency_key=f"qb-{uuid.uuid4().hex}",
        )


def test_separate_db_clients_capacity_guard(db: Database) -> None:
    """Dua MongoClient terpisah (thread) pada DB yang sama — bukan OS multiprocess."""
    uri = os.environ["MONGODB_URI"]
    db_name = db.name
    po = _seed(db)
    created = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po,
        accurate_line_id=LINE_A,
        allocations=[
            {"operator_email": "op-a@eternal.local", "qty": 400},
            {"operator_email": "op-b@eternal.local", "qty": 400},
        ],
        idempotency_key=f"mp-{uuid.uuid4().hex}",
    )
    a = created["allocations"][0]["allocation_id"]
    b = created["allocations"][1]["allocation_id"]

    def worker(alloc_id: str) -> str:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        try:
            database = client[db_name]
            po_doc = get_by_accurate_id(database, PO_ID)
            assert po_doc is not None
            try:
                scheduling_service.revise_allocation_qty(
                    database,
                    HEAD,
                    po_doc=po_doc,
                    allocation_id=alloc_id,
                    qty=600,
                    reason="mp",
                )
                return "ok"
            except Exception as exc:  # noqa: BLE001
                return type(exc).__name__
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            f.result()
            for f in as_completed([pool.submit(worker, a), pool.submit(worker, b)])
        ]
    total = schedules.active_allocated_qty(db, LINE_A)
    assert total <= 1000
    assert results.count("ok") <= 1
    # OS-level multiprocess spawn: NOT VERIFIED in this harness.
