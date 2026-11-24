"""Status Cetak pada Jadwal Pengiriman: qty cetak terkonfirmasi, bukan qty SJ."""

from __future__ import annotations

import copy

from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import gate_repository as gates
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import production_service as prod
from app.services import scheduling_service
from tests.po_accept_helpers import mark_po_accepted
from tests.sample_flow_helpers import approve_sample

PO_ID = "95106"
LINE_A = "106318"
LINE_B = "106319"

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
OPERATOR_B = {
    "email_normalized": "op2@eternal.local",
    "role": "user",
    "operational_role": "operator",
    "effective_module_permissions": ["access.all_branches"],
}


def _seed(db: Database, payload: dict | None = None) -> dict:
    upsert_sales_order(db, normalize_sales_order(payload or SAMPLE_SALES_ORDER_STRUCTURE))
    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji cetak"
        )
    return get_by_accurate_id(db, PO_ID)


def _two_cetak_same_name(*, unit_b: str = "PCS", qty_b: float = 500) -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    second = payload["detailItem"][1]
    second["charField1"] = "Cetak"
    second["item"] = dict(payload["detailItem"][0]["item"])
    second["quantity"] = qty_b
    second["itemUnit"] = {"name": unit_b}
    return payload


def _schedule(db: Database, po_doc: dict, line_id: str, splits: list[tuple[str, float]], key: str) -> list[str]:
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=line_id,
        allocations=[{"operator_email": op, "qty": qty} for op, qty in splits],
        idempotency_key=key,
    )
    return [row["allocation_id"] for row in res["allocations"]]


def _open_print(db: Database, po_doc: dict, line_id: str, alloc_qty: float, key: str, operator: dict | None = None) -> str:
    actor = operator or OPERATOR
    email = str(actor["email_normalized"])
    (alloc_id,) = _schedule(db, po_doc, line_id, [(email, alloc_qty)], f"sched-{key}")
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=line_id,
        qty=alloc_qty,
        idempotency_key=f"prep-{key}",
    )
    prod.confirm_screen(
        db, SCREENER, allocation_id=alloc_id, qty=10, idempotency_key=f"screen-{key}"
    )
    approve_sample(db, actor, HEAD, alloc_id)
    return alloc_id


def _sj(
    db: Database,
    sj_id: str,
    lines: list[tuple[str, float, str]],
    *,
    number: str | None = None,
    ship_date: str = "2026-11-20",
) -> None:
    deliveries.upsert_delivery_note(
        db,
        {
            "id": sj_id,
            "number": number or sj_id,
            "salesOrderId": PO_ID,
            "salesOrderNumber": "PO/18/11/2026/000010",
            "transDate": ship_date,
            "shipDate": ship_date,
            "toAddress": "ALAMAT-TEST",
            "shipmentName": "OUTLET-TEST",
            "detailItem": [
                {
                    "id": f"{sj_id}-{line_id}",
                    "salesOrderDetailId": line_id,
                    "itemNo": "BRG-SIM-001",
                    "detailName": "Barang Simulasi Cetak",
                    "quantity": qty,
                    "itemUnitName": unit,
                }
                for line_id, qty, unit in lines
            ],
        },
    )


def _row(db: Database, sj_id: str, user: dict | None = None) -> dict:
    schedule = delivery.delivery_schedule(db, user)
    for row in schedule["items"]:
        if row["accurate_sj_id"] == sj_id:
            return row
    raise AssertionError(f"SJ {sj_id} tidak ada di jadwal")


def test_no_print_confirmation_is_verified_zero(db: Database) -> None:
    po = _seed(db)
    alloc = _open_print(db, po, LINE_A, 1000, "a")
    _sj(db, "SJ-A", [(LINE_A, 300, "PCS")])
    status = _row(db, "SJ-A")["print_status"]
    screen = tasks.get_task(db, prod.screen_task_key({"accurate_po_id": PO_ID, "accurate_line_id": LINE_A, "allocation_id": alloc, "po_no": "", "item_code": ""}))
    assert float((screen or {}).get("qty_confirmed") or 0) == 10
    assert status["state"] == "belum"
    assert status["label"] == "Belum Cetak"
    assert status["qty_confirmed"] == 0
    assert status["qty_reference"] == 1000
    assert status["unit"] == "PCS"
    assert status["percent"] == 0


def test_partial_print_uses_po_line_not_sj_or_screen_or_qc(db: Database) -> None:
    po = _seed(db)
    alloc = _open_print(db, po, LINE_A, 1000, "b")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc, qty=250, idempotency_key="print-b")
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_A,
        qty=80,
        idempotency_key="qc-b",
    )
    _sj(db, "SJ-B", [(LINE_A, 999, "PCS")])
    status = _row(db, "SJ-B")["print_status"]
    assert status == {
        "state": "proses",
        "label": "Proses Cetak",
        "qty_confirmed": 250,
        "qty_reference": 1000,
        "unit": "PCS",
        "percent": 25,
    }


