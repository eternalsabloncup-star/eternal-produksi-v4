"""Checkpoint E: SJ Accurate, Loading, selisih Qty, Driver, bukti, soft delete."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import evidence_repository as evidence
from app.repositories import gate_repository as gates
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import production_service as prod
from app.services import scheduling_service
from app.services.task_engine import TaskPermissionError

PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NON_PRINT = "106319"
SJ_ID = "SJ-TEST-9001"

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
PNG = b"\x89PNG bukti"


def _sj_payload(**overrides) -> dict:
    payload = {
        "id": SJ_ID,
        "number": "SJ/TEST/18/11/2026/0001",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-20",
        "toAddress": "ALAMAT-TEST",
        "shipmentName": "OUTLET-TEST",
        "detailItem": [
            {
                "id": "SJL-1",
                "salesOrderDetailId": LINE_PRINT,
                "itemNo": "BRG-SIM-001",
                "detailName": "Item Cetak",
                "quantity": 300,
                "itemUnitName": "PCS",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _seed_po(db: Database) -> dict:
    upsert_sales_order(db, normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE))
    from tests.po_accept_helpers import mark_po_accepted
    mark_po_accepted(db)
    for gate in ("design", "payment"):
        gates.set_gate(
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="uji E"
        )
    return get_by_accurate_id(db, PO_ID)


def _produce_packed(db: Database, packed_qty: float) -> dict:
    """Jalankan produksi sampai QC & Packing sejumlah ``packed_qty``."""
    po_doc = _seed_po(db)
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_PRINT,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key="e-sched-1",
    )
    alloc = res["allocations"][0]["allocation_id"]
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=packed_qty,
        idempotency_key="e-prep",
    )
    prod.confirm_screen(db, SCREENER, allocation_id=alloc, qty=50, idempotency_key="e-screen")
    from tests.sample_flow_helpers import approve_sample

    approve_sample(db, OPERATOR, HEAD, alloc)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=packed_qty, idempotency_key="e-print"
    )
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=packed_qty,
        idempotency_key="e-qc",
    )
    return po_doc


# --- Sinkronisasi SJ -------------------------------------------------------


def test_delivery_note_sync_is_idempotent(db: Database) -> None:
    _seed_po(db)
    first = deliveries.upsert_delivery_note(db, _sj_payload())
    second = deliveries.upsert_delivery_note(db, _sj_payload())
    assert first["was_insert"] is True
    assert second["was_insert"] is False
    assert len(deliveries.list_delivery_notes(db, accurate_po_id=PO_ID)) == 1


def test_one_po_can_have_many_delivery_notes(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj_payload())
    deliveries.upsert_delivery_note(
        db,
        _sj_payload(
            id="SJ-TEST-9002",
            number="SJ/TEST/0002",
            detailItem=[
                {
                    "id": "SJL-2",
                    "salesOrderDetailId": LINE_NON_PRINT,
                    "itemNo": "BRG-SIM-002",
                    "quantity": 50,
                    "itemUnitName": "PACK",
                }
            ],
        ),
    )
    notes = deliveries.list_delivery_notes(db, accurate_po_id=PO_ID)
    assert len(notes) == 2
    assert {n["accurate_sj_id"] for n in notes} == {"SJ-TEST-9001", "SJ-TEST-9002"}


def test_sync_fails_closed_without_po_line_reference(db: Database) -> None:
    _seed_po(db)
    payload = _sj_payload()
    del payload["detailItem"][0]["salesOrderDetailId"]
    with pytest.raises(deliveries.DeliveryNoteIdentityError, match="accurate_line_id"):
        deliveries.upsert_delivery_note(db, payload)
    assert deliveries.list_delivery_notes(db) == []


def test_sync_rejects_duplicate_sj_line_ids(db: Database) -> None:
    _seed_po(db)
    payload = _sj_payload()
    payload["detailItem"].append(dict(payload["detailItem"][0]))
    with pytest.raises(deliveries.DeliveryNoteIdentityError, match="duplikat"):
        deliveries.upsert_delivery_note(db, payload)


def test_source_change_is_recorded(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(db, _sj_payload())
    changed = deliveries.upsert_delivery_note(
        db,
        _sj_payload(
            detailItem=[
                {
                    "id": "SJL-1",
                    "salesOrderDetailId": LINE_PRINT,
                    "itemNo": "BRG-SIM-001",
                    "quantity": 250,
                    "itemUnitName": "PCS",
                }
            ]
        ),
    )
    assert changed["source_changed"] is True
    assert changed["delivery_note"]["items"][0]["qty"] == 250


# --- Validasi Loading ------------------------------------------------------


def test_loading_blocked_when_qc_insufficient(db: Database) -> None:
    _produce_packed(db, 100)
    deliveries.upsert_delivery_note(db, _sj_payload())
    validation = delivery.validate_loading(db, SJ_ID)
    assert validation["all_matched"] is False
    assert "QC & Packing tersedia 100" in validation["items"][0]["reason"]

    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-blocked-1"
        )
    assert deliveries.get_loading_event(db, SJ_ID) is None


def test_loading_succeeds_when_qc_available(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())
    result = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-ok-1"
    )
    assert result["duplicate"] is False
    assert len(result["loading"]["items"]) == 1
    # Qty QC terkonsumsi sehingga tidak dapat dipakai SJ lain.
    qc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT))
    assert qc["qty_consumed"] == 300


def test_qc_qty_cannot_be_used_by_two_delivery_notes(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())
    deliveries.upsert_delivery_note(
        db, _sj_payload(id="SJ-TEST-9003", number="SJ/TEST/0003")
    )
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-first"
    )
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id="SJ-TEST-9003", idempotency_key="load-second"
        )


def test_non_print_item_follows_sj_without_production_ledger(db: Database) -> None:
    _seed_po(db)
    deliveries.upsert_delivery_note(
        db,
        _sj_payload(
            id="SJ-NONPRINT",
            number="SJ/TEST/NONPRINT",
            detailItem=[
                {
                    "id": "SJL-NP",
                    "salesOrderDetailId": LINE_NON_PRINT,
                    "itemNo": "BRG-SIM-002",
                    "quantity": 50,
                    "itemUnitName": "PACK",
                }
            ],
        ),
    )
    validation = delivery.validate_loading(db, "SJ-NONPRINT")
    assert validation["all_matched"] is True
    assert validation["items"][0]["process_kind"] == "Non-Cetak"
    result = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="SJ-NONPRINT", idempotency_key="load-nonprint"
    )
    assert result["duplicate"] is False


def test_mixed_sj_validated_as_one_document(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(
        db,
        _sj_payload(
            id="SJ-MIXED",
            number="SJ/TEST/MIXED",
            detailItem=[
                {
                    "id": "SJL-C",
                    "salesOrderDetailId": LINE_PRINT,
                    "itemNo": "BRG-SIM-001",
                    "quantity": 300,
                    "itemUnitName": "PCS",
                },
                {
                    "id": "SJL-N",
                    "salesOrderDetailId": LINE_NON_PRINT,
                    "itemNo": "BRG-SIM-002",
                    "quantity": 50,
                    "itemUnitName": "PACK",
                },
            ],
        ),
    )
    validation = delivery.validate_loading(db, "SJ-MIXED")
    assert [c["process_kind"] for c in validation["items"]] == ["Cetak", "Non-Cetak"]
    assert validation["all_matched"] is True
    loading = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="SJ-MIXED", idempotency_key="load-mixed"
    )
    assert len(loading["loading"]["items"]) == 2


def test_superseded_sj_cannot_be_loaded(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())
    deliveries.mark_superseded(db, accurate_sj_id=SJ_ID, replacement_sj_id="SJ-TEST-9009")
    with pytest.raises(delivery.LoadingValidationError, match="digantikan"):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-superseded"
        )


def test_replacement_sj_loads_normally_without_extra_approval(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload(detailItem=[
        {
            "id": "SJL-1",
            "salesOrderDetailId": LINE_PRINT,
            "itemNo": "BRG-SIM-001",
            "quantity": 400,
            "itemUnitName": "PCS",
        }
    ]))
    from app.repositories import master_repository as master

    master.set_role_actions(
        db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    deliveries.mark_superseded(db, accurate_sj_id=SJ_ID, replacement_sj_id="SJ-FIX")
    deliveries.upsert_delivery_note(
        db, _sj_payload(id="SJ-FIX", number="SJ/TEST/FIX")
    )
    result = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="SJ-FIX", idempotency_key="load-fix"
    )
    assert result["duplicate"] is False


def test_loading_confirmation_is_single_per_sj(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())
    first = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-once-1"
    )
    second = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-once-2"
    )
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert db["loading_events"].count_documents({"accurate_sj_id": SJ_ID}) == 1
    driver_tasks = tasks.list_tasks(db, task_type="pengiriman")
    assert len(driver_tasks) == 1


def test_parallel_loading_does_not_double_consume(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())

    def worker(i: int) -> str:
        try:
            res = delivery.confirm_loading(
                db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key=f"load-par-{i}"
            )
            return "duplicate" if res["duplicate"] else "ok"
        except Exception:  # noqa: BLE001
            return "rejected"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))

    assert results.count("ok") == 1
    assert db["loading_events"].count_documents({"accurate_sj_id": SJ_ID}) == 1
    qc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT))
    assert qc["qty_consumed"] == 300


def test_crash_after_reservation_rolls_back_qc_consumption(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proses mati setelah reservasi: transaksi batal, Qty QC tidak terpakai."""
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())

    def _boom(*args, **kwargs):
        raise RuntimeError("proses mati sebelum event Loading tertulis")

    monkeypatch.setattr(delivery.deliveries, "insert_loading_event", _boom)
    with pytest.raises(RuntimeError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-crash-1"
        )

    qc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT))
    assert float(qc.get("qty_consumed") or 0) == 0
    assert deliveries.get_loading_event(db, SJ_ID) is None

    monkeypatch.undo()
    recovered = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-crash-retry"
    )
    assert recovered["duplicate"] is False
    assert tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT))["qty_consumed"] == 300


