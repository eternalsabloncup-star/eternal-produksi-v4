"""Checkpoint F: Buffer Stock, Alur Produksi 15 node, Dashboard, Laporan."""

from __future__ import annotations

import copy

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import gate_repository as gates
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import production_service as prod
from app.services import read_models
from app.services import scheduling_service

PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NON_PRINT = "106319"

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
DRIVER = {
    "email_normalized": "driver@eternal.local",
    "role": "user",
    "operational_role": "driver",
    "effective_module_permissions": ["access.all_branches"],
}
PNG = b"\x89PNG f"


def _payload(buffer_label: str | None = None) -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    if buffer_label is not None:
        payload["charField5"] = buffer_label
    return payload


def _seed(db: Database, buffer_label: str | None = None) -> dict:
    upsert_sales_order(db, normalize_sales_order(_payload(buffer_label)))
    from tests.po_accept_helpers import mark_po_accepted
    mark_po_accepted(db)
    return get_by_accurate_id(db, PO_ID)


def _open_gates(db: Database) -> None:
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji F"
        )


def _run_production(db: Database, po_doc: dict, qty: float) -> str:
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_PRINT,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="f-sched",
    )
    alloc = res["allocations"][0]["allocation_id"]
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=qty,
        idempotency_key="f-prep",
    )
    prod.confirm_screen(db, SCREENER, allocation_id=alloc, qty=20, idempotency_key="f-screen")
    from tests.sample_flow_helpers import approve_sample

    approve_sample(db, OPERATOR, HEAD, alloc)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=qty, idempotency_key="f-print"
    )
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=qty,
        idempotency_key="f-qc",
    )
    return alloc


def _sj(db: Database, qty: float, sj_id: str = "SJ-F-1") -> str:
    deliveries.upsert_delivery_note(
        db,
        {
            "id": sj_id,
            "number": f"SJ/F/{sj_id}",
            "salesOrderId": PO_ID,
            "salesOrderNumber": "PO/18/11/2026/000010",
            "transDate": "2026-11-24",
            "toAddress": "ALAMAT-F",
            "detailItem": [
                {
                    "id": f"{sj_id}-L1",
                    "salesOrderDetailId": LINE_PRINT,
                    "itemNo": "BRG-SIM-001",
                    "quantity": qty,
                    "itemUnitName": "PCS",
                }
            ],
        },
    )
    return sj_id


# --- Buffer Stock ----------------------------------------------------------


def test_buffer_detection_from_accurate_attribute(db: Database) -> None:
    assert read_models.is_buffer_po({"header": {"buffer_label": "Buffer"}}) is True
    assert read_models.is_buffer_po({"header": {"buffer_label": "Non - Buffer"}}) is False
    assert read_models.is_buffer_po({"header": {"buffer_label": ""}}) is False


def test_buffer_stock_excludes_non_buffer_po(db: Database) -> None:
    _seed(db, "Non - Buffer")
    assert read_models.buffer_stock_rows(db)["items"] == []


def test_buffer_stock_excludes_empty_buffer_label(db: Database) -> None:
    _seed(db, "")
    assert read_models.buffer_stock_rows(db)["items"] == []
    # Label hilang dari payload Accurate → tidak masuk monitoring Buffer.
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload.pop("charField5", None)
    payload.pop("searchCharField5", None)
    upsert_sales_order(db, normalize_sales_order(payload))
    from tests.po_accept_helpers import mark_po_accepted

    mark_po_accepted(db)
    assert read_models.buffer_stock_rows(db)["items"] == []


def test_buffer_stock_keeps_cetak_and_non_cetak_rows_without_fiction(
    db: Database,
) -> None:
    """PO Buffer: baris Cetak + Non-Cetak; status/outlet tidak dikarang."""
    po_doc = _seed(db, "Buffer")
    _open_gates(db)
    _run_production(db, po_doc, 200)
    result = read_models.buffer_stock_rows(db)
    assert len(result["items"]) == 2
    by_line = {r["accurate_line_id"]: r for r in result["items"]}
    cetak = by_line[LINE_PRINT]
    non_cetak = by_line[LINE_NON_PRINT]
    assert cetak["process_kind"] == "Cetak"
    assert cetak["qty_printed"] == 200
    assert cetak["qty_ready_warehouse"] == 200
    assert non_cetak["process_kind"] == "Non-Cetak"
    assert non_cetak["qty_printed"] == 0
    assert non_cetak["qty_ready_warehouse"] == 0
    for row in (cetak, non_cetak):
        assert row["buffer_status"] == read_models.NOT_CONFIGURED
        assert row["outlet_warehouse"] == read_models.NOT_CONFIGURED
        assert row["buffer_status"] != 0
        assert row["outlet_warehouse"] != 0
    assert "buffer_status" in result["not_configured"]
    assert "outlet_warehouse" in result["not_configured"]


