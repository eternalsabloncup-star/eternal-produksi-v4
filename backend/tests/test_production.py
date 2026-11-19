"""Checkpoint D: Persiapan, Screen, Sampel, Cetak, QC & Packing."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import gate_repository as gates
from app.repositories import sample_repository as samples
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import production_service as prod
from app.services import scheduling_service
from app.services.task_engine import TaskPermissionError

PO_ID = "95106"
LINE_A = "106318"
LINE_B = "106319"

HEAD = {
    "email_normalized": "kp@eternal.local",
    "role": "user",
    "operational_role": "production-head",
}
HELPER = {
    "email_normalized": "helper@eternal.local",
    "role": "user",
    "operational_role": "helper",
}
SCREENER = {
    "email_normalized": "screener@eternal.local",
    "role": "user",
    "operational_role": "screener",
}
OPERATOR = {
    "email_normalized": "op1@eternal.local",
    "role": "user",
    "operational_role": "operator",
}
OPERATOR_B = {
    "email_normalized": "op2@eternal.local",
    "role": "user",
    "operational_role": "operator",
}

PNG = b"\x89PNG sample"


def _two_print_lines_same_code() -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["detailItem"][1]["charField1"] = "Cetak"
    payload["detailItem"][1]["item"] = dict(payload["detailItem"][0]["item"])
    payload["detailItem"][1]["quantity"] = 500
    return payload


def _seed(db: Database, payload=None) -> dict:
    upsert_sales_order(db, normalize_sales_order(payload or SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted
    mark_po_accepted(db)
    doc = get_by_accurate_id(db, PO_ID)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji D"
        )
    return doc


def _schedule(
    db: Database, po_doc: dict, line_id: str, splits: list[tuple[str, float]], key: str
) -> list[str]:
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=line_id,
        allocations=[{"operator_email": op, "qty": q} for op, q in splits],
        idempotency_key=key,
    )
    return [a["allocation_id"] for a in res["allocations"]]


def _prep(db: Database, line_id: str, qty: float, key: str) -> dict:
    return prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=line_id,
        qty=qty,
        idempotency_key=key,
    )


def _screen(db: Database, allocation_id: str, qty: float, key: str) -> dict:
    return prod.confirm_screen(
        db, SCREENER, allocation_id=allocation_id, qty=qty, idempotency_key=key
    )


def _sample_approved(db: Database, allocation_id: str, operator=OPERATOR) -> None:
    prod.submit_sample(
        db,
        operator,
        allocation_id=allocation_id,
        filename="sample.png",
        content_type="image/png",
        content=PNG,
    )
    latest = samples.latest_sample(db, allocation_id)
    prod.decide_sample(
        db, HEAD, allocation_id=allocation_id, version=latest["version"], approved=True
    )


# --- Helper: Persiapan Cup -------------------------------------------------


def test_preparation_partial_confirmations(db: Database) -> None:
    po_doc = _seed(db)
    _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 1000)], "d-prep-1")

    _prep(db, LINE_A, 200, "prep-200")
    result = _prep(db, LINE_A, 300, "prep-300")
    quota = result["quota"]
    assert quota["qty_prepared"] == 500
    assert quota["qty_belum_disiapkan"] == 500
    assert quota["qty_available"] == 500
    history = tasks.list_confirmations(db, prod.prep_task_key(PO_ID, LINE_A))
    assert [h["qty"] for h in history] == [200, 300]
    assert all(h["actor_email"] == "helper@eternal.local" for h in history)


def test_preparation_over_target_rejected(db: Database) -> None:
    po_doc = _seed(db)
    _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 1000)], "d-prep-over")
    _prep(db, LINE_A, 900, "prep-900")
    with pytest.raises(Exception):
        _prep(db, LINE_A, 200, "prep-200-over")
    quota = prod.helper_quota_state(
        db, accurate_po_id=PO_ID, accurate_line_id=LINE_A
    )
    assert quota["qty_prepared"] == 900


def test_same_item_code_two_lines_keep_separate_quota(db: Database) -> None:
    po_doc = _seed(db, _two_print_lines_same_code())
    _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 100)], "d-sep-a")
    _schedule(db, po_doc, LINE_B, [("op1@eternal.local", 100)], "d-sep-b")
    _prep(db, LINE_A, 100, "prep-line-a")

    quota_a = prod.helper_quota_state(db, accurate_po_id=PO_ID, accurate_line_id=LINE_A)
    quota_b = prod.helper_quota_state(db, accurate_po_id=PO_ID, accurate_line_id=LINE_B)
    assert quota_a["qty_prepared"] == 100
    assert quota_b["qty_prepared"] == 0


# --- Screener --------------------------------------------------------------


def test_screen_is_per_allocation_and_partial(db: Database) -> None:
    po_doc = _seed(db)
    alloc_a, alloc_b = _schedule(
        db,
        po_doc,
        LINE_A,
        [("op1@eternal.local", 600), ("op2@eternal.local", 400)],
        "d-screen-1",
    )
    _screen(db, alloc_a, 50, "screen-a-50")
    _screen(db, alloc_a, 70, "screen-a-70")

    state = prod.line_production_state(db, po_doc, LINE_A)
    by_id = {a["allocation_id"]: a for a in state["allocations"]}
    assert by_id[alloc_a]["qty_screen"] == 120
    assert by_id[alloc_b]["qty_screen"] == 0
    # Qty screen tidak dipaksa sama dengan Qty jadwal.
    assert by_id[alloc_a]["qty"] == 600


def test_screen_of_operator_a_does_not_unlock_operator_b(db: Database) -> None:
    po_doc = _seed(db)
    alloc_a, alloc_b = _schedule(
        db,
        po_doc,
        LINE_A,
        [("op1@eternal.local", 500), ("op2@eternal.local", 500)],
        "d-screen-2",
    )
    _prep(db, LINE_A, 1000, "prep-screen-2")
    _screen(db, alloc_a, 10, "screen-only-a")

    with pytest.raises(prod.ProductionGateError, match="Screen"):
        prod.submit_sample(
            db,
            OPERATOR_B,
            allocation_id=alloc_b,
            filename="s.png",
            content_type="image/png",
            content=PNG,
        )


# --- Sample ----------------------------------------------------------------


def test_sample_versioning_reject_then_approve(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 500)], "d-s-1")
    _prep(db, LINE_A, 500, "prep-s-1")
    _screen(db, alloc_a, 10, "screen-s-1")

    prod.submit_sample(
        db,
        OPERATOR,
        allocation_id=alloc_a,
        filename="v1.png",
        content_type="image/png",
        content=PNG,
    )
    prod.decide_sample(
        db, HEAD, allocation_id=alloc_a, version=1, approved=False, reason="Warna meleset"
    )
    rejected = samples.list_samples(db, alloc_a)[0]
    assert rejected["status"] == samples.STATUS_REJECTED
    assert rejected["reject_reason"] == "Warna meleset"

    # Cetak tetap terkunci selama belum ada sampel approved.
    with pytest.raises(prod.ProductionGateError, match="Sampel"):
        prod.confirm_print(
            db, OPERATOR, allocation_id=alloc_a, qty=10, idempotency_key="print-locked"
        )

    prod.submit_sample(
        db,
        OPERATOR,
        allocation_id=alloc_a,
        filename="v2.png",
        content_type="image/png",
        content=b"\x89PNG revisi",
    )
    prod.decide_sample(db, HEAD, allocation_id=alloc_a, version=2, approved=True)

    all_versions = samples.list_samples(db, alloc_a)
    assert [s["version"] for s in all_versions] == [1, 2]
    assert all_versions[0]["status"] == samples.STATUS_REJECTED
    assert all_versions[1]["status"] == samples.STATUS_APPROVED


def test_sample_approval_does_not_unlock_other_allocation(db: Database) -> None:
    po_doc = _seed(db)
    alloc_a, alloc_b = _schedule(
        db,
        po_doc,
        LINE_A,
        [("op1@eternal.local", 500), ("op2@eternal.local", 500)],
        "d-s-2",
    )
    _prep(db, LINE_A, 1000, "prep-s-2")
    _screen(db, alloc_a, 10, "screen-s-2a")
    _screen(db, alloc_b, 10, "screen-s-2b")
    _sample_approved(db, alloc_a)

    assert samples.approved_sample(db, alloc_a) is not None
    assert samples.approved_sample(db, alloc_b) is None
    with pytest.raises(prod.ProductionGateError, match="Sampel"):
        prod.confirm_print(
            db, OPERATOR_B, allocation_id=alloc_b, qty=10, idempotency_key="print-b-1"
        )


def test_sample_upload_requires_operator_permission(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 100)], "d-s-3")
    _prep(db, LINE_A, 100, "prep-s-3")
    _screen(db, alloc_a, 10, "screen-s-3")
    with pytest.raises(TaskPermissionError):
        prod.submit_sample(
            db,
            SCREENER,
            allocation_id=alloc_a,
            filename="s.png",
            content_type="image/png",
            content=PNG,
        )


# --- Cetak -----------------------------------------------------------------


def test_print_partial_and_quota_consumption(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 800)], "d-p-1")
    _prep(db, LINE_A, 500, "prep-p-1")
    _screen(db, alloc_a, 100, "screen-p-1")
    _sample_approved(db, alloc_a)

    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=200, idempotency_key="print-200"
    )
    result = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=100, idempotency_key="print-100"
    )
    assert result["task"]["qty_confirmed"] == 300
    assert result["quota"]["qty_consumed"] == 300
    assert result["quota"]["qty_available"] == 200


def test_print_above_allocation_rejected(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 300)], "d-p-2")
    _prep(db, LINE_A, 1000, "prep-p-2")
    _screen(db, alloc_a, 50, "screen-p-2")
    _sample_approved(db, alloc_a)

    with pytest.raises(Exception):
        prod.confirm_print(
            db, OPERATOR, allocation_id=alloc_a, qty=400, idempotency_key="print-over"
        )
    # Gagal di tengah tidak boleh menyisakan kuota terpakai.
    quota = prod.helper_quota_state(db, accurate_po_id=PO_ID, accurate_line_id=LINE_A)
    assert quota["qty_consumed"] == 0
    assert quota["qty_available"] == 1000


def test_print_above_helper_quota_rejected(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 1000)], "d-p-3")
    _prep(db, LINE_A, 150, "prep-p-3")
    _screen(db, alloc_a, 50, "screen-p-3")
    _sample_approved(db, alloc_a)

    with pytest.raises(prod.ProductionQuantityError, match="Kuota"):
        prod.confirm_print(
            db, OPERATOR, allocation_id=alloc_a, qty=200, idempotency_key="print-quota"
        )
    quota = prod.helper_quota_state(db, accurate_po_id=PO_ID, accurate_line_id=LINE_A)
    assert quota["qty_consumed"] == 0


def test_print_double_submit_keeps_single_result(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 500)], "d-p-4")
    _prep(db, LINE_A, 500, "prep-p-4")
    _screen(db, alloc_a, 50, "screen-p-4")
    _sample_approved(db, alloc_a)

    first = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=120, idempotency_key="print-retry"
    )
    second = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=120, idempotency_key="print-retry"
    )
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["task"]["qty_confirmed"] == 120
    # Kuota hanya terpakai satu kali.
    assert second["quota"]["qty_consumed"] == 120


def test_two_operators_share_one_helper_quota(db: Database) -> None:
    po_doc = _seed(db)
    alloc_a, alloc_b = _schedule(
        db,
        po_doc,
        LINE_A,
        [("op1@eternal.local", 500), ("op2@eternal.local", 500)],
        "d-p-5",
    )
    _prep(db, LINE_A, 300, "prep-p-5")
    for alloc, op, key in ((alloc_a, OPERATOR, "a"), (alloc_b, OPERATOR_B, "b")):
        _screen(db, alloc, 10, f"screen-p5-{key}")
        _sample_approved(db, alloc, operator=op)

    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=200, idempotency_key="p5-a-200"
    )
    with pytest.raises(prod.ProductionQuantityError, match="Kuota"):
        prod.confirm_print(
            db, OPERATOR_B, allocation_id=alloc_b, qty=200, idempotency_key="p5-b-200"
        )
    ok = prod.confirm_print(
        db, OPERATOR_B, allocation_id=alloc_b, qty=100, idempotency_key="p5-b-100"
    )
    assert ok["quota"]["qty_available"] == 0
    assert prod.printed_total_for_line(
        db, accurate_po_id=PO_ID, accurate_line_id=LINE_A
    ) == 300


def test_concurrent_prints_never_exceed_shared_quota(db: Database) -> None:
    po_doc = _seed(db)
    alloc_a, alloc_b = _schedule(
        db,
        po_doc,
        LINE_A,
        [("op1@eternal.local", 500), ("op2@eternal.local", 500)],
        "d-p-6",
    )
    _prep(db, LINE_A, 600, "prep-p-6")
    for alloc, op, key in ((alloc_a, OPERATOR, "a"), (alloc_b, OPERATOR_B, "b")):
        _screen(db, alloc, 10, f"screen-p6-{key}")
        _sample_approved(db, alloc, operator=op)

    def worker(i: int) -> str:
        alloc = alloc_a if i % 2 == 0 else alloc_b
        actor = OPERATOR if i % 2 == 0 else OPERATOR_B
        try:
            prod.confirm_print(
                db,
                actor,
                allocation_id=alloc,
                qty=200,
                idempotency_key=f"p6-concurrent-{i}",
            )
            return "ok"
        except (prod.ProductionQuantityError, Exception):
            return "rejected"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(worker, range(6)))

    printed = prod.printed_total_for_line(
        db, accurate_po_id=PO_ID, accurate_line_id=LINE_A
    )
    quota = prod.helper_quota_state(db, accurate_po_id=PO_ID, accurate_line_id=LINE_A)
    assert results.count("ok") == 3
    assert printed == 600
    assert quota["qty_consumed"] == 600
    assert quota["qty_available"] == 0


def test_crash_mid_transaction_leaves_no_consumed_quota(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mati setelah kuota dipesan dan ledger ditulis, sebelum transaksi commit."""
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 500)], "d-crash-1")
    _prep(db, LINE_A, 500, "prep-crash")
    _screen(db, alloc_a, 50, "screen-crash")
    _sample_approved(db, alloc_a)

    def _boom(*args, **kwargs):
        raise RuntimeError("proses mati sebelum commit")

    monkeypatch.setattr(tasks, "_status_for", _boom)
    with pytest.raises(RuntimeError):
        prod.confirm_print(
            db, OPERATOR, allocation_id=alloc_a, qty=200, idempotency_key="print-crash"
        )

    quota = prod.helper_quota_state(db, accurate_po_id=PO_ID, accurate_line_id=LINE_A)
    assert quota["qty_consumed"] == 0
    assert quota["qty_available"] == 500
    assert tasks.list_confirmations(db, prod.print_task_key({
        "accurate_po_id": PO_ID,
        "po_no": "",
        "accurate_line_id": LINE_A,
        "item_code": "",
        "allocation_id": alloc_a,
    })) == []

    monkeypatch.undo()
    retried = prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=200, idempotency_key="print-crash"
    )
    assert retried["duplicate"] is False
    assert retried["quota"]["qty_consumed"] == 200