def test_loading_requires_permission(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())
    with pytest.raises(TaskPermissionError):
        delivery.confirm_loading(
            db, SCREENER, accurate_sj_id=SJ_ID, idempotency_key="load-denied"
        )


# --- Perbedaan Qty ---------------------------------------------------------


def test_discrepancy_creates_single_red_coordination_task(db: Database) -> None:
    _produce_packed(db, 100)
    deliveries.upsert_delivery_note(db, _sj_payload())
    from app.repositories import master_repository as master

    master.set_role_actions(
        db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    first = delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    second = delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    assert first["task_key"] == second["task_key"]
    rows = tasks.list_tasks(db, task_type="qty-loading-sj-berbeda")
    assert len(rows) == 1
    assert rows[0]["coordination"] is True
    assert rows[0]["assigned_role"] == "production-head"
    assert rows[0]["context"]["message"] == delivery.DISCREPANCY_MESSAGE
    assert rows[0]["context"]["mismatched_items"][0]["qty_sj"] == 300


def test_coordination_confirmation_does_not_unlock_loading(db: Database) -> None:
    _produce_packed(db, 100)
    deliveries.upsert_delivery_note(db, _sj_payload())
    from app.repositories import master_repository as master

    master.set_role_actions(
        db,
        role="production-head",
        task_type="loading-sj",
        actions=["view", "confirm", "edit", "cancel"],
    )
    task = delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    delivery.confirm_coordination(
        db, HEAD, task_key=task["task_key"], idempotency_key="coord-1"
    )
    done = tasks.get_task(db, task["task_key"])
    assert done["status"] == tasks.TASK_STATUS_DONE

    # Tidak mengubah SJ, Qty QC, atau membuka Loading.
    assert deliveries.get_delivery_note(db, SJ_ID)["items"][0]["qty"] == 300
    qc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT))
    assert qc["qty_confirmed"] == 100
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-after-coord"
        )