def test_buffer_stock_branch_scope_isolates_rows(db: Database) -> None:
    """Monitoring Buffer wajib terisolasi cabang — tanpa ubah stok fisik."""
    from app.repositories.po_repository import set_po_branch_id

    po_doc = _seed(db, "Buffer")
    set_po_branch_id(db, PO_ID, branch_id="50", expected_previous=None)
    # Cabang lain: tidak melihat baris.
    foreign = read_models.buffer_stock_rows(
        db, branch_ids=["150"], branch_scope=True
    )
    assert foreign["items"] == []
    # Cabang pemilik: melihat baris Buffer.
    own = read_models.buffer_stock_rows(db, branch_ids=["50"], branch_scope=True)
    assert len(own["items"]) == 2
    assert all(r["accurate_po_id"] == PO_ID for r in own["items"])
    # Fail-closed daftar cabang kosong.
    empty = read_models.buffer_stock_rows(db, branch_ids=[], branch_scope=True)
    assert empty["items"] == []
    # Elevated (tanpa branch_scope) tetap melihat semua — sama sebelum harden.
    elevated = read_models.buffer_stock_rows(db, branch_scope=False)
    assert len(elevated["items"]) >= 2
    assert po_doc.get("accurate_id") == PO_ID


def test_buffer_stock_rows_use_real_po_number(db: Database) -> None:
    po_doc = _seed(db, "Buffer")
    _open_gates(db)
    _run_production(db, po_doc, 400)

    result = read_models.buffer_stock_rows(db)
    rows = result["items"]
    assert len(rows) == 2  # satu baris per item line
    printed_row = next(r for r in rows if r["accurate_line_id"] == LINE_PRINT)
    assert printed_row["po_no"] == "PO/18/11/2026/000010"
    assert printed_row["qty_ordered"] == 1000
    assert printed_row["qty_printed"] == 400
    assert printed_row["qty_remaining"] == 600
    # Owner: Ready = cetak − terkirim; belum ada delivery → Ready = cetak.
    assert printed_row["qty_ready_warehouse"] == 400
    assert printed_row["ready_mismatch"] is False
    assert printed_row["buffer_status"] == read_models.NOT_CONFIGURED
    assert printed_row["outlet_warehouse"] == read_models.NOT_CONFIGURED
    assert result["kpi"]["qty_ready_warehouse"] == 400
    assert "qty_ready_warehouse" not in result["not_configured"]
    assert result["kpi"]["total_buffer_ordered"] == 1050
    assert result["ready_formula"] == (
        "qty_printed_confirmed - qty_delivery_success_confirmed"
    )


def test_compute_ready_gudang_owner_cases() -> None:
    """Kasus angka Owner tanpa DB — murni rumus."""
    assert read_models.compute_ready_gudang(
        qty_printed=10_000, qty_delivered_confirmed=6_000
    ) == {
        "qty_ready_warehouse": 4_000,
        "ready_mismatch": False,
        "qty_over_shipped": 0.0,
    }
    # SJ/Loading dibuat belum terkirim → shipped 0 → Ready = cetak.
    assert read_models.compute_ready_gudang(
        qty_printed=10_000, qty_delivered_confirmed=0
    )["qty_ready_warehouse"] == 10_000
    # Parsial 3k+3k.
    assert read_models.compute_ready_gudang(
        qty_printed=10_000, qty_delivered_confirmed=6_000
    )["qty_ready_warehouse"] == 4_000
    mismatch = read_models.compute_ready_gudang(
        qty_printed=100, qty_delivered_confirmed=150
    )
    assert mismatch["qty_ready_warehouse"] == -50
    assert mismatch["ready_mismatch"] is True
    assert mismatch["qty_over_shipped"] == 50