def test_print_requires_operator_permission(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 100)], "d-p-7")
    _prep(db, LINE_A, 100, "prep-p-7")
    _screen(db, alloc_a, 10, "screen-p-7")
    _sample_approved(db, alloc_a)
    with pytest.raises(TaskPermissionError):
        prod.confirm_print(
            db, HELPER, allocation_id=alloc_a, qty=10, idempotency_key="print-denied"
        )


# --- QC & Packing ----------------------------------------------------------


def _printed_line(db: Database, po_doc: dict) -> tuple[str, str]:
    alloc_a, alloc_b = _schedule(
        db,
        po_doc,
        LINE_A,
        [("op1@eternal.local", 500), ("op2@eternal.local", 500)],
        "d-qc-setup",
    )
    _prep(db, LINE_A, 1000, "prep-qc")
    for alloc, op, key in ((alloc_a, OPERATOR, "a"), (alloc_b, OPERATOR_B, "b")):
        _screen(db, alloc, 10, f"screen-qc-{key}")
        _sample_approved(db, alloc, operator=op)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=300, idempotency_key="qc-print-a"
    )
    prod.confirm_print(
        db, OPERATOR_B, allocation_id=alloc_b, qty=200, idempotency_key="qc-print-b"
    )
    return alloc_a, alloc_b