# --- Driver ----------------------------------------------------------------


def _loaded_sj(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="load-driver-setup"
    )


def test_driver_task_created_once_per_sj(db: Database) -> None:
    _loaded_sj(db)
    rows = tasks.list_tasks(db, task_type="pengiriman")
    assert len(rows) == 1
    assert rows[0]["ref"]["accurate_delivery_note_id"] == SJ_ID
    assert rows[0]["assigned_role"] == "driver"


def test_delivery_success_requires_two_photos_and_signature(db: Database) -> None:
    _loaded_sj(db)
    with pytest.raises(delivery.DeliveryEvidenceError, match="minimal 2 foto"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima Uji",
            photos=[("p1.png", "image/png", PNG)],
            signature=("sig.png", "image/png", PNG),
        )
    with pytest.raises(delivery.DeliveryEvidenceError, match="Tanda tangan"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima Uji",
            photos=[("p1.png", "image/png", PNG), ("p2.png", "image/png", PNG)],
            signature=("sig.png", "image/png", b""),
        )
    with pytest.raises(delivery.DeliveryEvidenceError, match="Nama penerima"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="  ",
            photos=[("p1.png", "image/png", PNG), ("p2.png", "image/png", PNG)],
            signature=("sig.png", "image/png", PNG),
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []


def test_delivery_success_stores_evidence_and_courier(db: Database) -> None:
    _loaded_sj(db)
    event = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima Uji",
        photos=[
            ("p1.png", "image/png", PNG),
            ("p2.png", "image/png", b"\x89PNG dua"),
            ("p3.png", "image/png", b"\x89PNG tiga"),
        ],
        signature=("sig.png", "image/png", b"\x89PNG ttd"),
    )
    assert event["status"] == deliveries.DELIVERY_SUCCESS
    assert event["courier_email"] == "driver@eternal.local"
    assert len(event["photo_evidence_ids"]) == 3
    assert event["signature_evidence_id"]

    stored = delivery.active_delivery_evidence(db, SJ_ID)
    assert len(stored["photos"]) == 3
    assert all(p["sha256"] for p in stored["photos"])
    driver_task = tasks.list_tasks(db, task_type="pengiriman")[0]
    assert driver_task["status"] == tasks.TASK_STATUS_DONE