def test_buffer_shipped_counts_only_successful_delivery(db: Database) -> None:
    po_doc = _seed(db, "Buffer")
    _open_gates(db)
    _run_production(db, po_doc, 300)
    _sj(db, 300)
    delivery.confirm_loading(db, DRIVER, accurate_sj_id="SJ-F-1", idempotency_key="f-load")

    mid = read_models.buffer_stock_rows(db)
    assert mid["kpi"]["qty_shipped"] == 0
    printed_row = next(
        r for r in mid["items"] if r["accurate_line_id"] == LINE_PRINT
    )
    # SJ + Loading belum terkirim → Ready tetap = qty cetak.
    assert printed_row["qty_ready_warehouse"] == 300

    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id="SJ-F-1",
        recipient_name="Penerima F",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("s.png", "image/png", PNG),
    )
    after = read_models.buffer_stock_rows(db)
    assert after["kpi"]["qty_shipped"] == 300
    printed_after = next(
        r for r in after["items"] if r["accurate_line_id"] == LINE_PRINT
    )
    assert printed_after["qty_ready_warehouse"] == 0


def test_buffer_ready_partial_deliveries_and_no_double_count(db: Database) -> None:
    po_doc = _seed(db, "Buffer")
    _open_gates(db)
    _run_production(db, po_doc, 400)

    _sj(db, 150, sj_id="SJ-F-A")
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="SJ-F-A", idempotency_key="f-load-a"
    )
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id="SJ-F-A",
        recipient_name="Penerima A",
        photos=[("a1.png", "image/png", PNG), ("a2.png", "image/png", PNG)],
        signature=("as.png", "image/png", PNG),
    )
    # Konfirmasi berulang — tidak mengurangi dua kali.
    again = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id="SJ-F-A",
        recipient_name="Penerima A",
        photos=[("a1.png", "image/png", PNG), ("a2.png", "image/png", PNG)],
        signature=("as.png", "image/png", PNG),
    )
    assert again["status"] == deliveries.DELIVERY_SUCCESS

    _sj(db, 150, sj_id="SJ-F-B")
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="SJ-F-B", idempotency_key="f-load-b"
    )
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id="SJ-F-B",
        recipient_name="Penerima B",
        photos=[("b1.png", "image/png", PNG), ("b2.png", "image/png", PNG)],
        signature=("bs.png", "image/png", PNG),
    )

    result = read_models.buffer_stock_rows(db)
    printed_row = next(
        r for r in result["items"] if r["accurate_line_id"] == LINE_PRINT
    )
    assert printed_row["qty_printed"] == 400
    assert printed_row["qty_shipped"] == 300
    assert printed_row["qty_ready_warehouse"] == 100
    # Line Non-Cetak tidak mencampur qty cetak/terkirim line Cetak.
    non_print = next(
        r for r in result["items"] if r["accurate_line_id"] == LINE_NON_PRINT
    )
    assert non_print["qty_printed"] == 0
    assert non_print["qty_shipped"] == 0
    assert non_print["qty_ready_warehouse"] == 0


def test_buffer_ready_skips_superseded_sj(db: Database) -> None:
    po_doc = _seed(db, "Buffer")
    _open_gates(db)
    _run_production(db, po_doc, 200)
    _sj(db, 200, sj_id="SJ-OLD")
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="SJ-OLD", idempotency_key="f-load-old"
    )
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id="SJ-OLD",
        recipient_name="Penerima Old",
        photos=[("o1.png", "image/png", PNG), ("o2.png", "image/png", PNG)],
        signature=("os.png", "image/png", PNG),
    )
    before = next(
        r
        for r in read_models.buffer_stock_rows(db)["items"]
        if r["accurate_line_id"] == LINE_PRINT
    )
    assert before["qty_shipped"] == 200
    assert before["qty_ready_warehouse"] == 0

    deliveries.mark_superseded(
        db, accurate_sj_id="SJ-OLD", replacement_sj_id="SJ-NEW"
    )
    after = next(
        r
        for r in read_models.buffer_stock_rows(db)["items"]
        if r["accurate_line_id"] == LINE_PRINT
    )
    # SJ superseded tidak dihitung — hindari dobel setelah revisi.
    assert after["qty_shipped"] == 0
    assert after["qty_ready_warehouse"] == 200


