"""P8C — native multi-PO SJ + identity hold + recovery guards (isolated Mongo)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

from app.domain.operational import TaskRef, build_task_key
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import org_master_repository as org
from app.repositories import task_repository as tasks
from app.repositories.po_repository import ensure_po_indexes, get_by_accurate_id
from app.services import delivery_sync
from app.services.delivery_service import LOADING_SJ_TASK
from app.services.accurate_auto_sync.sj_sync_review import (
    REASON_DELIVERY_NOTE_IDENTITY,
    REASON_PO_NOT_REGISTERED,
    get_sj_sync_review,
)
from app.services.accurate_auto_sync.status import ensure_auto_sync_status_indexes
from app.services.historical_po_recovery import (
    HistoricalRecoveryError,
    RecoveryApproval,
    assert_runtime_approval,
    load_sealed_manifest,
)
from app.services.po_sync import sync_sales_order_payload

LIVE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"
)
OWNER = {
    "email_normalized": "owner@eternal.local",
    "role": "super_admin",
    "effective_module_permissions": ["access.all_branches"],
}


@pytest.fixture(autouse=True)
def _enable_sj(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "1")
    monkeypatch.setenv("V4_SJ_SYNC_PRODUCTION_ENABLED", "0")
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    monkeypatch.setenv("V4_LOADING_SJ_TASK_ENABLED", "1")
    from app.config import clear_settings_cache
    from app.services.accurate_auto_sync.date_floor import clear_date_floor_cache

    clear_settings_cache()
    clear_date_floor_cache()
    yield
    clear_settings_cache()
    clear_date_floor_cache()


@pytest.fixture()
def sdb(db: Database) -> Database:
    ensure_auto_sync_status_indexes(db)
    ensure_po_indexes(db)
    deliveries.ensure_delivery_indexes(db)
    org.ensure_org_master_indexes(db)
    org.upsert_branch(db, code="50", name="Cabang 50", is_active=True)
    from app.repositories import master_repository as master

    try:
        master.ensure_master_indexes(db)
    except Exception:  # noqa: BLE001
        pass
    db["master_task_types"].update_one(
        {"task_type": LOADING_SJ_TASK},
        {
            "$set": {
                "task_type": LOADING_SJ_TASK,
                "label": "Loading Surat Jalan",
                "scope": "delivery_note",
                "assigned_role": "helper",
                "is_active": True,
            }
        },
        upsert=True,
    )
    return db


def _sj(
    sj_id: int,
    *,
    so_ids: list[int],
    trans_date: str = "05/11/2026",
    detail_ids: list[int] | None = None,
    qtys: list[float] | None = None,
) -> dict[str, Any]:
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    payload["id"] = sj_id
    payload["number"] = f"TEST-SJ/P8C/{sj_id}"
    payload["transDate"] = trans_date
    base = payload["detailItem"][0]
    items = []
    for i, so_id in enumerate(so_ids):
        item = copy.deepcopy(base)
        item["id"] = int(f"{sj_id}{i + 1}")
        item["salesOrderId"] = so_id
        if detail_ids is not None:
            item["salesOrderDetailId"] = detail_ids[i]
        else:
            item["salesOrderDetailId"] = int(f"{so_id}{i + 1}")
        if qtys is not None:
            item["quantity"] = qtys[i]
        if isinstance(item.get("salesOrder"), dict):
            item["salesOrder"]["id"] = so_id
            item["salesOrder"]["number"] = f"PO/{so_id}"
        items.append(item)
    payload["detailItem"] = items
    return payload


def _seed_po(db: Database, accurate_id: str, *, branch_id: int = 50) -> None:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id)
    payload["branchId"] = branch_id
    payload["number"] = f"PO/{accurate_id}"
    payload["transDate"] = "18/11/2026"
    # Pastikan line id cocok dengan SJ fixture bila dipanggil dengan detail khusus.
    out = sync_sales_order_payload(db, payload, mongo_guard="test")
    assert int(out.get("writes") or 0) >= 1
    assert get_by_accurate_id(db, accurate_id) is not None


def test_single_so_regression_normalize_and_fields() -> None:
    note = deliveries.normalize_delivery_note(_sj(880001, so_ids=[95106]))
    assert note["is_multi_po"] is False
    assert note["accurate_po_id"] == "95106"
    assert note["accurate_po_ids"] == ["95106"]
    assert note["items"][0]["accurate_po_id"] == "95106"


def test_multi_so_two_and_three_parents_normalize() -> None:
    two = deliveries.normalize_delivery_note(_sj(880002, so_ids=[111, 222]))
    assert two["is_multi_po"] is True
    assert two["accurate_po_ids"] == ["111", "222"]
    assert not two.get("accurate_po_id")
    assert {i["accurate_po_id"] for i in two["items"]} == {"111", "222"}

    three = deliveries.normalize_delivery_note(_sj(880003, so_ids=[10, 20, 30]))
    assert three["accurate_po_ids"] == ["10", "20", "30"]
    assert len(three["parent_associations"]) == 3


def test_same_item_code_different_so_keeps_attribution() -> None:
    payload = _sj(880004, so_ids=[501, 502], detail_ids=[9001, 9002])
    for item in payload["detailItem"]:
        item["item"] = {"no": "100115", "name": "SAME"}
        item["itemNo"] = "100115"
    note = deliveries.normalize_delivery_note(payload)
    assert note["items"][0]["item_code"] == note["items"][1]["item_code"]
    assert note["items"][0]["accurate_po_id"] == "501"
    assert note["items"][1]["accurate_po_id"] == "502"
    assert note["items"][0]["accurate_line_id"] != note["items"][1]["accurate_line_id"]


def test_duplicate_sales_order_detail_rejected() -> None:
    payload = _sj(880005, so_ids=[701, 701], detail_ids=[555, 555])
    with pytest.raises(deliveries.DeliveryNoteIdentityError) as exc:
        deliveries.normalize_delivery_note(payload)
    assert exc.value.code == "SO_DETAIL_DUPLICATE"


def test_multi_line_missing_so_on_one_item_rejected() -> None:
    # Tiga baris / dua SO: baris ketiga tanpa SO → jangan wariskan parent sembarang.
    payload = _sj(880006, so_ids=[801, 802, 801])
    payload["detailItem"][2].pop("salesOrderId", None)
    payload["detailItem"][2]["salesOrder"] = True  # boolean Accurate — bukan SO
    with pytest.raises(deliveries.DeliveryNoteIdentityError):
        deliveries.normalize_delivery_note(payload)


def test_sj_120851_null_so_and_detail_rejected() -> None:
    payload = _sj(120851, so_ids=[95106])
    for item in payload["detailItem"]:
        item.pop("salesOrderId", None)
        item.pop("salesOrderDetailId", None)
        item["salesOrder"] = True
    with pytest.raises(deliveries.DeliveryNoteIdentityError):
        deliveries.normalize_delivery_note(payload)


def test_multi_ingest_requires_all_parents(sdb: Database) -> None:
    _seed_po(sdb, "95106")
    detail = _sj(119979, so_ids=[95106, 94581])
    first = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert first["outcome"] == "skipped"
    assert first["reason"] == REASON_PO_NOT_REGISTERED
    assert "94581" in (first.get("missing_po_ids") or [])
    assert deliveries.get_delivery_note(sdb, "119979") is None

    _seed_po(sdb, "94581")
    second = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert second["outcome"] == "created"
    assert second["is_multi_po"] is True
    assert set(second["accurate_po_ids"]) == {"94581", "95106"}
    note = deliveries.get_delivery_note(sdb, "119979")
    assert note is not None
    assert note["is_multi_po"] is True
    assert not note.get("accurate_po_id")
    assert get_sj_sync_review(sdb, "119979")["status"] == "resolved"


def test_multi_ingest_idempotent_and_loading_one_per_sj(sdb: Database) -> None:
    _seed_po(sdb, "91902")
    _seed_po(sdb, "94106")
    detail = _sj(120806, so_ids=[91902, 94106])
    first = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    second = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, detail, mongo_guard="test"
    )
    assert first["outcome"] == "created"
    assert second["outcome"] == "unchanged"
    note = deliveries.get_delivery_note(sdb, "120806")
    assert note is not None
    key = build_task_key(
        LOADING_SJ_TASK,
        "delivery_note",
        TaskRef(accurate_delivery_note_id="120806"),
    )
    task = tasks.get_task(sdb, key)
    assert task is not None
    assert task["context"].get("is_multi_po") is True
    assert set(task["context"].get("accurate_po_ids") or []) == {"91902", "94106"}
    assert tasks.tasks_collection(sdb).count_documents(
        {"task_type": LOADING_SJ_TASK}
    ) == 1
    assert sdb["loading_events"].count_documents({}) == 0
    assert sdb["delivery_events"].count_documents({}) == 0


def test_list_notes_finds_multi_under_each_parent(sdb: Database) -> None:
    _seed_po(sdb, "111")
    _seed_po(sdb, "222")
    detail = _sj(880010, so_ids=[111, 222])
    delivery_sync.sync_delivery_order_payload(sdb, OWNER, detail, mongo_guard="test")
    a = deliveries.list_delivery_notes(sdb, accurate_po_id="111")
    b = deliveries.list_delivery_notes(sdb, accurate_po_id="222")
    assert any(n["accurate_sj_id"] == "880010" for n in a)
    assert any(n["accurate_sj_id"] == "880010" for n in b)


def test_legacy_single_so_document_still_loads(sdb: Database) -> None:
    """Dokumen lama tanpa accurate_po_ids tetap terbaca helper parent."""
    sdb[deliveries.DELIVERY_NOTES_COLLECTION].insert_one(
        {
            "accurate_sj_id": "legacy-1",
            "sj_no": "LEGACY/1",
            "accurate_po_id": "95106",
            "po_no": "PO/95106",
            "items": [
                {
                    "sj_line_id": "1",
                    "accurate_line_id": "10",
                    "item_code": "X",
                    "qty": 1,
                }
            ],
            "ship_date": "05/11/2026",
        }
    )
    note = deliveries.get_delivery_note(sdb, "legacy-1")
    assert deliveries.note_parent_po_ids(note) == ["95106"]
    assert deliveries.note_is_multi_po(note) is False
    assert deliveries.item_belongs_to_po(
        note["items"][0], accurate_po_id="95106", note=note
    )


def test_120851_sync_identity_quarantine_no_loading(sdb: Database) -> None:
    payload = _sj(120851, so_ids=[95106])
    for item in payload["detailItem"]:
        item.pop("salesOrderId", None)
        item.pop("salesOrderDetailId", None)
        item["salesOrder"] = True
    result = delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, payload, mongo_guard="test"
    )
    assert result["outcome"] == "review_required"
    assert result["reason"] == REASON_DELIVERY_NOTE_IDENTITY
    assert deliveries.get_delivery_note(sdb, "120851") is None
    assert tasks.tasks_collection(sdb).count_documents(
        {"task_type": LOADING_SJ_TASK}
    ) == 0


def test_historical_recovery_apply_guard_and_dry_approval(sdb: Database) -> None:
    """Reuse P6 sealed tooling — dry approval OK; APPLY tanpa execute gagal."""
    del sdb  # approval-only; no Mongo mutation
    manifest = load_sealed_manifest()
    dry = RecoveryApproval(
        mode="dry_run",
        expect_manifest_sha256=manifest.manifest_sha256,
        expect_po_count=manifest.po_count,
        expect_batch_id=manifest.batch_id,
    )
    assert_runtime_approval(dry, manifest=manifest, app_env="test")

    bad_apply = RecoveryApproval(
        mode="apply",
        expect_manifest_sha256=manifest.manifest_sha256,
        expect_po_count=manifest.po_count,
        expect_batch_id=manifest.batch_id,
        execute=False,
    )
    with pytest.raises(HistoricalRecoveryError) as ei:
        assert_runtime_approval(bad_apply, manifest=manifest, app_env="test")
    assert ei.value.code == "apply_execute_required"


def test_no_accepted_payment_inventory_on_multi_ingest(sdb: Database) -> None:
    _seed_po(sdb, "333")
    _seed_po(sdb, "444")
    before_po = get_by_accurate_id(sdb, "333")
    before_acc = (before_po.get("operational") or {}).get("acceptance")
    delivery_sync.sync_delivery_order_payload(
        sdb, OWNER, _sj(880020, so_ids=[333, 444]), mongo_guard="test"
    )
    after_po = get_by_accurate_id(sdb, "333")
    assert (after_po.get("operational") or {}).get("acceptance") == before_acc
    assert sdb["loading_events"].count_documents({}) == 0
    assert sdb["delivery_events"].count_documents({}) == 0
