"""SJ safety gaps — non-prod isolated; fake Accurate; no production.

Kontrak Accepted (Owner final):
- SJ boleh diingest sebelum PO Accepted (referensi/pending).
- Loading & pengiriman hanya setelah ``operational.acceptance.status == accepted``.
- Tidak auto-accept PO; tidak mengubah Gate Payment; PO missing/multi-SO fail-closed.
"""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import org_master_repository as org
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.services import delivery_service as delivery
from app.services import delivery_sync
from app.services.accurate_auto_sync.date_floor import detail_scope_outcome
from app.services.po_sync import sync_sales_order_payload

LIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"
)
OWNER = {
    "email_normalized": "owner@eternal.local",
    "role": "super_admin",
    "effective_module_permissions": ["access.all_branches"],
}
DRIVER = {
    "email_normalized": "driver@eternal.local",
    "role": "operator",
    "effective_module_permissions": ["access.all_branches"],
    "assigned_task_types": ["driver-pengiriman"],
}


@pytest.fixture(autouse=True)
def _enable_sj_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    from app.config import clear_settings_cache
    from app.services.accurate_auto_sync.date_floor import clear_date_floor_cache

    clear_settings_cache()
    clear_date_floor_cache()
    yield
    clear_settings_cache()
    clear_date_floor_cache()


def _seed_branch(db: Database, code: str = "50") -> None:
    org.ensure_org_master_indexes(db)
    org.upsert_branch(db, code=code, name=f"Cabang {code}", is_active=True)


def _po_payload(*, branch_id: int = 50, trans_date: str = "18/11/2026") -> dict:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["branchId"] = branch_id
    payload["transDate"] = trans_date
    return payload


def _seed_po_via_sync(db: Database, *, accepted: bool = False) -> dict:
    _seed_branch(db)
    out = sync_sales_order_payload(db, _po_payload(), mongo_guard="test")
    assert int(out.get("writes") or 0) >= 1
    assert get_by_accurate_id(db, "95106") is not None
    if accepted:
        from tests.po_accept_helpers import mark_po_accepted

        mark_po_accepted(db)
    return out


def _sj_detail(
    sj_id: int = 880201,
    *,
    so_id: int = 95106,
    trans_date: str = "05/11/2026",
    branch_name: str = "CABANG-50",
) -> dict:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = sj_id
    payload["number"] = f"TEST-SJ/GAP/{sj_id}"
    payload["transDate"] = trans_date
    payload["branch"] = {"name": branch_name, "id": 50}
    for item in payload["detailItem"]:
        item["salesOrderId"] = so_id
        if isinstance(item.get("salesOrder"), dict):
            item["salesOrder"]["id"] = so_id
        item["id"] = int(f"{sj_id}{int(item['id']) % 1000}")
    return payload


def test_sj_ingest_with_po_accepted(db: Database) -> None:
    _seed_po_via_sync(db, accepted=True)
    result = delivery_sync.sync_delivery_order_payload(
        db, OWNER, _sj_detail(880201), mongo_guard="test"
    )
    assert result["outcome"] in {"created", "updated", "unchanged"}
    assert deliveries.get_delivery_note(db, "880201") is not None


def test_sj_ingest_with_po_not_accepted(db: Database) -> None:
    """Kebijakan: ingest referensi boleh sebelum Accepted."""
    _seed_po_via_sync(db, accepted=False)
    po = get_by_accurate_id(db, "95106")
    assert (po.get("operational") or {}).get("acceptance", {}).get("status") != "accepted"
    result = delivery_sync.sync_delivery_order_payload(
        db, OWNER, _sj_detail(880202), mongo_guard="test"
    )
    assert result["outcome"] == "created"
    note = deliveries.get_delivery_note(db, "880202")
    assert note is not None
    # Belum Delivered otomatis.
    assert not deliveries.has_successful_delivery_event(db, "880202")


def test_sj_ingest_po_missing_fail_closed(db: Database) -> None:
    result = delivery_sync.sync_delivery_order_payload(
        db, OWNER, _sj_detail(880203, so_id=999001), mongo_guard="test"
    )
    assert result["outcome"] == "skipped"
    assert result["reason"] == "po_not_registered"
    assert deliveries.get_delivery_note(db, "880203") is None