def test_qc_task_appears_after_partial_print(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 500)], "d-qc-1")
    _prep(db, LINE_A, 500, "prep-qc-1")
    _screen(db, alloc_a, 10, "screen-qc-1")
    _sample_approved(db, alloc_a)
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_A)) is None

    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=150, idempotency_key="qc1-print"
    )
    qc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_A))
    assert qc is not None
    assert qc["qty_target"] == 150
    assert qc["assigned_role"] == "helper"


def test_qc_packing_partial_across_operators(db: Database) -> None:
    po_doc = _seed(db)
    _printed_line(db, po_doc)

    first = prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_A,
        qty=300,
        idempotency_key="qc-pack-300",
    )
    assert first["qty_printed"] == 500
    assert first["qty_ready_to_ship"] == 300

    second = prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_A,
        qty=200,
        idempotency_key="qc-pack-200",
    )
    assert second["qty_ready_to_ship"] == 500
    state = prod.line_production_state(db, po_doc, LINE_A)
    assert state["qty_waiting_qc"] == 0


def test_overpacking_rejected(db: Database) -> None:
    po_doc = _seed(db)
    _printed_line(db, po_doc)
    with pytest.raises(Exception):
        prod.confirm_qc_packing(
            db,
            HELPER,
            accurate_po_id=PO_ID,
            accurate_line_id=LINE_A,
            qty=600,
            idempotency_key="qc-over",
        )
    state = prod.line_production_state(db, po_doc, LINE_A)
    assert state["qty_packed"] == 0


def test_non_print_line_has_no_qc_task(db: Database) -> None:
    po_doc = _seed(db)
    _printed_line(db, po_doc)
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_B)) is None


def test_production_actions_are_audited(db: Database) -> None:
    po_doc = _seed(db)
    (alloc_a,) = _schedule(db, po_doc, LINE_A, [("op1@eternal.local", 200)], "d-aud-1")
    _prep(db, LINE_A, 200, "prep-aud")
    _screen(db, alloc_a, 10, "screen-aud")
    _sample_approved(db, alloc_a)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc_a, qty=50, idempotency_key="print-aud"
    )

    actions = {e["action"] for e in db["audit_logs"].find({})}
    assert "production.sample.upload" in actions
    assert "production.sample.approve" in actions
    assert "task.confirm.cetak" in actions
    assert "task.confirm.persiapan-cup" in actions
    assert "task.confirm.screen" in actions