def test_full_print_is_complete(db: Database) -> None:
    po = _seed(db)
    alloc = _open_print(db, po, LINE_A, 1000, "c")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc, qty=1000, idempotency_key="print-c")
    _sj(db, "SJ-C", [(LINE_A, 10, "PCS")])
    status = _row(db, "SJ-C")["print_status"]
    assert status["state"] == "selesai"
    assert status["label"] == "Selesai Cetak"
    assert status["qty_confirmed"] == 1000
    assert status["qty_reference"] == 1000
    assert status["percent"] == 100


def test_two_operators_aggregate_separate_tasks(db: Database) -> None:
    po = _seed(db)
    allocs = _schedule(
        db,
        po,
        LINE_A,
        [("op1@eternal.local", 600), ("op2@eternal.local", 400)],
        "sched-d",
    )
    prod.confirm_preparation(
        db, HELPER, accurate_po_id=PO_ID, accurate_line_id=LINE_A, qty=1000, idempotency_key="prep-d"
    )
    for alloc, actor, key, qty in (
        (allocs[0], OPERATOR, "d1", 200),
        (allocs[1], OPERATOR_B, "d2", 300),
    ):
        prod.confirm_screen(db, SCREENER, allocation_id=alloc, qty=10, idempotency_key=f"screen-{key}")
        approve_sample(db, actor, HEAD, alloc)
        prod.confirm_print(db, actor, allocation_id=alloc, qty=qty, idempotency_key=f"print-{key}")
    _sj(db, "SJ-D", [(LINE_A, 100, "PCS")])
    status = _row(db, "SJ-D")["print_status"]
    assert status["qty_confirmed"] == 500
    assert status["qty_reference"] == 1000
    assert status["percent"] == 50
    assert status["state"] == "proses"


def test_retry_same_idempotency_does_not_double_count(db: Database) -> None:
    po = _seed(db)
    alloc = _open_print(db, po, LINE_A, 1000, "e")
    first = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=200, idempotency_key="print-e"
    )
    second = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=200, idempotency_key="print-e"
    )
    assert second["duplicate"] is True
    assert first["duplicate"] is False
    _sj(db, "SJ-E", [(LINE_A, 200, "PCS")])
    before = tasks.confirmations_collection(db).count_documents({})
    confirmed_before = tasks.get_task(db, prod.print_task_key({
        "accurate_po_id": PO_ID,
        "accurate_line_id": LINE_A,
        "allocation_id": alloc,
    }))["qty_confirmed"]
    status = _row(db, "SJ-E")["print_status"]
    assert tasks.confirmations_collection(db).count_documents({}) == before
    assert tasks.get_task(db, prod.print_task_key({
        "accurate_po_id": PO_ID,
        "accurate_line_id": LINE_A,
        "allocation_id": alloc,
    }))["qty_confirmed"] == confirmed_before
    assert status["qty_confirmed"] == 200
    assert status["percent"] == 20


def test_two_lines_same_item_name_stay_separate(db: Database) -> None:
    po = _seed(db, _two_cetak_same_name())
    alloc_a = _open_print(db, po, LINE_A, 1000, "fa")
    alloc_b = _open_print(db, po, LINE_B, 500, "fb")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc_a, qty=200, idempotency_key="print-fa")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc_b, qty=100, idempotency_key="print-fb")
    assert prod.printed_total_for_line(db, accurate_po_id=PO_ID, accurate_line_id=LINE_A) == 200
    assert prod.printed_total_for_line(db, accurate_po_id=PO_ID, accurate_line_id=LINE_B) == 100
    _sj(db, "SJ-F", [(LINE_A, 10, "PCS"), (LINE_B, 10, "PCS")])
    status = _row(db, "SJ-F")["print_status"]
    assert status["qty_confirmed"] == 300
    assert status["qty_reference"] == 1500
    assert status["unit"] == "PCS"
    assert status["percent"] == 20
    assert status["state"] == "proses"


def test_two_active_sj_do_not_repeat_line_print_qty(db: Database) -> None:
    po = _seed(db)
    alloc = _open_print(db, po, LINE_A, 1000, "g")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc, qty=400, idempotency_key="print-g")
    _sj(db, "SJ-G1", [(LINE_A, 100, "PCS")], number="SJ/G/1")
    _sj(db, "SJ-G2", [(LINE_A, 100, "PCS")], number="SJ/G/2")
    schedule = delivery.delivery_schedule(db)
    by_id = {row["accurate_sj_id"]: row["print_status"] for row in schedule["items"]}
    assert by_id["SJ-G1"]["state"] == "unavailable"
    assert by_id["SJ-G2"]["state"] == "unavailable"
    assert by_id["SJ-G1"]["qty_confirmed"] is None
    assert by_id["SJ-G2"]["qty_confirmed"] is None
    assert by_id["SJ-G1"]["label"] == "Belum tersedia"