def test_delivery_failure_creates_red_coordination_task(db: Database) -> None:
    _loaded_sj(db)
    result = delivery.confirm_delivery_failure(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Alamat tutup"
    )
    assert result["event"]["status"] == deliveries.DELIVERY_FAILED

    coordination = tasks.get_task(db, result["coordination_task_key"])
    assert coordination["coordination"] is True
    assert coordination["assigned_role"] == "production-head"
    assert coordination["status"] == tasks.TASK_STATUS_OPEN
    assert coordination["context"]["failure_reason"] == "Alamat tutup"

    delivery.confirm_coordination(
        db, HEAD, task_key=result["coordination_task_key"], idempotency_key="coord-fail-1"
    )
    # Koordinasi selesai, tetapi pengiriman tetap gagal dan tidak dijadwal ulang.
    assert tasks.get_task(db, result["coordination_task_key"])["status"] == (
        tasks.TASK_STATUS_DONE
    )
    events = deliveries.list_delivery_events(db, SJ_ID)
    assert [e["status"] for e in events] == [deliveries.DELIVERY_FAILED]


def test_failure_reason_is_required(db: Database) -> None:
    _loaded_sj(db)
    with pytest.raises(delivery.DeliveryEvidenceError):
        delivery.confirm_delivery_failure(db, DRIVER, accurate_sj_id=SJ_ID, reason="  ")


def test_delivery_attempts_keep_history_without_duplicating_sj(db: Database) -> None:
    _loaded_sj(db)
    delivery.confirm_delivery_failure(db, DRIVER, accurate_sj_id=SJ_ID, reason="Gagal 1")
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima Kedua",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("sig.png", "image/png", PNG),
    )
    events = deliveries.list_delivery_events(db, SJ_ID)
    assert [e["attempt"] for e in events] == [1, 2]
    assert [e["status"] for e in events] == [
        deliveries.DELIVERY_FAILED,
        deliveries.DELIVERY_SUCCESS,
    ]
    assert len(deliveries.list_delivery_notes(db, accurate_po_id=PO_ID)) == 1