def test_sj_branch_label_mismatch_does_not_block_ingest(db: Database) -> None:
    """Saat ini sync SJ tidak membandingkan branch SJ vs PO.branch_id.

    Fail-closed branch ambiguous di jalur PO ingest; mismatch label SJ = RISK
    terdokumentasi — bukan auto-delivered / bukan gate payment.
    """
    _seed_po_via_sync(db, accepted=False)
    po = get_by_accurate_id(db, "95106")
    assert str(po.get("branch_id")) == "50"
    detail = _sj_detail(880204, branch_name="CABANG-LAIN-150")
    result = delivery_sync.sync_delivery_order_payload(
        db, OWNER, detail, mongo_guard="test"
    )
    assert result["outcome"] == "created"
    note = deliveries.get_delivery_note(db, "880204")
    assert note["branch_name"] == "CABANG-LAIN-150"


def test_sj_date_floor_31_oct_excluded() -> None:
    gated = detail_scope_outcome(
        {"id": 1, "transDate": "31/10/2026"}, accurate_id="1"
    )
    assert gated is not None
    assert gated["outcome"] == "excluded"
    assert gated["reason"] == "before_date_floor"
    assert gated["writes"] == 0


def test_sj_date_floor_1_nov_included() -> None:
    gated = detail_scope_outcome(
        {"id": 2, "transDate": "01/11/2026"}, accurate_id="2"
    )
    assert gated is None


def test_sj_sync_blocked_during_maintenance_writer_fence(db: Database) -> None:
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        release_po_repair_maintenance,
    )

    _seed_po_via_sync(db)
    handle = acquire_po_repair_maintenance(db, ttl_seconds=60)
    try:
        with pytest.raises(PoRepairMaintenanceError):
            delivery_sync.sync_delivery_order_payload(
                db, OWNER, _sj_detail(880205), mongo_guard="test"
            )
        assert deliveries.get_delivery_note(db, "880205") is None
    finally:
        release_po_repair_maintenance(
            db,
            owner_token=handle["owner_token"],
            job_tokens=handle.get("job_tokens"),
        )


def test_tracking_seed_survives_sj_resync(db: Database) -> None:
    """Regresi fixture tracking: seed PO via sync+branch lalu SJ resync idempotent."""
    _seed_po_via_sync(db, accepted=True)
    assert get_by_accurate_id(db, "95106") is not None
    first = delivery_sync.sync_delivery_order_payload(
        db, OWNER, _sj_detail(880206), mongo_guard="test"
    )
    second = delivery_sync.sync_delivery_order_payload(
        db, OWNER, _sj_detail(880206), mongo_guard="test"
    )
    assert first["outcome"] == "created"
    assert second["outcome"] == "unchanged"
    assert get_by_accurate_id(db, "95106") is not None
    assert not deliveries.has_successful_delivery_event(db, "880206")


def test_sj_resync_not_auto_delivered(db: Database) -> None:
    _seed_po_via_sync(db, accepted=True)
    delivery_sync.sync_delivery_order_payload(
        db, OWNER, _sj_detail(880207), mongo_guard="test"
    )
    delivery_sync.sync_delivery_order_payload(
        db, OWNER, _sj_detail(880207), mongo_guard="test"
    )
    assert deliveries.get_delivery_note(db, "880207") is not None
    assert deliveries.list_delivery_events(db, "880207") == []
    assert deliveries.get_loading_event(db, "880207") is None


