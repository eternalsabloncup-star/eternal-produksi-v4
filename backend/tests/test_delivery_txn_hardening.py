"""Regresi transaksi Loading / cancel / revisi SJ + index/sync guards."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from pymongo.database import Database

from app.config import clear_settings_cache, get_settings
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories import delivery_repository as deliveries
from app.repositories import evidence_repository as evidence
from app.repositories import gate_repository as gates
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id, upsert_sales_order
from app.repositories import master_repository as master
from app.services import delivery_service as delivery
from app.services import production_service as prod
from app.services import scheduling_service
from app.services import task_engine
from app.services.index_preflight import (
    MissingRequiredIndexError,
    assert_preflight_target_safe,
    clear_index_verification_cache,
    inspect_required_indexes,
    mark_index_specs_verified,
    mongo_target_cache_key,
)

PO_ID = "95106"
LINE_PRINT = "106318"
LINE_NON_PRINT = "106319"
SJ_ID = "SJ-TXN-9001"

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
PNG = b"\x89PNG bukti"


def _sj(qty: float = 300, **overrides) -> dict:
    payload = {
        "id": SJ_ID,
        "number": "SJ/TXN/0001",
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
                "quantity": qty,
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
            db, accurate_po_id=PO_ID, gate=gate, state=gates.GATE_OPEN, reason="txn"
        )
    return get_by_accurate_id(db, PO_ID)


def _packed(db: Database, qty: float) -> None:
    po_doc = _seed_po(db)
    res = scheduling_service.create_allocations(
        db,
        HEAD,
        po_doc=po_doc,
        accurate_line_id=LINE_PRINT,
        allocations=[{"operator_email": "op1@eternal.local", "qty": 1000}],
        idempotency_key=f"txn-sched-{qty}",
    )
    alloc = res["allocations"][0]["allocation_id"]
    prod.confirm_preparation(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=qty,
        idempotency_key=f"txn-prep-{qty}",
    )
    prod.confirm_screen(
        db, SCREENER, allocation_id=alloc, qty=50, idempotency_key=f"txn-screen-{qty}"
    )
    from tests.sample_flow_helpers import approve_sample

    approve_sample(db, OPERATOR, HEAD, alloc)
    prod.confirm_print(
        db, OPERATOR, allocation_id=alloc, qty=qty, idempotency_key=f"txn-print-{qty}"
    )
    prod.confirm_qc_packing(
        db,
        HELPER,
        accurate_po_id=PO_ID,
        accurate_line_id=LINE_PRINT,
        qty=qty,
        idempotency_key=f"txn-qc-{qty}",
    )


def _qc_consumed(db: Database) -> float:
    qc = tasks.get_task(db, prod.qc_task_key(PO_ID, LINE_PRINT)) or {}
    return float(qc.get("qty_consumed") or 0)


def _grant_cancel(db: Database) -> None:
    master.seed_master_defaults(db)
    task_engine.set_role_actions(
        db,
        OWNER,
        role="driver",
        task_type="pengiriman",
        actions=["view", "input", "confirm", "manage_evidence", "cancel"],
    )


def _audit_actions(db: Database, action: str) -> list[dict]:
    return list(db["audit_logs"].find({"action": action}, {"_id": 0}))


# --- A. Confirm Loading ----------------------------------------------------


def test_confirm_loading_full_success_atomic(db: Database) -> None:
    assert db.name.startswith("eternal_v4_test_")
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    result = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-load-ok"
    )
    assert result["duplicate"] is False
    assert deliveries.get_loading_event(db, SJ_ID) is not None
    assert _qc_consumed(db) == 300
    note = deliveries.get_delivery_note(db, SJ_ID)
    assert note["revision_status"] == deliveries.REVISION_ACTIVE
    driver = tasks.get_task(db, result["driver_task_key"])
    assert driver is not None
    assert driver["context"]["loading_id"] == result["loading"]["loading_id"]
    audits = _audit_actions(db, "delivery.loading.confirm")
    assert len(audits) == 1
    assert audits[0]["detail"]["loading_id"] == result["loading"]["loading_id"]


def test_confirm_rolls_back_when_driver_task_fails(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))

    def _boom(*a, **k):
        raise RuntimeError("driver task write failed")

    monkeypatch.setattr(delivery, "_ensure_driver_task", _boom)
    with pytest.raises(RuntimeError, match="driver task"):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-load-drv-fail"
        )

    assert deliveries.get_loading_event(db, SJ_ID) is None
    assert _qc_consumed(db) == 0
    assert _audit_actions(db, "delivery.loading.confirm") == []


def test_confirm_rolls_back_when_audit_fails(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))

    def _boom(*a, **k):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(delivery, "append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="audit write"):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-load-audit-fail"
        )

    assert deliveries.get_loading_event(db, SJ_ID) is None
    assert _qc_consumed(db) == 0
    assert tasks.list_tasks(db, task_type="pengiriman") == []


def test_confirm_duplicate_and_concurrent(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    first = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-dup-1"
    )
    second = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-dup-1"
    )
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert _qc_consumed(db) == 300
    assert len(tasks.list_tasks(db, task_type="pengiriman")) == 1
    assert len(_audit_actions(db, "delivery.loading.confirm")) == 1

    def worker(i: int) -> str:
        try:
            res = delivery.confirm_loading(
                db,
                DRIVER,
                accurate_sj_id=SJ_ID,
                idempotency_key=f"txn-par-{i}",
            )
            return "duplicate" if res["duplicate"] else "ok"
        except Exception:  # noqa: BLE001
            return "rejected"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))
    assert results.count("ok") == 0
    assert results.count("duplicate") >= 1
    assert db["loading_events"].count_documents(
        {"accurate_sj_id": SJ_ID, "status": "aktif"}
    ) == 1


def test_confirm_insufficient_qc_and_version_guard(db: Database) -> None:
    _packed(db, 100)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-insuff"
        )
    assert deliveries.get_loading_event(db, SJ_ID) is None
    assert _qc_consumed(db) == 0


def test_confirm_multi_line_items(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(
        db,
        OWNER,
        _sj(
            300,
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
    result = delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-multi"
    )
    assert len(result["loading"]["items"]) == 2
    assert _qc_consumed(db) == 300


# --- B. Cancel Loading -----------------------------------------------------


def test_cancel_loading_full_success_and_retry(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-c-1"
    )
    _grant_cancel(db)
    first = delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi Qty")
    second = delivery.cancel_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Revisi Qty ulang"
    )
    assert first["cancelled"] is True
    assert second["duplicate"] is True
    assert _qc_consumed(db) == 0
    assert deliveries.get_loading_event(db, SJ_ID) is None
    hist = deliveries.list_loading_events(db, SJ_ID)
    assert len(hist) == 1
    assert hist[0]["status"] == deliveries.LOADING_CANCELLED
    audits = _audit_actions(db, "delivery.loading.cancel")
    assert len(audits) == 1
    detail = audits[0]["detail"]
    assert detail["loading_id"]
    assert detail["accurate_sj_id"] == SJ_ID
    assert detail["reason"] == "Revisi Qty"
    assert detail["qty_reverted_total"] == 300
    assert detail["actor_email"] == DRIVER["email_normalized"]
    driver = tasks.list_tasks(db, task_type="pengiriman")[0]
    assert driver["context"]["loading_cancelled"] is True


def test_cancel_rolls_back_when_audit_fails(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-c-audit"
    )
    _grant_cancel(db)

    def _boom(*a, **k):
        raise RuntimeError("cancel audit failed")

    monkeypatch.setattr(delivery, "append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="cancel audit"):
        delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="crash audit")

    assert deliveries.get_loading_event(db, SJ_ID) is not None
    assert _qc_consumed(db) == 300
    assert _audit_actions(db, "delivery.loading.cancel") == []


def test_cancel_concurrent_once(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-c-par"
    )
    _grant_cancel(db)

    def worker(i: int) -> str:
        try:
            res = delivery.cancel_loading(
                db, DRIVER, accurate_sj_id=SJ_ID, reason=f"par-{i}"
            )
            return "cancelled" if res["cancelled"] else "duplicate"
        except Exception:  # noqa: BLE001
            return "rejected"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))
    assert results.count("cancelled") == 1
    assert _qc_consumed(db) == 0
    assert len(_audit_actions(db, "delivery.loading.cancel")) == 1


def test_delivered_cannot_cancel(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-deliv"
    )
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("s.png", "image/png", PNG),
    )
    _grant_cancel(db)
    with pytest.raises(delivery.LoadingCancelError):
        delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="terlambat")


# --- C. SJ Revision --------------------------------------------------------


def test_revision_atomic_snapshot_and_history(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    assert len(deliveries.list_note_revisions(db, SJ_ID)) == 1

    same = delivery.ingest_delivery_note(db, OWNER, _sj(300))
    assert same["source_changed"] is False
    assert len(deliveries.list_note_revisions(db, SJ_ID)) == 1

    changed = delivery.ingest_delivery_note(db, OWNER, _sj(250))
    assert changed["source_changed"] is True
    assert changed["sj_version"] == 2
    assert len(deliveries.list_note_revisions(db, SJ_ID)) == 2

    # Fault: insert histori gagal → snapshot tidak naik tanpa histori.
    import app.repositories.delivery_repository as deliv_mod

    real_coll_fn = deliv_mod.note_revisions_collection

    class _BoomColl:
        def __init__(self, inner):
            self._inner = inner

        def insert_one(self, doc, session=None):
            raise RuntimeError("revision history failed")

        def find(self, *a, **k):
            return self._inner.find(*a, **k)

        def find_one(self, *a, **k):
            return self._inner.find_one(*a, **k)

    monkeypatch.setattr(
        deliv_mod,
        "note_revisions_collection",
        lambda database: _BoomColl(real_coll_fn(database)),
    )
    before = deliveries.get_delivery_note(db, SJ_ID)
    with pytest.raises(RuntimeError, match="revision history"):
        deliveries.upsert_delivery_note(db, _sj(200))
    monkeypatch.undo()
    after = deliveries.get_delivery_note(db, SJ_ID)
    assert after["sj_version"] == before["sj_version"]
    assert after["items"][0]["qty"] == before["items"][0]["qty"]
    assert len(deliveries.list_note_revisions(db, SJ_ID)) == 2


def test_revision_concurrent_unique_version(db: Database) -> None:
    delivery.ingest_delivery_note(db, OWNER, _sj(300))

    def worker(qty: int) -> int:
        res = delivery.ingest_delivery_note(db, OWNER, _sj(float(qty)))
        return int(res["sj_version"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(worker, [280, 270, 260, 250]))

    revs = deliveries.list_note_revisions(db, SJ_ID)
    version_nums = [int(r["version"]) for r in revs]
    assert len(version_nums) == len(set(version_nums))
    assert max(versions) == max(version_nums)


def test_revision_opsi_b_loading_and_delivered(db: Database) -> None:
    _packed(db, 600)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-rev-b"
    )
    rev = delivery.ingest_delivery_note(db, OWNER, _sj(250))
    assert rev["revision_status"] == deliveries.REVISION_NEEDS_RELOADING
    assert deliveries.get_loading_event(db, SJ_ID) is not None  # tidak auto-cancel

    sid = "SJ-TXN-DELIV"
    delivery.ingest_delivery_note(db, OWNER, _sj(300, id=sid, number="SJ/TXN/DELIV"))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=sid, idempotency_key="txn-rev-deliv-load"
    )
    delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=sid,
        recipient_name="Penerima",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("s.png", "image/png", PNG),
    )
    result = delivery.ingest_delivery_note(
        db, OWNER, _sj(250, id=sid, number="SJ/TXN/DELIV")
    )
    assert result["revision_status"] == deliveries.REVISION_NEEDS_ADMIN_CORRECTION
    assert deliveries.get_loading_event(db, sid) is not None


# --- D. Index + Sync guards ------------------------------------------------


def test_required_indexes_present_on_test_db(db: Database) -> None:
    report = inspect_required_indexes(db)
    assert report["writes"] is False
    assert report["missing"] == []
    assert report["conflict"] == []
    for name in (
        "uniq_accurate_sj_id",
        "uniq_active_loading_per_sj",
        "uniq_loading_id",
        "uniq_note_revision_version",
        "uniq_delivery_attempt",
        "uniq_successful_delivery_per_sj",
        "uniq_task_key",
        "uniq_confirmation_idempotency",
        "idx_audit_created_at",
        "idx_audit_action_created",
        "uniq_evidence_id",
        "idx_evidence_scope_version",
    ):
        assert name in report["present"]
    success_spec = next(
        s
        for s in deliveries.REQUIRED_DELIVERY_INDEX_SPECS
        if s["name"] == "uniq_successful_delivery_per_sj"
    )
    assert success_spec["unique"] is True
    assert success_spec["keys"] == [("accurate_sj_id", 1)]
    assert success_spec["partialFilterExpression"] == {"status": "berhasil"}
    assert report["partial_successful_delivery_status"] == "berhasil"


def test_missing_required_index_rejects_without_createindex(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    clear_index_verification_cache(db=db)
    db["loading_events"].drop_index("uniq_active_loading_per_sj")

    created: list[str] = []

    def _spy_create_index(self, *a, **k):
        created.append(str(k.get("name") or a))
        raise AssertionError("createIndex tidak boleh dipanggil dari request path")

    monkeypatch.setattr(
        type(db["loading_events"]), "create_index", _spy_create_index
    )

    with pytest.raises(MissingRequiredIndexError) as exc:
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-idx-missing"
        )
    assert "uniq_active_loading_per_sj" in exc.value.missing
    assert created == []
    assert (
        db["loading_events"].find_one(
            {"accurate_sj_id": SJ_ID, "status": "aktif"}, {"_id": 0}
        )
        is None
    )
    assert "uniq_active_loading_per_sj" not in {
        ix["name"] for ix in db["loading_events"].list_indexes()
    }


def test_wrong_index_spec_rejected(db: Database) -> None:
    clear_index_verification_cache(db=db)
    db["loading_events"].drop_index("uniq_active_loading_per_sj")
    # Spek salah: unique hilang + tanpa partial filter.
    db["loading_events"].create_index(
        [("accurate_sj_id", 1)], name="uniq_active_loading_per_sj", unique=False
    )
    clear_index_verification_cache(db=db)
    report = inspect_required_indexes(db)
    assert any(c["name"] == "uniq_active_loading_per_sj" for c in report["conflict"])
    with pytest.raises(MissingRequiredIndexError) as exc:
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-idx-bad-spec"
        )
    assert any(c["name"] == "uniq_active_loading_per_sj" for c in exc.value.conflict)


def test_index_cache_isolated_by_mongo_target_not_db_name_only(db: Database) -> None:
    """Cache verifikasi memakai host:port|db|client — bukan nama DB saja."""
    from app.services import index_preflight as ip

    clear_index_verification_cache()
    key = mongo_target_cache_key(db)
    assert "|" in key
    assert db.name in key
    assert key != db.name
    mark_index_specs_verified(["uniq_active_loading_per_sj"], db=db)
    assert key in ip._VERIFIED_SPECS
    assert db.name not in ip._VERIFIED_SPECS
    clear_index_verification_cache(db=db)
    assert key not in ip._VERIFIED_SPECS


def test_prepare_rejects_missing_master_without_seed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    db["master_task_types"].delete_many({})
    seeded: list[str] = []

    def _spy_seed(database):
        seeded.append(database.name)
        raise AssertionError("seed_master_defaults tidak boleh dipanggil dari Loading")

    monkeypatch.setattr(master, "seed_master_defaults", _spy_seed)
    with pytest.raises(master.MissingMasterConfigError):
        delivery.confirm_loading(
            db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-no-master"
        )
    assert seeded == []
    assert deliveries.get_loading_event(db, SJ_ID) is None


def test_revision_concurrent_with_loading_and_delivery(db: Database) -> None:
    """Revisi SJ bersamaan dengan Loading / delivery — konsisten Opsi B."""
    _packed(db, 600)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))

    def load_worker() -> str:
        try:
            res = delivery.confirm_loading(
                db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-rev-load-race"
            )
            return "loaded" if not res["duplicate"] else "dup"
        except Exception:  # noqa: BLE001
            return "rejected"

    def rev_worker() -> str:
        try:
            res = delivery.ingest_delivery_note(db, OWNER, _sj(250))
            return str(res.get("revision_status") or "")
        except Exception:  # noqa: BLE001
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(load_worker), pool.submit(rev_worker)]
        outcomes = [f.result() for f in futures]

    note = deliveries.get_delivery_note(db, SJ_ID)
    assert note is not None
    active = deliveries.get_loading_event(db, SJ_ID)
    revs = deliveries.list_note_revisions(db, SJ_ID)
    assert len(revs) >= 1
    # Jika Loading aktif pada versi lama setelah revisi menang → wajib Opsi B.
    if active is not None and int(note.get("sj_version") or 1) > int(
        active.get("sj_version") or 1
    ):
        assert note["revision_status"] == deliveries.REVISION_NEEDS_RELOADING
    else:
        assert note["revision_status"] in {
            deliveries.REVISION_ACTIVE,
            deliveries.REVISION_NEEDS_RELOADING,
            deliveries.REVISION_NEEDS_ADMIN_CORRECTION,
        }
    assert any(o in {"loaded", "dup"} or o.startswith("PERLU") or o == "aktif" for o in outcomes)


def test_cancel_vs_delivery_success_race(db: Database) -> None:
    """Cancel bersamaan delivery success — yang sudah sukses tidak bisa dibatalkan."""
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-race-load"
    )
    _grant_cancel(db)

    def cancel_worker() -> str:
        try:
            res = delivery.cancel_loading(
                db, DRIVER, accurate_sj_id=SJ_ID, reason="race cancel"
            )
            return "cancelled" if res["cancelled"] else "duplicate"
        except delivery.LoadingCancelError:
            return "blocked"
        except Exception:  # noqa: BLE001
            return "rejected"

    def deliver_worker() -> str:
        try:
            delivery.confirm_delivery_success(
                db,
                DRIVER,
                accurate_sj_id=SJ_ID,
                recipient_name="Penerima",
                photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
                signature=("s.png", "image/png", PNG),
            )
            return "delivered"
        except Exception:  # noqa: BLE001
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(cancel_worker), pool.submit(deliver_worker)]
        outcomes = [f.result() for f in futures]

    delivered = deliveries.has_successful_delivery_event(db, SJ_ID)
    active = deliveries.get_loading_event(db, SJ_ID)
    assert "delivered" in outcomes or "cancelled" in outcomes
    if delivered and active is not None:
        with pytest.raises(delivery.LoadingCancelError):
            delivery.cancel_loading(
                db, DRIVER, accurate_sj_id=SJ_ID, reason="after success"
            )
    if delivered and "cancelled" in outcomes:
        # Cancel menang race sebelum delivery: Loading boleh hilang; delivery event
        # sukses tetap tidak boleh diikuti cancel kedua jika Loading masih aktif.
        assert active is None or "blocked" in outcomes


# --- E. Discrepancy / delivery failure — no Master bootstrap -----------------


def test_discrepancy_success_and_no_seed(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 100)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    seeded: list[str] = []

    def _spy_seed(database):
        seeded.append(database.name)
        raise AssertionError("seed_master_defaults dilarang di discrepancy")

    monkeypatch.setattr(master, "seed_master_defaults", _spy_seed)
    first = delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    second = delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    assert first["task_key"] == second["task_key"]
    assert seeded == []
    assert len(tasks.list_tasks(db, task_type="qty-loading-sj-berbeda")) == 1
    assert len(_audit_actions(db, "delivery.loading.discrepancy")) == 2


def test_discrepancy_fail_closed_without_master(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 100)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    db["master_task_types"].delete_many({"task_type": "qty-loading-sj-berbeda"})
    monkeypatch.setattr(
        master,
        "seed_master_defaults",
        lambda _db: (_ for _ in ()).throw(
            AssertionError("seed tidak boleh dari discrepancy")
        ),
    )
    with pytest.raises(master.MissingMasterConfigError):
        delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    assert tasks.list_tasks(db, task_type="qty-loading-sj-berbeda") == []
    assert _audit_actions(db, "delivery.loading.discrepancy") == []


def test_discrepancy_rolls_back_when_audit_fails(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 100)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))

    def _boom(*a, **k):
        raise RuntimeError("discrepancy audit failed")

    monkeypatch.setattr(delivery, "append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="discrepancy audit"):
        delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    assert tasks.list_tasks(db, task_type="qty-loading-sj-berbeda") == []


def test_delivery_failure_success_and_audit_rollback(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-fail-ok"
    )
    seeded: list[str] = []
    monkeypatch.setattr(
        master,
        "seed_master_defaults",
        lambda database: seeded.append(database.name)
        or (_ for _ in ()).throw(AssertionError("seed dilarang di failure")),
    )
    ok = delivery.confirm_delivery_failure(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Alamat tutup"
    )
    assert seeded == []
    assert ok["event"]["status"] == deliveries.DELIVERY_FAILED
    assert tasks.get_task(db, ok["coordination_task_key"]) is not None
    assert len(_audit_actions(db, "delivery.confirm.failure")) == 1

    # Retry: attempt baru, tugas koordinasi tetap satu (upsert idempoten).
    again = delivery.confirm_delivery_failure(
        db, DRIVER, accurate_sj_id=SJ_ID, reason="Masih tutup"
    )
    assert again["event"]["attempt"] == 2
    assert again["coordination_task_key"] == ok["coordination_task_key"]
    assert (
        len(tasks.list_tasks(db, task_type="pengiriman-gagal-koordinasi")) == 1
    )


def test_delivery_failure_rolls_back_when_audit_fails(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-fail-audit"
    )

    def _boom(*a, **k):
        raise RuntimeError("failure audit failed")

    monkeypatch.setattr(delivery, "append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="failure audit"):
        delivery.confirm_delivery_failure(
            db, DRIVER, accurate_sj_id=SJ_ID, reason="crash audit"
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []
    assert tasks.list_tasks(db, task_type="pengiriman-gagal-koordinasi") == []
    assert _audit_actions(db, "delivery.confirm.failure") == []


def test_delivery_failure_fail_closed_without_master(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-fail-nomaster"
    )
    db["master_task_types"].delete_many(
        {"task_type": "pengiriman-gagal-koordinasi"}
    )
    monkeypatch.setattr(
        master,
        "seed_master_defaults",
        lambda _db: (_ for _ in ()).throw(
            AssertionError("seed tidak boleh dari failure")
        ),
    )
    with pytest.raises(master.MissingMasterConfigError):
        delivery.confirm_delivery_failure(
            db, DRIVER, accurate_sj_id=SJ_ID, reason="tanpa master"
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []
    assert tasks.list_tasks(db, task_type="pengiriman-gagal-koordinasi") == []


def test_missing_evidence_index_rejects_before_staging(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-evid-idx"
    )
    clear_index_verification_cache(db=db)
    db["evidence_files"].drop_index("uniq_evidence_id")
    created: list[str] = []

    def _spy_create_index(self, *a, **k):
        created.append(str(k.get("name") or a))
        raise AssertionError("createIndex dilarang di request evidence")

    monkeypatch.setattr(type(db["evidence_files"]), "create_index", _spy_create_index)
    with pytest.raises(MissingRequiredIndexError) as exc:
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )
    assert "uniq_evidence_id" in exc.value.missing
    assert created == []
    assert deliveries.list_delivery_events(db, SJ_ID) == []
    # Jangan panggil list_evidence (juga require indeks); cek koleksi langsung.
    assert db["evidence_files"].count_documents({"scope_id": SJ_ID}) == 0
    assert db["delivery_events"].count_documents({"accurate_sj_id": SJ_ID}) == 0


def test_wrong_evidence_index_spec_rejected(db: Database) -> None:
    clear_index_verification_cache(db=db)
    db["evidence_files"].drop_index("uniq_evidence_id")
    db["evidence_files"].create_index(
        [("evidence_id", 1)], name="uniq_evidence_id", unique=False
    )
    clear_index_verification_cache(db=db)
    report = inspect_required_indexes(db)
    assert any(c["name"] == "uniq_evidence_id" for c in report["conflict"])
    with pytest.raises(MissingRequiredIndexError) as exc:
        evidence.store_evidence(
            db,
            scope="delivery-photo",
            scope_id="x",
            filename="a.png",
            content_type="image/png",
            content=PNG,
            uploaded_by="t@t",
            kind="delivery-photo",
        )
    assert any(c["name"] == "uniq_evidence_id" for c in exc.value.conflict)
    assert db["evidence_files"].count_documents({}) == 0


def test_orphan_gridfs_detection_read_only_after_audit_failure(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-orphan"
    )

    def _boom(*a, **k):
        raise RuntimeError("success audit failed")

    monkeypatch.setattr(delivery, "append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="success audit"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )
    assert deliveries.has_successful_delivery_event(db, SJ_ID) is False
    orphans = evidence.list_orphan_gridfs_candidates(db, limit=20)
    assert orphans
    assert all(o["writes"] is False for o in orphans)
    assert any(o["reason"] == "evidence_soft_deleted_gridfs_retained" for o in orphans)


def test_discrepancy_missing_index_no_createindex(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packed(db, 100)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    clear_index_verification_cache(db=db)
    db["operational_tasks"].drop_index("uniq_task_key")
    created: list[str] = []

    def _spy_create_index(self, *a, **k):
        created.append(str(k.get("name") or a))
        raise AssertionError("createIndex dilarang di discrepancy")

    monkeypatch.setattr(type(db["operational_tasks"]), "create_index", _spy_create_index)
    with pytest.raises(MissingRequiredIndexError) as exc:
        delivery.raise_quantity_discrepancy(db, HEAD, accurate_sj_id=SJ_ID)
    assert "uniq_task_key" in exc.value.missing
    assert created == []
    assert tasks.list_tasks(db, task_type="qty-loading-sj-berbeda") == []


# --- F. confirm_delivery_success hardening ---------------------------------


def _load_for_success(db: Database) -> None:
    _packed(db, 300)
    delivery.ingest_delivery_note(db, OWNER, _sj(300))
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-succ-load"
    )


def test_delivery_success_normal_and_idempotent_retry(db: Database) -> None:
    _load_for_success(db)
    first = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima",
        photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
        signature=("s.png", "image/png", PNG),
    )
    assert first["status"] == deliveries.DELIVERY_SUCCESS
    assert len(first["photo_evidence_ids"]) == 2
    assert first["signature_evidence_id"]
    photos_before = delivery.active_delivery_evidence(db, SJ_ID)["photos"]
    assert len(photos_before) == 2
    for p in photos_before:
        assert p["context"]["delivery_commit"] == "committed"
        assert p["context"]["delivery_event_id"] == first["delivery_event_id"]

    second = delivery.confirm_delivery_success(
        db,
        DRIVER,
        accurate_sj_id=SJ_ID,
        recipient_name="Penerima Lain",
        photos=[("c.png", "image/png", PNG), ("d.png", "image/png", PNG)],
        signature=("s2.png", "image/png", PNG),
    )
    assert second["delivery_event_id"] == first["delivery_event_id"]
    assert second["attempt"] == first["attempt"]
    assert (
        db["delivery_events"].count_documents(
            {"accurate_sj_id": SJ_ID, "status": "berhasil"}
        )
        == 1
    )
    # Bukti valid tidak diganti / tidak hilang pada retry.
    assert len(delivery.active_delivery_evidence(db, SJ_ID)["photos"]) == 2
    assert len(_audit_actions(db, "delivery.confirm.success")) == 1
    driver = tasks.list_tasks(db, task_type="pengiriman")[0]
    assert driver["status"] == tasks.TASK_STATUS_DONE


def test_delivery_success_validation_gates(db: Database) -> None:
    _load_for_success(db)
    with pytest.raises(delivery.DeliveryEvidenceError, match="minimal 2 foto"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )
    with pytest.raises(delivery.DeliveryEvidenceError, match="Nama penerima"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="  ",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []
    assert delivery.active_delivery_evidence(db, SJ_ID)["photos"] == []


def test_delivery_success_rolls_back_on_audit_failure(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load_for_success(db)

    def _boom(*a, **k):
        raise RuntimeError("success audit failed")

    monkeypatch.setattr(delivery, "append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="success audit"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []
    assert _audit_actions(db, "delivery.confirm.success") == []
    assert tasks.list_tasks(db, task_type="pengiriman")[0]["status"] != (
        tasks.TASK_STATUS_DONE
    )
    # Staging ditinggalkan sebagai soft-delete, bukan bukti aktif.
    assert delivery.active_delivery_evidence(db, SJ_ID)["photos"] == []
    assert (
        db["evidence_files"].count_documents(
            {"scope": "delivery-photo", "scope_id": SJ_ID, "deleted_at": {"$ne": None}}
        )
        >= 1
    )


def test_delivery_success_evidence_write_failure_no_partial(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load_for_success(db)
    calls = {"n": 0}
    real = evidence.store_evidence

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("evidence write failed")
        return real(*a, **k)

    monkeypatch.setattr(evidence, "store_evidence", _flaky)
    with pytest.raises(RuntimeError, match="evidence write"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []
    assert delivery.active_delivery_evidence(db, SJ_ID)["photos"] == []


def test_delivery_success_event_write_failure_abandons_staging(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load_for_success(db)

    def _boom(*a, **k):
        raise RuntimeError("delivery event write failed")

    monkeypatch.setattr(deliveries, "insert_delivery_event", _boom)
    with pytest.raises(RuntimeError, match="delivery event"):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )
    assert deliveries.list_delivery_events(db, SJ_ID) == []
    assert delivery.active_delivery_evidence(db, SJ_ID)["photos"] == []


def test_delivery_success_parallel_single_winner(db: Database) -> None:
    _load_for_success(db)

    def worker(i: int) -> str:
        try:
            event = delivery.confirm_delivery_success(
                db,
                DRIVER,
                accurate_sj_id=SJ_ID,
                recipient_name=f"Penerima-{i}",
                photos=[
                    (f"a{i}.png", "image/png", PNG),
                    (f"b{i}.png", "image/png", PNG),
                ],
                signature=(f"s{i}.png", "image/png", PNG),
            )
            return event["delivery_event_id"]
        except Exception:  # noqa: BLE001
            return "rejected"

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(worker, range(4)))
    success_ids = [i for i in ids if i != "rejected"]
    assert len(set(success_ids)) == 1
    assert (
        db["delivery_events"].count_documents(
            {"accurate_sj_id": SJ_ID, "status": "berhasil"}
        )
        == 1
    )
    assert len(_audit_actions(db, "delivery.confirm.success")) == 1


def test_delivery_success_blocked_after_cancel_or_revision(db: Database) -> None:
    _load_for_success(db)
    _grant_cancel(db)
    delivery.cancel_loading(db, DRIVER, accurate_sj_id=SJ_ID, reason="batal dulu")
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )

    # Loading ulang lalu revisi → PERLU_LOADING_ULANG memblokir delivered.
    delivery.confirm_loading(
        db, DRIVER, accurate_sj_id=SJ_ID, idempotency_key="txn-succ-reload"
    )
    delivery.ingest_delivery_note(db, OWNER, _sj(250))
    note = deliveries.get_delivery_note(db, SJ_ID)
    assert note["revision_status"] == deliveries.REVISION_NEEDS_RELOADING
    with pytest.raises(delivery.LoadingValidationError):
        delivery.confirm_delivery_success(
            db,
            DRIVER,
            accurate_sj_id=SJ_ID,
            recipient_name="Penerima",
            photos=[("a.png", "image/png", PNG), ("b.png", "image/png", PNG)],
            signature=("s.png", "image/png", PNG),
        )


def test_preflight_rejects_production_ports() -> None:
    with pytest.raises(PermissionError):
        assert_preflight_target_safe(
            uri="mongodb://u:p@127.0.0.1:27018/?authSource=admin",
            db_name="eternal_v4_test_x",
            allow_production_readonly=False,
        )
    with pytest.raises(PermissionError):
        assert_preflight_target_safe(
            uri="mongodb://u:p@127.0.0.1:27017/?authSource=admin",
            db_name="erp",
            allow_production_readonly=False,
        )
    ok = assert_preflight_target_safe(
        uri="mongodb://u:p@127.0.0.1:27019/?authSource=admin",
        db_name="eternal_v4_test_x",
        allow_production_readonly=False,
    )
    assert ok["writes"] is False


def test_sj_sync_kill_switch_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("V4_SJ_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("SJ_SYNC_ENABLED", raising=False)
    clear_settings_cache()
    settings = get_settings()
    assert settings.sj_sync_enabled is False
    clear_settings_cache()


def test_unsafe_mongo_sync_target_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db_guard import UnsafeMongoTargetError, assert_safe_mongo_sync_target

    with pytest.raises(UnsafeMongoTargetError):
        assert_safe_mongo_sync_target(
            uri="mongodb://u:p@127.0.0.1:27018/?authSource=admin",
            db_name="eternal_v4",
            app_env="development",
        )
    monkeypatch.setenv("V4_SJ_SYNC_ENABLED", "0")
    clear_settings_cache()
    from app.services import delivery_sync

    assert delivery_sync.is_sj_sync_enabled() is False
    clear_settings_cache()