# --- Soft delete bukti -----------------------------------------------------


def test_soft_delete_keeps_file_and_audit(db: Database) -> None:
    _loaded_sj(db)
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima Uji",
        photos=[("p1.png", "image/png", PNG), ("p2.png", "image/png", PNG)],
        signature=("sig.png", "image/png", PNG),
    )
    photos = delivery.active_delivery_evidence(db, SJ_ID)["photos"]
    target = photos[0]["evidence_id"]

    delivery.soft_delete_delivery_evidence(
        db, DRIVER, evidence_id=target, reason="Foto buram"
    )
    active = delivery.active_delivery_evidence(db, SJ_ID)["photos"]
    assert target not in [p["evidence_id"] for p in active]

    archived = evidence.list_evidence(
        db, scope=delivery.DELIVERY_PHOTO_SCOPE, scope_id=SJ_ID, include_deleted=True
    )
    deleted = next(p for p in archived if p["evidence_id"] == target)
    assert deleted["deleted_by"] == "driver@eternal.local"
    assert deleted["delete_reason"] == "Foto buram"
    assert deleted["gridfs_id"]
    assert db["evidence.files"].count_documents({}) >= 3
    assert db["audit_logs"].count_documents(
        {"action": "delivery.evidence.soft_delete"}
    ) == 1


def test_soft_delete_requires_evidence_permission(db: Database) -> None:
    _loaded_sj(db)
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima Uji",
        photos=[("p1.png", "image/png", PNG), ("p2.png", "image/png", PNG)],
        signature=("sig.png", "image/png", PNG),
    )
    target = delivery.active_delivery_evidence(db, SJ_ID)["photos"][0]["evidence_id"]
    with pytest.raises(TaskPermissionError):
        delivery.soft_delete_delivery_evidence(
            db, SCREENER, evidence_id=target, reason="coba hapus"
        )


# --- Jadwal Pengiriman -----------------------------------------------------


def test_delivery_schedule_rows_and_summary(db: Database) -> None:
    _produce_packed(db, 300)
    deliveries.upsert_delivery_note(db, _sj_payload())
    deliveries.upsert_delivery_note(
        db,
        _sj_payload(
            id="SJ-TEST-9004",
            number="SJ/TEST/0004",
            transDate="2026-11-25",
            toAddress="ALAMAT-TEST-2",
            detailItem=[
                {
                    "id": "SJL-4",
                    "salesOrderDetailId": LINE_NON_PRINT,
                    "itemNo": "BRG-SIM-002",
                    "quantity": 50,
                    "itemUnitName": "PACK",
                }
            ],
        ),
    )
    schedule = delivery.delivery_schedule(db)
    assert schedule["summary"]["sj_count"] == 2
    assert schedule["summary"]["destination_count"] == 2
    # PCS dan PACK tidak dijumlahkan menjadi satu angka.
    assert schedule["summary"]["qty_by_unit"] == {"PCS": 300.0, "PACK": 50.0}

    window = delivery.delivery_schedule(
        db, ship_date_from="2026-11-24", ship_date_to="2026-11-26"
    )
    assert window["summary"]["sj_count"] == 1
    assert window["items"][0]["accurate_sj_id"] == "SJ-TEST-9004"


def test_schedule_marks_loading_and_delivery_status(db: Database) -> None:
    _loaded_sj(db)
    schedule = delivery.delivery_schedule(db)
    row = schedule["items"][0]
    assert row["loaded"] is True
    assert row["delivery_status"] == deliveries.DELIVERY_PENDING

    delivery.confirm_delivery_failure(db, DRIVER, accurate_sj_id=SJ_ID, reason="Gagal")
    assert delivery.delivery_schedule(db)["items"][0]["delivery_status"] == (
        deliveries.DELIVERY_FAILED
    )
