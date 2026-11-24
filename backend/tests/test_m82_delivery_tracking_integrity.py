"""M8.2 Delivery & Tracking Integrity — focused matrix (critical transitions)."""

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
from app.repositories import evidence_repository as evidence
from app.repositories import gate_repository as gates
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import loading_sj_bootstrap as boot
from app.services.object_auth import ObjectAuthError
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, PoAcceptanceError
from app.services.task_engine import TaskPermissionError
from tests.po_accept_helpers import mark_po_accepted

PO_ID = "95106"
LINE_NC = "106319"
SJ_ID = "SJ-M82-DEL"

DRIVER = {
    "email_normalized": "driver@eternal.local",
    "role": "user",
    "operational_role": "driver",
    "effective_module_permissions": ["access.all_branches"],
}
FOREIGN = {
    "email_normalized": "foreign-driver@eternal.local",
    "role": "user",
    "operational_role": "driver",
    "branch_ids": ["999"],
    "effective_module_permissions": [],
}
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
PNG = b"\x89PNG m82"


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_LOADING_SJ_TASK_ENABLED", "1")
    monkeypatch.setenv("V4_LOADING_PACKING_LEDGER_ENFORCED", "0")
    clear_settings_cache()


def _sj(*, qty: float = 5, sj_id: str = SJ_ID) -> dict:
    return {
        "id": sj_id,
        "number": f"SJ/M82/{sj_id}",
        "salesOrderId": PO_ID,
        "salesOrderNumber": "PO/18/11/2026/000010",
        "transDate": "2026-11-20",
        "toAddress": "ALAMAT-M82",
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


def _seed_po(db: Database, *, accepted: bool = True) -> dict:
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
        for gate in ("design", "payment"):
            gates.set_gate(
                db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="m82"
            )
    else:
        db["accurate_sales_orders"].update_one(
            {"accurate_id": PO_ID},
            {"$set": {"operational.acceptance.status": "pending"}},
        )
    return get_by_accurate_id(db, PO_ID)


def _stub_loading_ok(monkeypatch: pytest.MonkeyPatch, note: dict) -> None:
    items = note.get("items") or []
    line0 = items[0] if items else {"accurate_line_id": LINE_NC, "qty": 5}
    monkeypatch.setattr(
        delivery,
        "validate_loading",
        lambda *_a, **_k: {
            "all_matched": True,
            "items": [
                {
                    "sj_line_id": "SJL-N",
                    "accurate_line_id": str(line0.get("accurate_line_id") or LINE_NC),
                    "item_code": "BRG",
                    "qty": float(line0.get("qty") or 5),
                    "unit": "PCS",
                    "process_kind": "Non-Cetak",
                    "ledger_mode": "legacy_non_cetak",
                    "matched": True,
                    "qty_available": float(line0.get("qty") or 5),
                    "accurate_po_id": PO_ID,
                }
            ],
        },
    )


def _loaded(
    db: Database, monkeypatch: pytest.MonkeyPatch, *, accepted: bool = True
) -> dict:
    _env(monkeypatch)
    _seed_po(db, accepted=accepted)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    if accepted:
        _stub_loading_ok(monkeypatch, note)
        delivery.confirm_loading(
            db, HELPER, accurate_sj_id=SJ_ID, idempotency_key="m82-load"
        )
    return note


def _photos(n: int = 2) -> list[tuple[str, str, bytes]]:
    return [(f"p{i}.png", "image/png", PNG + bytes([i])) for i in range(n)]


def _sig() -> tuple[str, str, bytes]:
    return ("sig.png", "image/png", b"\x89PNG sig")


# --- T01 / T02 ---


def test_t01_loading_creates_driver_task(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    rows = tasks.list_tasks(db, task_type="pengiriman")
    assert len(rows) == 1
    assert rows[0]["ref"]["accurate_delivery_note_id"] == SJ_ID


def test_t02_missing_loading_blocks_delivery(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=True)
    boot.verify_loading_sj_contract(db)
    deliveries.upsert_delivery_note(db, _sj())
    with pytest.raises(delivery.LoadingValidationError, match="Loading"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=_photos(),
            signature=_sig(),
        )


def test_t03_non_accepted_blocks_delivery(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=False)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    # Historic loading without Accepted (safety-gap shape).
    deliveries.insert_loading_event(
        db,
        accurate_sj_id=SJ_ID,
        accurate_po_id=PO_ID,
        sj_no=str(note["sj_no"]),
        items=[
            {
                "sj_line_id": "SJL-N",
                "accurate_line_id": LINE_NC,
                "item_code": "BRG",
                "qty": 5.0,
                "unit": "PCS",
                "process_kind": "Non-Cetak",
            }
        ],
        actor_email="driver@eternal.local",
        idempotency_key="m82-hist-load",
        sj_version=1,
        sequence=1,
    )
    with pytest.raises(PoAcceptanceError, match="belum diterima"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=_photos(),
            signature=_sig(),
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []
    assert db["evidence_files"].count_documents({"scope_id": SJ_ID}) == 0


def test_t04_unauthorized_role_rejected(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    with pytest.raises(TaskPermissionError):
        delivery.confirm_delivery_success(
            db,
            HELPER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=_photos(),
            signature=_sig(),
        )


def test_t05_cross_branch_rejected(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    db["accurate_delivery_notes"].update_one(
        {"accurate_sj_id": SJ_ID}, {"$set": {"branch_id": "150"}}
    )
    with pytest.raises(ObjectAuthError):
        delivery.confirm_delivery_success(
            db,
            FOREIGN,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=_photos(),
            signature=_sig(),
        )


def test_t06_missing_sj_rejected(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    _loaded(db, monkeypatch)
    with pytest.raises(LookupError, match="tidak ditemukan"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id="SJ-MISSING",
            recipient_name="Penerima",
            photos=_photos(),
            signature=_sig(),
        )


def test_t07_t08_t09_evidence_required(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    with pytest.raises(delivery.DeliveryEvidenceError, match="Nama penerima"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="  ",
            photos=_photos(),
            signature=_sig(),
        )
    with pytest.raises(delivery.DeliveryEvidenceError, match="minimal 2 foto"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=_photos(1),
            signature=_sig(),
        )
    with pytest.raises(delivery.DeliveryEvidenceError, match="Tanda tangan"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=_photos(),
            signature=("sig.png", "image/png", b""),
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []


def test_t10_t11_valid_evidence_and_event(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    event = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima Uji",
        photos=_photos(),
        signature=_sig(),
    )
    assert event["status"] == deliveries.DELIVERY_SUCCESS
    assert event["recipient_name"] == "Penerima Uji"
    assert len(event["photo_evidence_ids"]) == 2
    assert event["signature_evidence_id"]
    stored = delivery.active_delivery_evidence(db, SJ_ID)
    assert len(stored["photos"]) >= 2
    assert len(stored["signatures"]) >= 1
    driver = tasks.list_tasks(db, task_type="pengiriman")[0]
    assert driver["status"] == tasks.TASK_STATUS_DONE


def test_t12_t14_idempotent_retry_without_reupload(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    first = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=_photos(),
        signature=_sig(),
    )
    # Retry with empty evidence must still return the committed event.
    second = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="",
        photos=[],
        signature=("sig.png", "image/png", b""),
    )
    assert first["delivery_event_id"] == second["delivery_event_id"]
    assert (
        db["delivery_events"].count_documents(
            {"accurate_sj_id": SJ_ID, "status": deliveries.DELIVERY_SUCCESS}
        )
        == 1
    )


def test_t13_concurrent_success_protected(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)

    def _once(i: int) -> str:
        ev = delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name=f"Penerima-{i}",
            photos=_photos(),
            signature=_sig(),
        )
        return str(ev["delivery_event_id"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(_once, range(4)))
    assert len(set(ids)) == 1
    assert (
        db["delivery_events"].count_documents(
            {"accurate_sj_id": SJ_ID, "status": deliveries.DELIVERY_SUCCESS}
        )
        == 1
    )


def test_t15_t16_partial_policy_one_success_per_sj(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema: one successful delivery per SJ — remainder multi-success = POLICY HOLD."""
    _loaded(db, monkeypatch)
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=_photos(),
        signature=_sig(),
    )
    # Second "partial" attempt returns same event (no double-count).
    again = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima-2",
        photos=_photos(),
        signature=_sig(),
    )
    assert again["status"] == deliveries.DELIVERY_SUCCESS
    assert (
        db["delivery_events"].count_documents(
            {"accurate_sj_id": SJ_ID, "status": deliveries.DELIVERY_SUCCESS}
        )
        == 1
    )


def test_t17_historical_evidence_preserved_on_soft_delete(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    event = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=_photos(),
        signature=_sig(),
    )
    eid = event["photo_evidence_ids"][0]
    # Grant manage_evidence to driver for soft-delete path in this fixture.
    from app.repositories import master_repository as master

    master.set_role_actions(
        db,
        role="driver",
        task_type="pengiriman",
        actions=["view", "confirm", "manage_evidence"],
    )
    delivery.soft_delete_delivery_evidence(
        db, DRIVER, evidence_id=eid, reason="uji soft-delete"
    )
    meta = evidence.get_evidence(db, eid)
    assert meta is not None
    assert meta.get("deleted_at") is not None
    # Event still references the evidence id (history preserved).
    persisted = deliveries.list_delivery_events(db, SJ_ID)[0]
    assert eid in (persisted.get("photo_evidence_ids") or [])


def test_t18_unauthorized_evidence_access_rejected(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.evidence_access import assert_delivery_evidence_access

    _loaded(db, monkeypatch)
    note = deliveries.get_delivery_note(db, SJ_ID)
    stranger = {
        "email_normalized": "screener@eternal.local",
        "role": "user",
        "operational_role": "screener",
        "effective_module_permissions": ["access.all_branches"],
    }
    with pytest.raises(ObjectAuthError):
        assert_delivery_evidence_access(db, stranger, note=note or {})


def test_t19_t20_tracking_matches_delivery_state(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.tracking_progress import compute_progress, evaluate_milestones
    from app.services.tracking_workflow_adapter import (
        eligible_delivered_sj_qty,
        load_workflow_snapshot,
    )

    _loaded(db, monkeypatch)
    po = get_by_accurate_id(db, PO_ID)
    wf_before = load_workflow_snapshot(db, PO_ID)
    progress_before = compute_progress(
        evaluate_milestones(po, wf_before), po, wf_before
    )
    assert float(progress_before["shipping"]["qty_delivered"] or 0) == 0.0

    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=_photos(),
        signature=_sig(),
    )
    wf = load_workflow_snapshot(db, PO_ID)
    progress = compute_progress(evaluate_milestones(po, wf), po, wf)
    eligible = eligible_delivered_sj_qty(wf)
    assert any(str(r.get("accurate_sj_id")) == SJ_ID for r in eligible), (
        f"eligible={eligible} notes={[n.get('accurate_sj_id') for n in (wf.get('delivery_notes') or [])]} "
        f"events={len(wf.get('delivery_events') or [])}"
    )
    assert float(progress["shipping"]["qty_delivered"] or 0) > 0


def test_t22_failure_then_success_preserves_history(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    delivery.confirm_delivery_failure(db, DRIVER, accurate_sj_id=SJ_ID, reason="Tutup")
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=_photos(),
        signature=_sig(),
    )
    statuses = [e["status"] for e in deliveries.list_delivery_events(db, SJ_ID)]
    assert deliveries.DELIVERY_FAILED in statuses
    assert deliveries.DELIVERY_SUCCESS in statuses


def test_t23_audit_append_on_success(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    before = db["audit_logs"].count_documents({"action": "delivery.confirm.success"})
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=_photos(),
        signature=_sig(),
    )
    after = db["audit_logs"].count_documents({"action": "delivery.confirm.success"})
    assert after == before + 1
    # Idempotent retry must not invent a second success business event; audit may stay.
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="",
        photos=[],
        signature=("x.png", "image/png", b""),
    )
    assert (
        db["delivery_events"].count_documents(
            {"accurate_sj_id": SJ_ID, "status": deliveries.DELIVERY_SUCCESS}
        )
        == 1
    )


def test_t24_m81_loading_assign_still_requires_accepted(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    _seed_po(db, accepted=False)
    boot.verify_loading_sj_contract(db)
    note = deliveries.upsert_delivery_note(db, _sj())["delivery_note"]
    delivery.ensure_loading_sj_task(db, note)
    with pytest.raises(PoAcceptanceError):
        delivery.assign_loading_sj_task(
            db, HEAD, accurate_sj_id=SJ_ID, assigned_user_email="helper@eternal.local"
        )


def test_t25_design_policy_a_untouched_by_delivery(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _loaded(db, monkeypatch)
    before = gates.get_gates(db, PO_ID)
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=_photos(),
        signature=_sig(),
    )
    after = gates.get_gates(db, PO_ID)
    assert before.get("design") == after.get("design")
    assert before.get("payment") == after.get("payment")
    assert hasattr(gates, "design_unlocks_scheduling")


def test_t03b_idempotent_success_still_requires_accepted(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After success, un-accepting is not a real path; simulate pending + historic success."""
    _loaded(db, monkeypatch)
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=_photos(),
        signature=_sig(),
    )
    db["accurate_sales_orders"].update_one(
        {"accurate_id": PO_ID},
        {"$set": {"operational.acceptance.status": "pending"}},
    )
    with pytest.raises(PoAcceptanceError):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="",
            photos=[],
            signature=("x.png", "image/png", b""),
        )