def test_replaced_sj_does_not_keep_a_second_copy_of_print_qty(db: Database) -> None:
    po = _seed(db)
    alloc = _open_print(db, po, LINE_A, 1000, "g2")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc, qty=400, idempotency_key="print-g2")
    _sj(db, "SJ-OLD", [(LINE_A, 100, "PCS")], number="SJ/OLD")
    _sj(db, "SJ-NEW", [(LINE_A, 100, "PCS")], number="SJ/NEW")
    deliveries.mark_superseded(db, accurate_sj_id="SJ-OLD", replacement_sj_id="SJ-NEW")
    old = _row(db, "SJ-OLD")["print_status"]
    new = _row(db, "SJ-NEW")["print_status"]
    assert old["state"] == "unavailable"
    assert old["qty_confirmed"] is None
    assert new["qty_confirmed"] == 400
    assert new["qty_reference"] == 1000
    assert new["state"] == "proses"


def test_ship_date_window_does_not_claim_qty_shared_with_hidden_sj(db: Database) -> None:
    po = _seed(db)
    alloc = _open_print(db, po, LINE_A, 1000, "win")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc, qty=400, idempotency_key="print-win")
    _sj(db, "SJ-W1", [(LINE_A, 100, "PCS")], number="SJ/W/1", ship_date="2026-11-20")
    _sj(db, "SJ-W2", [(LINE_A, 100, "PCS")], number="SJ/W/2", ship_date="2026-12-02")
    window = delivery.delivery_schedule(db, ship_date_from="2026-11-20", ship_date_to="2026-11-20")
    assert [row["accurate_sj_id"] for row in window["items"]] == ["SJ-W1"]
    assert window["items"][0]["print_status"]["state"] == "unavailable"
    assert window["items"][0]["print_status"]["qty_confirmed"] is None


def test_reference_larger_than_print_stays_partial(db: Database) -> None:
    po = _seed(db)
    alloc = _open_print(db, po, LINE_A, 1000, "h")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc, qty=100, idempotency_key="print-h")
    _sj(db, "SJ-H", [(LINE_A, 5000, "PACK")])
    status = _row(db, "SJ-H")["print_status"]
    assert status["qty_confirmed"] == 100
    assert status["qty_reference"] == 1000
    assert status["unit"] == "PCS"
    assert status["percent"] == 10
    assert status["state"] == "proses"


def test_unmapped_line_is_unavailable_not_zero(db: Database) -> None:
    _seed(db)
    _sj(db, "SJ-I", [("999999", 20, "PCS")])
    status = _row(db, "SJ-I")["print_status"]
    assert status["state"] == "unavailable"
    assert status["label"] == "Belum tersedia"
    assert status["qty_confirmed"] is None
    assert status["percent"] is None


def test_non_cetak_line_is_not_a_fake_zero(db: Database) -> None:
    _seed(db)
    _sj(db, "SJ-NC", [(LINE_B, 50, "PACK")])
    status = _row(db, "SJ-NC")["print_status"]
    assert status["state"] == "non_cetak"
    assert status["label"] == "Non-Cetak"
    assert status["qty_confirmed"] is None
    assert status["percent"] is None


def test_mixed_units_are_not_summed(db: Database) -> None:
    po = _seed(db, _two_cetak_same_name(unit_b="PACK", qty_b=50))
    alloc_a = _open_print(db, po, LINE_A, 1000, "mix-a")
    alloc_b = _open_print(db, po, LINE_B, 50, "mix-b")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc_a, qty=10, idempotency_key="print-mix-a")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc_b, qty=5, idempotency_key="print-mix-b")
    _sj(db, "SJ-MIX", [(LINE_A, 1, "PCS"), (LINE_B, 1, "PACK")])
    status = _row(db, "SJ-MIX")["print_status"]
    assert status["state"] == "unavailable"
    assert status["qty_confirmed"] is None


def test_cross_branch_schedule_does_not_leak_other_sj(db: Database) -> None:
    po = _seed(db, _two_cetak_same_name(qty_b=500))
    alloc_a = _open_print(db, po, LINE_A, 1000, "ja")
    alloc_b = _open_print(db, po, LINE_B, 500, "jb")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc_a, qty=111, idempotency_key="print-ja")
    prod.confirm_print(db, OPERATOR, allocation_id=alloc_b, qty=321, idempotency_key="print-jb")
    _sj(db, "SJ-HQ", [(LINE_A, 10, "PCS")], number="SJ/HQ")
    _sj(db, "SJ-JOG", [(LINE_B, 10, "PCS")], number="SJ/JOG")
    deliveries.delivery_notes_collection(db).update_one(
        {"accurate_sj_id": "SJ-HQ"}, {"$set": {"branch_code": "HQ"}}
    )
    deliveries.delivery_notes_collection(db).update_one(
        {"accurate_sj_id": "SJ-JOG"}, {"$set": {"branch_code": "JOG"}}
    )
    limited = {
        "email_normalized": "hq@eternal.local",
        "role": "user",
        "operational_role": "driver",
        "branch_ids": ["HQ"],
        "effective_module_permissions": [],
    }
    schedule = delivery.delivery_schedule(db, limited)
    ids = [row["accurate_sj_id"] for row in schedule["items"]]
    assert ids == ["SJ-HQ"]
    status = schedule["items"][0]["print_status"]
    assert status["qty_confirmed"] == 111
    assert status["qty_reference"] == 1000
    blob = str(schedule)
    assert "SJ-JOG" not in blob
    assert "321" not in blob