# --- Alur Produksi ---------------------------------------------------------


def test_flow_has_fifteen_nodes_in_locked_order(db: Database) -> None:
    po_doc = _seed(db)
    flow = read_models.production_flow_state(db, po_doc)
    assert [n["label"] for n in flow["nodes"]] == [
        "Order Masuk",
        "Design",
        "Approval Design",
        "Payment",
        "Approval Payment",
        "Penjadwalan",
        "Screener",
        "Persiapan",
        "Operator",
        "Konfirmasi Sample",
        "Cetak",
        "Packing",
        "Surat Jalan",
        "Loading",
        "Pengiriman",
    ]
    assert flow["parallel_pairs"] == [
        ["flow-design", "flow-payment"],
        ["flow-screener", "flow-persiapan"],
    ]


def test_flow_starts_waiting_not_completed(db: Database) -> None:
    po_doc = _seed(db)
    nodes = {n["id"]: n for n in read_models.production_flow_state(db, po_doc)["nodes"]}
    assert nodes["flow-order-masuk"]["status"] == read_models.NODE_DONE
    assert nodes["flow-design"]["status"] == read_models.NODE_WAITING
    assert nodes["flow-approval-payment"]["status"] == read_models.NODE_WAITING
    assert nodes["flow-pengiriman"]["status"] == read_models.NODE_WAITING


def test_flow_marks_non_print_po_stages_not_applicable(db: Database) -> None:
    payload = _payload()
    for line in payload["detailItem"]:
        line["charField1"] = "Tidak Cetak"
    upsert_sales_order(db, normalize_sales_order(payload))
    po_doc = get_by_accurate_id(db, PO_ID)
    nodes = {n["id"]: n for n in read_models.production_flow_state(db, po_doc)["nodes"]}
    for node_id in (
        "flow-design",
        "flow-penjadwalan",
        "flow-screener",
        "flow-persiapan",
        "flow-cetak",
        "flow-packing",
    ):
        assert nodes[node_id]["status"] == read_models.NODE_NOT_APPLICABLE


def test_flow_follows_real_events(db: Database) -> None:
    po_doc = _seed(db)
    _open_gates(db)
    _run_production(db, po_doc, 1000)
    sj = _sj(db, 1000)
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=sj, idempotency_key="f-flow-load")

    nodes = {n["id"]: n for n in read_models.production_flow_state(db, po_doc)["nodes"]}
    assert nodes["flow-approval-design"]["status"] == read_models.NODE_DONE
    assert nodes["flow-penjadwalan"]["status"] == read_models.NODE_DONE
    assert nodes["flow-screener"]["status"] == read_models.NODE_DONE
    assert nodes["flow-persiapan"]["status"] == read_models.NODE_DONE
    assert nodes["flow-konfirmasi-sample"]["status"] == read_models.NODE_DONE
    assert nodes["flow-cetak"]["status"] == read_models.NODE_DONE
    assert nodes["flow-packing"]["status"] == read_models.NODE_DONE
    assert nodes["flow-surat-jalan"]["status"] == read_models.NODE_DONE
    assert nodes["flow-loading"]["status"] == read_models.NODE_DONE
    assert nodes["flow-pengiriman"]["status"] == read_models.NODE_WAITING

    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=sj,
        recipient_name="Penerima F",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("s.png", "image/png", PNG),
    )
    after = {n["id"]: n for n in read_models.production_flow_state(db, po_doc)["nodes"]}
    assert after["flow-pengiriman"]["status"] == read_models.NODE_DONE


def test_flow_payment_review_is_running_not_done(db: Database) -> None:
    po_doc = _seed(db)
    gates.set_gate(
        db,
        accurate_po_id=PO_ID,
        gate="payment",
        state=gates.GATE_REVIEW,
        reason="Perlu keputusan Owner",
    )
    nodes = {n["id"]: n for n in read_models.production_flow_state(db, po_doc)["nodes"]}
    assert nodes["flow-payment"]["status"] == read_models.NODE_RUNNING
    assert nodes["flow-approval-payment"]["status"] == read_models.NODE_WAITING


