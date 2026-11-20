"""Aturan terkunci: revisi SJ sebagian item + pembatalan Loading (Opsi B)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import production_service as prod
from app.services import scheduling_service
from app.services import task_engine
from app.services.task_engine import TaskPermissionError

PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NON_PRINT = "106319"
SJ_ID = "SJ-REV-1"
PNG = b"\x89PNG rev"

OWNER = {"email_normalized": "owner@eternal.local", "role": "super_admin"}
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


def _sj(qty_print: float, *, extra_items: list[dict] | None = None, **over) -> dict:
    payload = {
        "id": SJ_ID,
        "number": "SJ/REV/0001",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-25",
        "toAddress": "ALAMAT-REV",
        "detailItem": [
            {
                "id": "REV-L1",
                "salesOrderDetailId": LINE_PRINT,
                "itemNo": "BRG-SIM-001",
                "quantity": qty_print,
                "itemUnitName": "PCS",
            }
        ],
    }
    if extra_items:
        payload["detailItem"].extend(extra_items)
    payload.update(over)
    return payload


def _non_print_item(qty: float, line_id: str = "REV-L2") -> dict:
    return {
        "id": line_id,
        "salesOrderDetailId": LINE_NON_PRINT,
        "itemNo": "BRG-SIM-002",
        "quantity": qty,
        "itemUnitName": "PACK",
    }


def _packed(db: Database, qty: float) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted
    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji rev"
        )
    po_doc = get_by_accurate_id(db, PO_ID)
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_PRINT,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="rev-sched",
    )
    alloc = res["allocations"][0]["allocation_id"]
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=qty,
        idempotency_key="rev-prep",
    )
    prod.confirm_screen(db, SCREENER, allocation_id=alloc, qty=20, idempotency_key="rev-screen")
    from tests.sample_flow_helpers import approve_sample

    approve_sample(db, OPERATOR, HEAD, alloc)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=qty, idempotency_key="rev-print"
    )
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=qty,
        idempotency_key="rev-qc",
    )
    return po_doc


def _grant_cancel(db: Database, role: str = "driver") -> None:
    """Izin pembatalan diberikan lewat Master, bukan default role."""
    master.seed_master_defaults(db)
    task_engine.set_role_actions(
        db,
        OWNER,
        role=role,
        task_type="pengiriman",
        actions=["view", "input", "confirm", "manage_evidence", "cancel"],
    )


def _qc_consumed(db: Database) -> float:
    qc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT)) or {}
    return float(qc.get("qty_consumed") or 0)


# --- A. Revisi sebelum Loading --------------------------------------------


def test_revision_before_loading_keeps_history_and_allows_normal_loading(
    db: Database,
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(200, extra_items=[_non_print_item(50)]))
    result = delivery.ingest_delivery_note(
        db, OWNER, _sj(300, extra_items=[_non_print_item(50)])
    )

    assert result["source_changed"] is True
    assert result["sj_version"] == 2
    changed = result["changed_items"]
    assert len(changed) == 1
    assert changed[0]["sj_line_id"] == "REV-L1"
    assert changed[0]["qty_before"] == 200
    assert changed[0]["qty_after"] == 300
    assert result["revision_status"] == deliveries.REVISION_ACTIVE

    revisions = deliveries.list_note_revisions(db, SJ_ID)
    assert [r["version"] for r in revisions] == [1, 2]
    assert revisions[1]["snapshot_before"]["items"][0]["qty"] == 200

    loading = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-load-a"
    )
    assert loading["duplicate"] is False


def test_three_items_only_one_changes(db: Database) -> None:
    _packed(db, 500)
    base = _sj(
        200,
        extra_items=[_non_print_item(50), _non_print_item(10, line_id="REV-L3")],
    )
    delivery.ingest_delivery_note(db, OWNER, base)
    revised = _sj(
        200,
        extra_items=[_non_print_item(75), _non_print_item(10, line_id="REV-L3")],
    )
    result = delivery.ingest_delivery_note(db, OWNER, revised)
    assert [c["sj_line_id"] for c in result["changed_items"]] == ["REV-L2"]
    assert result["changed_items"][0]["qty_after"] == 75


# --- B. Revisi setelah Loading --------------------------------------------


def test_revision_after_loading_locks_delivery(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-b-1")
    assert _qc_consumed(db) == 300

    result = delivery.ingest_delivery_note(db, OWNER, _sj(250))
    assert result["revision_status"] == deliveries.REVISION_NEEDS_RELOADING

    with pytest.raises(delivery.LoadingValidationError, match="direvisi"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-b-2"
        )


def test_cancel_loading_reverts_exact_consumed_qty(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-c-1")
    delivery.ingest_delivery_note(db, OWNER, _sj(250))

    _grant_cancel(db)
    cancelled = delivery.cancel_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi Qty dari administrasi"
    )
    assert cancelled["cancelled"] is True
    assert _qc_consumed(db) == 0
    # Konfirmasi QC & Packing tidak dihapus oleh reversal.
    qc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT))
    assert qc["qty_confirmed"] == 300

    history = deliveries.list_loading_events(db, SJ_ID)
    assert len(history) == 1
    assert history[0]["status"] == deliveries.LOADING_CANCELLED
    assert history[0]["cancel_reason"] == "Revisi Qty dari administrasi"
    assert history[0]["items"][0]["qty"] == 300


def test_reloading_uses_new_qty_and_single_driver_task(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-d-1")
    delivery.ingest_delivery_note(db, OWNER, _sj(250))
    _grant_cancel(db)
    delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi Qty")

    again = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-d-2"
    )
    assert again["duplicate"] is False
    assert _qc_consumed(db) == 250

    loadings = deliveries.list_loading_events(db, SJ_ID)
    assert [l["status"] for l in loadings] == [
        deliveries.LOADING_CANCELLED,
        deliveries.LOADING_ACTIVE,
    ]
    driver_tasks = tasks.list_tasks(db, task_type="pengiriman")
    assert len(driver_tasks) == 1
    assert driver_tasks[0]["context"]["loading_id"] == loadings[1]["loading_id"]
    assert driver_tasks[0]["context"]["loading_cancelled"] is False

    delivered = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima Setelah Revisi",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("s.png", "image/png", PNG),
    )
    assert delivered["status"] == deliveries.DELIVERY_SUCCESS


def test_qty_increase_after_loading_requires_more_qc(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(200))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-e-1")
    delivery.ingest_delivery_note(db, OWNER, _sj(400))
    _grant_cancel(db)
    delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Qty bertambah")

    validation = delivery.validate_loading(db, SJ_ID)
    assert validation["all_matched"] is False
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-e-2"
        )
    from app.repositories import master_repository as master

    master.set_role_actions(
        db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    task = delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    assert task["context"]["message"] == delivery.DISCREPANCY_MESSAGE


def test_mixed_sj_revision_reverts_only_print_items(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300, extra_items=[_non_print_item(50)]))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-f-1")
    assert _qc_consumed(db) == 300

    delivery.ingest_delivery_note(db, OWNER, _sj(300, extra_items=[_non_print_item(80)]))
    _grant_cancel(db)
    delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Qty Non-Cetak naik")
    assert _qc_consumed(db) == 0


# --- Concurrency, idempotency, crash --------------------------------------


def test_cancel_twice_does_not_double_revert(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-g-1")
    delivery.ingest_delivery_note(db, OWNER, _sj(250))
    _grant_cancel(db)

    first = delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi")
    second = delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi")
    assert first["cancelled"] is True
    assert second["cancelled"] is False
    assert second["duplicate"] is True
    assert _qc_consumed(db) == 0


def test_parallel_cancel_reverts_once(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-h-1")
    delivery.ingest_delivery_note(db, OWNER, _sj(250))
    _grant_cancel(db)

    def worker(i: int) -> str:
        try:
            res = delivery.cancel_loading(
                db, DRIVER, accurate_sj_id=SJ_ID, reason=f"Revisi paralel {i}"
            )
            return "cancelled" if res["cancelled"] else "duplicate"
        except Exception:  # noqa: BLE001
            return "rejected"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))

    assert results.count("cancelled") == 1
    assert _qc_consumed(db) == 0


def test_crash_during_cancel_leaves_state_consistent(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-i-1")
    delivery.ingest_delivery_note(db, OWNER, _sj(250))
    _grant_cancel(db)

    def _boom(*args, **kwargs):
        raise RuntimeError("proses mati sebelum commit pembatalan")

    monkeypatch.setattr(delivery.deliveries, "set_revision_status", _boom)
    with pytest.raises(RuntimeError):
        delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi crash")

    # Tidak boleh: Loading batal tetapi Qty belum kembali, atau sebaliknya.
    assert _qc_consumed(db) == 300
    assert deliveries.get_loading_event(db, SJ_ID) is not None

    monkeypatch.undo()
    recovered = delivery.cancel_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi setelah pulih"
    )
    assert recovered["cancelled"] is True
    assert _qc_consumed(db) == 0


# --- C/D. Riwayat gagal dan pengiriman berhasil ----------------------------


def test_failed_attempt_history_survives_reloading(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-j-1")
    delivery.confirm_delivery_failure(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Alamat tutup"
    )
    delivery.ingest_delivery_note(db, OWNER, _sj(250))
    _grant_cancel(db)
    delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi setelah gagal")
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-j-2")

    events = deliveries.list_delivery_events(db, SJ_ID)
    assert [e["status"] for e in events] == [deliveries.DELIVERY_FAILED]
    assert events[0]["failure_reason"] == "Alamat tutup"
    coordination = tasks.list_tasks(db, task_type="pengiriman-gagal-koordinasi")
    assert len(coordination) == 1


def test_delivered_sj_cannot_be_cancelled_and_is_marked_for_correction(
    db: Database,
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-k-1")
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima Sah",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("s.png", "image/png", PNG),
    )

    result = delivery.ingest_delivery_note(db, OWNER, _sj(250))
    assert result["revision_status"] == deliveries.REVISION_NEEDS_ADMIN_CORRECTION

    _grant_cancel(db)
    with pytest.raises(delivery.LoadingCancelError, match="bukti penerimaan"):
        delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="coba batal")

    # Bukti dan status historis tidak berubah.
    events = deliveries.list_delivery_events(db, SJ_ID)
    assert events[0]["status"] == deliveries.DELIVERY_SUCCESS
    assert events[0]["recipient_name"] == "Penerima Sah"
    assert len(delivery.active_delivery_evidence(db, SJ_ID)["photos"]) == 2
    assert _qc_consumed(db) == 300


# --- Permission dan audit --------------------------------------------------


def test_cancel_requires_master_permission(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-l-1")
    delivery.ingest_delivery_note(db, OWNER, _sj(250))

    # Default: tidak ada role operasional yang punya izin cancel.
    with pytest.raises(TaskPermissionError):
        delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="tanpa izin")
    assert _qc_consumed(db) == 300

    _grant_cancel(db)
    assert delivery.cancel_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="dengan izin Master"
    )["cancelled"] is True


def test_cancel_is_audited_with_reverted_items(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-m-1")
    delivery.ingest_delivery_note(db, OWNER, _sj(250))
    _grant_cancel(db)
    delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Audit uji")

    revision_events = list(db["audit_logs"].find({"action": "delivery.note.revision"}))
    assert revision_events[-1]["detail"]["revision_status"] == (
        deliveries.REVISION_NEEDS_RELOADING
    )
    assert revision_events[-1]["detail"]["changed_items"][0]["qty_before"] == 300

    cancel_events = list(db["audit_logs"].find({"action": "delivery.loading.cancel"}))
    assert len(cancel_events) == 1
    assert cancel_events[0]["detail"]["reverted_items"][0]["qty"] == 300
    assert cancel_events[0]["detail"]["reason"] == "Audit uji"


def test_replacement_sj_without_proven_relation_stays_blocked(db: Database) -> None:
    """SJ baru tanpa referensi pengganti terbukti tidak otomatis menggantikan."""
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="rev-n-1")

    delivery.ingest_delivery_note(
        db, OWNER, _sj(300, id="SJ-REV-NEW", number="SJ/REV/0002")
    )
    old = deliveries.get_delivery_note(db, SJ_ID)
    assert old["superseded_by"] is None
    # SJ baru tidak mewarisi Loading dan tidak bisa langsung di-Loading
    # karena Qty QC sudah dipakai SJ lama.
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id="SJ-REV-NEW", idempotency_key="rev-n-2"
        )
