"""RULE 01 — Ukuran Screen dari Master Produk (bukan DEMO)."""

from __future__ import annotations

from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import gate_repository as gates
from app.repositories import product_master_repository as products
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import scheduling_service

PO_ID = "95106"
LINE_A = "106318"
HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
}


def _seed_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted

    mark_po_accepted(db)
    return get_by_accurate_id(db, PO_ID)


def _open_gates(db: Database) -> None:
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="design", state=gates.GATE_OPEN, reason="uji"
    )
    gates.set_gate(
        db, accurate_po_id=PO_ID, gate="payment", state=gates.GATE_OPEN, reason="uji"
    )


def test_screen_size_unset_without_master_mapping(db: Database) -> None:
    po_doc = _seed_po(db)
    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    assert state["screen_size"] == products.SCREEN_UNSET
    assert products.screen_size_for_item(db, state["item_code"]) == products.SCREEN_UNSET


def test_screen_size_from_master_product(db: Database) -> None:
    po_doc = _seed_po(db)
    base = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    item_code = str(base["item_code"])
    products.upsert_product_screen_size(
        db,
        item_code=item_code,
        screen_size="35x45",
        actor_email="owner@eternal.local",
    )
    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    assert state["screen_size"] == "35x45"
    assert state["item_code"] == item_code


def test_screen_size_empty_string_falls_back_to_unset(db: Database) -> None:
    po_doc = _seed_po(db)
    base = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    item_code = str(base["item_code"])
    products.upsert_product_screen_size(
        db,
        item_code=item_code,
        screen_size="  ",
        actor_email="owner@eternal.local",
    )
    assert products.screen_size_for_item(db, item_code) == products.SCREEN_UNSET
    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    assert state["screen_size"] == products.SCREEN_UNSET


def test_partial_schedule_leaves_remainder_in_backlog(db: Database) -> None:
    """RULE 02 — sisa qty menunggu jadwal; alokasi batal tidak dihitung terjadwal."""
    po_doc = _seed_po(db)
    _open_gates(db)
    scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 400}],
        idempotency_key="partial-sched-1",
    )
    state = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    assert state["qty_target"] == 1000
    assert state["qty_allocated"] == 400
    assert state["qty_unallocated"] == 600

    # Alokasi kedua (sisa) tanpa auto-reschedule hanya karena sisa.
    scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_A,
        allocations=[{"operator_email": "op2@eternal.local", "qty": 600}],
        idempotency_key="partial-sched-2",
    )
    full = scheduling_service.line_schedule_state(db, po_doc, LINE_A)
    assert full["qty_allocated"] == 1000
    assert full["qty_unallocated"] == 0