# --- Dashboard -------------------------------------------------------------


def test_dashboard_counts_po_once_despite_many_lines(db: Database) -> None:
    _seed(db)
    metrics = read_models.dashboard_metrics(db)
    assert metrics["po_total"] == 1
    assert metrics["po_waiting_design_gate"] == 1
    assert metrics["po_waiting_payment_gate"] == 1
    assert metrics["lines_ready_to_schedule"] == 0


def test_dashboard_reflects_production_and_delivery(db: Database) -> None:
    po_doc = _seed(db)
    _open_gates(db)
    _run_production(db, po_doc, 500)
    sj = _sj(db, 500)
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=sj, idempotency_key="f-dash-load")

    metrics = read_models.dashboard_metrics(db)
    assert metrics["po_waiting_design_gate"] == 0
    assert metrics["lines_ready_to_schedule"] == 1
    assert metrics["qty_printed_by_unit"] == {"PCS": 500.0}
    assert metrics["qty_packed_by_unit"] == {"PCS": 500.0}
    assert metrics["sj_total"] == 1
    assert metrics["sj_loaded"] == 1
    assert metrics["sj_delivered_success"] == 0

    delivery.confirm_delivery_failure(db, DRIVER, accurate_sj_id=sj, reason="Gagal uji")
    assert read_models.dashboard_metrics(db)["sj_delivered_failed"] == 1


def test_dashboard_declares_limitations(db: Database) -> None:
    _seed(db)
    metrics = read_models.dashboard_metrics(db)
    assert any("cabang" in note.lower() for note in metrics["limitations"])


# --- Laporan ---------------------------------------------------------------


def test_report_po_includes_gate_state(db: Database) -> None:
    _seed(db)
    rows = read_models.report(db, "po")["items"]
    assert rows[0]["po_no"] == "PO/18/11/2026/000010"
    assert rows[0]["gate_design"] == "closed"


def test_report_printing_matches_ledger(db: Database) -> None:
    po_doc = _seed(db)
    _open_gates(db)
    alloc = _run_production(db, po_doc, 250)
    rows = read_models.report(db, "printing")["items"]
    assert len(rows) == 1
    assert rows[0]["qty"] == 250
    assert rows[0]["actor_email"] == "op1@eternal.local"
    assert rows[0]["ref"]["allocation_id"] == alloc


def test_report_filters_by_actor_and_po(db: Database) -> None:
    po_doc = _seed(db)
    _open_gates(db)
    _run_production(db, po_doc, 100)
    assert read_models.report(db, "preparation", actor="helper@eternal.local")["total"] == 1
    assert read_models.report(db, "preparation", actor="op1@eternal.local")["total"] == 0
    assert read_models.report(db, "printing", accurate_po_id="99999")["total"] == 0


def test_report_allocations_and_deliveries(db: Database) -> None:
    po_doc = _seed(db)
    _open_gates(db)
    _run_production(db, po_doc, 200)
    sj = _sj(db, 200)
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=sj, idempotency_key="f-rep-load")
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=sj,
        recipient_name="Penerima Laporan",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("s.png", "image/png", PNG),
    )

    allocations = read_models.report(db, "allocations")["items"]
    assert allocations[0]["qty_printed"] == 200

    notes = read_models.report(db, "delivery-notes")["items"]
    assert notes[0]["loaded"] is True
    assert notes[0]["loaded_by"] == "driver@eternal.local"

    shipments = read_models.report(db, "deliveries")["items"]
    assert shipments[0]["status"] == "berhasil"
    assert shipments[0]["photo_count"] == 2
    assert shipments[0]["recipient_name"] == "Penerima Laporan"


def test_report_tasks_merges_confirmation_history(db: Database) -> None:
    po_doc = _seed(db)
    _open_gates(db)
    _run_production(db, po_doc, 150)
    rows = read_models.report(db, "tasks")["items"]
    types = {r["task_type"] for r in rows}
    assert {"persiapan-cup", "screen", "cetak", "qc-packing"} <= types
    assert all("created_at" in r for r in rows)


def test_unknown_report_kind_rejected(db: Database) -> None:
    with pytest.raises(ValueError):
        read_models.report(db, "laba-rugi")