def test_loading_not_duplicated_on_retry(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Idempotensi Loading — tidak menggandakan event aktif."""
    upsert_sales_order(db, normalize_sales_order(_po_payload()))
    from tests.po_accept_helpers import mark_po_accepted

    mark_po_accepted(db)
    note_payload = _sj_detail(880208)
    delivery.ingest_delivery_note(db, OWNER, note_payload)
    _stub_loading_ok(monkeypatch, note_payload)
    first = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="880208", idempotency_key="load-a"
    )
    second = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="880208", idempotency_key="load-b"
    )
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert db["loading_events"].count_documents({"accurate_sj_id": "880208"}) == 1


def test_soft_delete_evidence_keeps_audit(db: Database) -> None:
    from datetime import datetime, timezone

    from app.repositories import evidence_repository as evidence
    from app.services.audit_log import append_audit_event

    evidence.ensure_evidence_indexes(db)
    evidence_id = "evid:delivery-photo:gap:v1"
    evidence.evidence_collection(db).insert_one(
        {
            "evidence_id": evidence_id,
            "scope": "delivery-photo",
            "scope_id": "880209",
            "version": 1,
            "deleted_at": None,
            "gridfs_id": "000000000000000000000001",
            "context": {"accurate_sj_id": "880209", "delivery_commit": "committed"},
            "created_at": datetime.now(timezone.utc),
        }
    )
    deleted = evidence.soft_delete_evidence(
        db,
        evidence_id=evidence_id,
        actor_email="driver@eternal.local",
        reason="uji gap",
    )
    assert deleted.get("deleted_at") is not None
    append_audit_event(
        db,
        action="delivery.evidence.soft_delete",
        actor_email="driver@eternal.local",
        target_email=None,
        detail={"evidence_id": evidence_id, "reason": "uji gap"},
    )
    doc = evidence.evidence_collection(db).find_one({"evidence_id": evidence_id})
    assert doc is not None
    assert doc.get("deleted_at") is not None
    assert doc.get("delete_reason") == "uji gap"
    assert db["audit_logs"].count_documents({"action": "delivery.evidence.soft_delete"}) >= 1


def test_concurrent_sj_sync_idempotent(db: Database) -> None:
    """Race label outcome boleh >1 'created'; dokumen SJ tetap satu (idempotent)."""
    _seed_po_via_sync(db, accepted=True)
    detail = _sj_detail(880210)

    def _once() -> str:
        out = delivery_sync.sync_delivery_order_payload(
            db, OWNER, detail, mongo_guard="test"
        )
        return str(out.get("outcome"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: _once(), range(4)))
    assert set(outcomes) <= {"created", "unchanged", "updated"}
    assert db["accurate_delivery_notes"].count_documents({"accurate_sj_id": "880210"}) == 1
    note = deliveries.get_delivery_note(db, "880210")
    assert note is not None
    assert int(note.get("sj_version") or 1) == 1
    assert not deliveries.has_successful_delivery_event(db, "880210")


def test_sj_default_production_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    from app.config import clear_settings_cache
    from app.services.delivery_sync import resolve_sj_mongo_guard
    from app.services.accurate_auto_sync import sj_job

    clear_settings_cache()
    assert resolve_sj_mongo_guard() == "test"
    note = sj_job.sj_auto_sync_settings_note()
    assert note["sj_sync_enabled"] is False
    assert note["sj_sync_production_enabled"] is False


def _stub_loading_ok(monkeypatch: pytest.MonkeyPatch, note_payload: dict) -> None:
    line0 = note_payload["detailItem"][0]
    monkeypatch.setattr(
        delivery,
        "validate_loading",
        lambda *_a, **_k: {
            "all_matched": True,
            "items": [
                {
                    "sj_line_id": str(line0["id"]),
                    "accurate_line_id": str(
                        line0.get("salesOrderDetailId") or line0["id"]
                    ),
                    "item_code": str(line0.get("itemNo") or "GAP-ITEM"),
                    "qty": float(line0["quantity"]),
                    "unit": str(line0.get("itemUnitName") or "PCS"),
                    "process_kind": "Non-Cetak",
                    "ledger_mode": "legacy_non_cetak",
                }
            ],
        },
    )
    monkeypatch.setattr(delivery, "require_action", lambda *_a, **_k: None)
    monkeypatch.setattr(
        delivery, "_require_loading_permission", lambda *_a, **_k: None
    )
    monkeypatch.setattr(delivery, "_assert_loading_assignee", lambda *_a, **_k: None)
    monkeypatch.setattr(delivery, "_prepare_delivery_txn", lambda *_a, **_k: "driver")


def _payment_gate_snapshot(db: Database, po_id: str = "95106") -> dict:
    from app.repositories import gate_repository as gates

    g = gates.get_gates(db, po_id)
    pay = g.get("payment") if isinstance(g, dict) else None
    if not isinstance(pay, dict):
        return {"state": None, "reason": None}
    return {"state": pay.get("state"), "reason": pay.get("reason")}


def test_loading_blocked_before_po_accepted(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.po_acceptance import PoAcceptanceError

    _seed_po_via_sync(db, accepted=False)
    before_gate = _payment_gate_snapshot(db)
    note_payload = _sj_detail(880301)
    delivery.ingest_delivery_note(db, OWNER, note_payload)
    _stub_loading_ok(monkeypatch, note_payload)

    with pytest.raises(PoAcceptanceError, match="belum diterima"):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id="880301", idempotency_key="load-pending"
        )

    assert deliveries.get_loading_event(db, "880301") is None
    assert db["loading_events"].count_documents({"accurate_sj_id": "880301"}) == 0
    po = get_by_accurate_id(db, "95106")
    assert (po.get("operational") or {}).get("acceptance", {}).get("status") != "accepted"
    assert _payment_gate_snapshot(db) == before_gate


def test_loading_allowed_after_po_accepted(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_po_via_sync(db, accepted=True)
    before_gate = _payment_gate_snapshot(db)
    note_payload = _sj_detail(880302)
    delivery.ingest_delivery_note(db, OWNER, note_payload)
    _stub_loading_ok(monkeypatch, note_payload)

    out = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id="880302", idempotency_key="load-ok-acc"
    )
    assert out["duplicate"] is False
    assert deliveries.get_loading_event(db, "880302") is not None
    assert _payment_gate_snapshot(db) == before_gate
    po = get_by_accurate_id(db, "95106")
    assert (po.get("operational") or {}).get("acceptance", {}).get("status") == "accepted"


def test_delivery_blocked_before_po_accepted_no_partial_evidence(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed Loading historis + PO pending → delivery baru ditolak tanpa staging."""
    from app.services.po_acceptance import PoAcceptanceError

    _seed_po_via_sync(db, accepted=False)
    before_gate = _payment_gate_snapshot(db)
    note_payload = _sj_detail(880303)
    delivery.ingest_delivery_note(db, OWNER, note_payload)
    note = deliveries.get_delivery_note(db, "880303")
    assert note is not None
    # Simulasikan Loading historis tanpa melewati guard Accepted baru.
    deliveries.insert_loading_event(
        db,
        accurate_sj_id="880303",
        accurate_po_id="95106",
        sj_no=str(note["sj_no"]),
        items=[
            {
                "sj_line_id": str(note_payload["detailItem"][0]["id"]),
                "accurate_line_id": str(
                    note_payload["detailItem"][0].get("salesOrderDetailId") or "0"
                ),
                "item_code": "GAP",
                "qty": 1.0,
                "unit": "PCS",
                "process_kind": "Non-Cetak",
            }
        ],
        actor_email="driver@eternal.local",
        idempotency_key="hist-load-880303",
        sj_version=int(note.get("sj_version") or 1),
        sequence=1,
    )
    monkeypatch.setattr(delivery, "require_action", lambda *_a, **_k: None)
    png = b"\x89PNG gap"

    with pytest.raises(PoAcceptanceError, match="belum diterima"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id="880303",
            recipient_name="Penerima",
            photos=[("a.png", "image/png", png), ("b.png", "image/png", png)],
            signature=("s.png", "image/png", png),
        )

    assert deliveries.list_delivery_events(db, "880303") == []
    assert db["evidence_files"].count_documents({"scope_id": "880303"}) == 0
    assert _payment_gate_snapshot(db) == before_gate
    po = get_by_accurate_id(db, "95106")
    assert (po.get("operational") or {}).get("acceptance", {}).get("status") != "accepted"


def test_sj_ingest_does_not_open_payment_gate_or_auto_accept(db: Database) -> None:
    from app.repositories import gate_repository as gates

    _seed_po_via_sync(db, accepted=False)
    before = _payment_gate_snapshot(db)
    assert before["state"] in {None, "closed", gates.GATE_CLOSED}
    delivery_sync.sync_delivery_order_payload(
        db, OWNER, _sj_detail(880304), mongo_guard="test"
    )
    po = get_by_accurate_id(db, "95106")
    assert (po.get("operational") or {}).get("acceptance", {}).get("status") != "accepted"
    assert _payment_gate_snapshot(db) == before
    assert not deliveries.has_successful_delivery_event(db, "880304")


def test_multi_so_sj_normalizes_without_singular_parent() -> None:
    payload = _sj_detail(880305)
    payload["detailItem"][0]["salesOrderId"] = 95106
    if len(payload["detailItem"]) < 2:
        payload["detailItem"].append(copy.deepcopy(payload["detailItem"][0]))
        payload["detailItem"][1]["id"] = int(payload["detailItem"][0]["id"]) + 1
    payload["detailItem"][1]["salesOrderId"] = 99999
    if isinstance(payload["detailItem"][1].get("salesOrder"), dict):
        payload["detailItem"][1]["salesOrder"]["id"] = 99999
    note = deliveries.normalize_delivery_note(payload)
    assert note["is_multi_po"] is True
    assert set(note["accurate_po_ids"]) == {"95106", "99999"}
    assert not note.get("accurate_po_id")
