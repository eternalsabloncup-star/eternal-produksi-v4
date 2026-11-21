"""P6A — writer fence + durable audit + partial failure recovery."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pymongo.database import Database

from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.org_master_repository import (
    ensure_org_master_indexes,
    upsert_branch,
)
from app.repositories.po_repository import count_by_accurate_id, get_by_accurate_id
from app.services import historical_po_recovery_audit as audit
from app.services.accurate_auto_sync import locks as sync_locks
from app.services.accurate_auto_sync.date_floor import (
    clear_date_floor_cache,
    configured_date_floor,
)
from app.services.historical_po_recovery import (
    BATCH_ID,
    HistoricalRecoveryError,
    RecoveryApproval,
    load_sealed_manifest,
    default_manifest_path,
    recover_one_po,
    run_historical_po_recovery,
)
from app.services.po_repair_maintenance import (
    LOCKS_COLLECTION,
    MAINTENANCE_LOCK_ID,
    PoRepairMaintenanceError,
    acquire_po_repair_maintenance,
    enter_po_repair_writer_section,
    exit_po_repair_writer_section,
    is_po_repair_maintenance_active,
    release_po_repair_maintenance,
)


class FakeAccurateClient:
    def __init__(self, details: dict[str, dict[str, Any]]):
        self.details = details
        self.detail_calls = 0
        self.fail_ids: set[str] = set()

    def fetch_sales_order_detail(self, accurate_id: str | int) -> dict[str, Any]:
        self.detail_calls += 1
        key = str(accurate_id)
        if key in self.fail_ids:
            raise ConnectionError("accurate_down")
        if key not in self.details:
            raise LookupError(key)
        return copy.deepcopy(self.details[key])


@pytest.fixture()
def safety_db(db: Database, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setenv("ACCURATE_SYNC_START_DATE", "2026-11-01")
    clear_date_floor_cache()
    ensure_org_master_indexes(db)
    upsert_branch(db, code="50", name="Yogyakarta", is_active=True)
    upsert_branch(db, code="150", name="Semarang", is_active=True)
    audit.ensure_historical_recovery_audit_indexes(db)
    return db


def _manifest():
    return load_sealed_manifest(default_manifest_path())


def _detail(
    accurate_id: str,
    *,
    trans_date: str,
    branch_id: str,
) -> dict[str, Any]:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["id"] = int(accurate_id)
    payload["transDate"] = trans_date
    payload["number"] = f"PO/{trans_date}/X"
    payload["branchId"] = int(branch_id)
    return payload


def _apply_approval(manifest) -> RecoveryApproval:
    return RecoveryApproval(
        mode="apply",
        expect_manifest_sha256=manifest.manifest_sha256,
        expect_po_count=manifest.po_count,
        expect_batch_id=manifest.batch_id,
        execute=True,
        confirm_batch_id=manifest.batch_id,
    )


def _mini_client(manifest, *, limit: int = 2) -> FakeAccurateClient:
    details = {
        e.accurate_po_id: _detail(
            e.accurate_po_id,
            trans_date=e.trans_date.strftime("%d/%m/%Y"),
            branch_id=e.branch_id,
        )
        for e in manifest.entries[:limit]
    }
    return FakeAccurateClient(details)


def test_dual_apply_second_fails_on_maintenance(safety_db: Database) -> None:
    """A: dua APPLY bersamaan — kedua tidak boleh memegang fence."""
    first = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
    )
    assert is_po_repair_maintenance_active(safety_db)
    with pytest.raises((PoRepairMaintenanceError, sync_locks.AutoSyncLockError)):
        acquire_po_repair_maintenance(
            safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
        )
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(first["owner_token"]),
        job_tokens=dict(first["job_tokens"]),
    )


def test_apply_blocks_po_job_lock_and_writer_section(safety_db: Database) -> None:
    """B: APPLY + writer PO normal — job lock po + writer section ditolak."""
    handle = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
    )
    with pytest.raises(sync_locks.AutoSyncLockError):
        sync_locks.acquire_job_lock(safety_db, job="po", ttl_seconds=30)
    with pytest.raises(PoRepairMaintenanceError):
        enter_po_repair_writer_section(safety_db)
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )


def test_lock_acquire_failure_stops_before_write(safety_db: Database) -> None:
    """C: lock acquisition gagal → STOP sebelum write."""
    m = _manifest()
    blocker = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="other_maintenance"
    )
    client = _mini_client(m, limit=1)
    with pytest.raises(HistoricalRecoveryError) as ei:
        run_historical_po_recovery(
            safety_db,
            approval=_apply_approval(m),
            client=client,
            mongo_guard="test",
            app_env="test",
        )
    assert ei.value.code == "maintenance_acquire_failed"
    assert count_by_accurate_id(safety_db, m.entries[0].accurate_po_id) == 0
    assert safety_db[audit.RUNS].count_documents({}) == 0
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(blocker["owner_token"]),
        job_tokens=dict(blocker["job_tokens"]),
    )


def test_lease_loss_stops_batch(safety_db: Database) -> None:
    """D: lease kedaluwarsa → STOP, tidak lanjut write."""
    m = _manifest()
    entry = m.entries[0]
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    handle = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
    )
    run = audit.start_run(
        safety_db,
        batch_id=m.batch_id,
        manifest_sha256=m.manifest_sha256,
        live_sha=None,
        mode="apply",
        po_count=1,
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    # Expire lease milik kita (simulate timeout) tanpa release token asing.
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    safety_db[LOCKS_COLLECTION].update_one(
        {"_id": MAINTENANCE_LOCK_ID, "owner_token": handle["owner_token"]},
        {"$set": {"expires_at": past}},
    )
    out = recover_one_po(
        safety_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=payload,
        mode="apply",
        mongo_guard="test",
        run_id=str(run["run_id"]),
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        maintenance_owner_token=str(handle["owner_token"]),
        maintenance_job_tokens=dict(handle["job_tokens"]),
    )
    assert out["ok"] is False
    assert out["reason"] == "lease_lost"
    assert out["stop_batch"] is True
    assert count_by_accurate_id(safety_db, entry.accurate_po_id) == 0
    # Bersihkan lock expired milik kita.
    safety_db[LOCKS_COLLECTION].delete_one(
        {"_id": MAINTENANCE_LOCK_ID, "owner_token": handle["owner_token"]}
    )
    for job, jt in handle["job_tokens"].items():
        sync_locks.release_job_lock(safety_db, job=job, owner_token=jt)


def test_crash_before_write_leaves_no_po(safety_db: Database) -> None:
    """E: proses mati sebelum write — tidak ada PO, item processing boleh dangling."""
    m = _manifest()
    entry = m.entries[0]
    handle = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
    )
    run = audit.start_run(
        safety_db,
        batch_id=m.batch_id,
        manifest_sha256=m.manifest_sha256,
        live_sha=None,
        mode="apply",
        po_count=1,
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    audit.mark_item_processing(
        safety_db,
        batch_id=m.batch_id,
        run_id=str(run["run_id"]),
        accurate_po_id=entry.accurate_po_id,
        expected_branch_id=entry.branch_id,
        expected_trans_date=entry.trans_date.isoformat(),
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        attempt_number=1,
        po_present_before=False,
    )
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )
    item = audit.find_item(
        safety_db, run_id=str(run["run_id"]), accurate_po_id=entry.accurate_po_id
    )
    assert item is not None
    assert item["outcome"] == "processing"
    assert count_by_accurate_id(safety_db, entry.accurate_po_id) == 0


def test_crash_after_write_before_audit_requires_reconciliation(
    safety_db: Database,
) -> None:
    """F: PO written, audit completion gagal → reconciliation_required + STOP."""
    m = _manifest()
    entry = m.entries[0]
    payload = _detail(
        entry.accurate_po_id,
        trans_date=entry.trans_date.strftime("%d/%m/%Y"),
        branch_id=entry.branch_id,
    )
    handle = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
    )
    run = audit.start_run(
        safety_db,
        batch_id=m.batch_id,
        manifest_sha256=m.manifest_sha256,
        live_sha=None,
        mode="apply",
        po_count=1,
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )

    real_complete = audit.complete_item

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        # Gagal hanya pada complete sukses; biarkan mark_reconciliation_required.
        if kwargs.get("outcome") in {"created", "unchanged"}:
            raise RuntimeError("audit_mongo_down")
        return real_complete(*args, **kwargs)

    audit.complete_item = boom  # type: ignore[assignment]
    try:
        out = recover_one_po(
            safety_db,
            manifest=m,
            accurate_id=entry.accurate_po_id,
            payload=payload,
            mode="apply",
            mongo_guard="test",
            run_id=str(run["run_id"]),
            lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
            maintenance_owner_token=str(handle["owner_token"]),
            maintenance_job_tokens=dict(handle["job_tokens"]),
        )
    finally:
        audit.complete_item = real_complete  # type: ignore[assignment]
        release_po_repair_maintenance(
            safety_db,
            owner_token=str(handle["owner_token"]),
            job_tokens=dict(handle["job_tokens"]),
        )

    assert out["outcome"] == "reconciliation_required"
    assert out["stop_batch"] is True
    assert count_by_accurate_id(safety_db, entry.accurate_po_id) == 1
    item = audit.find_item(
        safety_db, run_id=str(run["run_id"]), accurate_po_id=entry.accurate_po_id
    )
    assert item is not None
    assert item["outcome"] == "reconciliation_required"


def test_rerun_after_partial_completion_idempotent(safety_db: Database) -> None:
    """I: re-run setelah partial — PO existing → already_present, no duplicate."""
    m = _manifest()
    entry = m.entries[0]
    details = {
        entry.accurate_po_id: _detail(
            entry.accurate_po_id,
            trans_date=entry.trans_date.strftime("%d/%m/%Y"),
            branch_id=entry.branch_id,
        )
    }
    # Fake client only has 1 PO; batch will fail fetch on #2 and STOP.
    client = FakeAccurateClient(details)
    # Patch manifest iteration by using recover path twice instead of full 80.
    handle = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
    )
    run1 = audit.start_run(
        safety_db,
        batch_id=m.batch_id,
        manifest_sha256=m.manifest_sha256,
        live_sha=None,
        mode="apply",
        po_count=1,
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        fence_epoch=int(handle.get("fence_epoch") or 0),
    )
    first = recover_one_po(
        safety_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=details[entry.accurate_po_id],
        mode="apply",
        mongo_guard="test",
        run_id=str(run1["run_id"]),
        lease_fp=audit.lease_fingerprint(str(handle["owner_token"])),
        maintenance_owner_token=str(handle["owner_token"]),
        maintenance_job_tokens=dict(handle["job_tokens"]),
    )
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )
    assert first["outcome"] == "created"

    handle2 = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
    )
    run2 = audit.start_run(
        safety_db,
        batch_id=m.batch_id,
        manifest_sha256=m.manifest_sha256,
        live_sha=None,
        mode="apply",
        po_count=1,
        lease_fp=audit.lease_fingerprint(str(handle2["owner_token"])),
        fence_epoch=int(handle2.get("fence_epoch") or 0),
    )
    second = recover_one_po(
        safety_db,
        manifest=m,
        accurate_id=entry.accurate_po_id,
        payload=details[entry.accurate_po_id],
        mode="apply",
        mongo_guard="test",
        run_id=str(run2["run_id"]),
        lease_fp=audit.lease_fingerprint(str(handle2["owner_token"])),
        maintenance_owner_token=str(handle2["owner_token"]),
        maintenance_job_tokens=dict(handle2["job_tokens"]),
    )
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(handle2["owner_token"]),
        job_tokens=dict(handle2["job_tokens"]),
    )
    assert second["outcome"] == "already_present"
    assert count_by_accurate_id(safety_db, entry.accurate_po_id) == 1
    assert safety_db[audit.EVENTS].count_documents({"event": "item_completed"}) >= 2


def test_network_fail_mid_batch_stops(safety_db: Database) -> None:
    """N: Accurate gagal di tengah → STOP, tanpa auto-retry seluruh batch."""
    m = _manifest()
    details = {
        e.accurate_po_id: _detail(
            e.accurate_po_id,
            trans_date=e.trans_date.strftime("%d/%m/%Y"),
            branch_id=e.branch_id,
        )
        for e in m.entries[:3]
    }
    client = FakeAccurateClient(details)
    client.fail_ids.add(m.entries[1].accurate_po_id)
    # Monkeypatch entries to only 3 for speed via temporary attribute — instead
    # call recover loop manually isn't available; use run with fail on first
    # missing. Provide only entry[0] then fail on [1] when iterating full 80.
    # Simpler: only seed entry0, fail_ids empty, LookupError on entry1 → stop.
    client = FakeAccurateClient(
        {
            m.entries[0].accurate_po_id: details[m.entries[0].accurate_po_id],
        }
    )
    report = run_historical_po_recovery(
        safety_db,
        approval=_apply_approval(m),
        client=client,
        mongo_guard="test",
        app_env="test",
        stop_on_unsafe_error=True,
    )
    assert report["stopped"] is True
    assert report["stop_reason"] == "accurate_fetch_failed"
    assert report["summary"]["created"] == 1
    assert report["processed"] == 2  # created + failed fetch
    assert get_by_accurate_id(safety_db, m.entries[0].accurate_po_id) is not None
    assert not is_po_repair_maintenance_active(safety_db)


def test_dry_run_zero_mongo_writes_including_audit(safety_db: Database) -> None:
    m = _manifest()
    details = {
        e.accurate_po_id: _detail(
            e.accurate_po_id,
            trans_date=e.trans_date.strftime("%d/%m/%Y"),
            branch_id=e.branch_id,
        )
        for e in m.entries
    }
    client = FakeAccurateClient(details)
    report = run_historical_po_recovery(
        safety_db,
        approval=RecoveryApproval(
            mode="dry_run",
            expect_manifest_sha256=m.manifest_sha256,
            expect_po_count=m.po_count,
            expect_batch_id=m.batch_id,
        ),
        client=client,
        mongo_guard="test",
        app_env="test",
    )
    assert report["mode"] == "dry_run"
    assert report["summary"]["writes"] == 0
    assert safety_db[audit.RUNS].count_documents({}) == 0
    assert safety_db[audit.ITEMS].count_documents({}) == 0
    assert safety_db[audit.EVENTS].count_documents({}) == 0
    assert not is_po_repair_maintenance_active(safety_db)


def test_release_does_not_drop_foreign_lock(safety_db: Database) -> None:
    handle = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=120, actor="historical_po_recovery_apply"
    )
    assert (
        release_po_repair_maintenance(
            safety_db, owner_token="not-the-owner", job_tokens={}
        )
        is False
    )
    assert is_po_repair_maintenance_active(safety_db)
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )


def test_date_floor_unchanged(safety_db: Database) -> None:
    assert configured_date_floor().isoformat() == "2026-11-01"
    assert _manifest().batch_id == BATCH_ID
    assert _manifest().po_count == 80


def test_active_writer_blocks_maintenance_acquire(safety_db: Database) -> None:
    """Writer PO repair section aktif → historical APPLY tidak bisa ambil fence."""
    fence = enter_po_repair_writer_section(safety_db, ttl_seconds=120)
    try:
        with pytest.raises(PoRepairMaintenanceError):
            acquire_po_repair_maintenance(
                safety_db, ttl_seconds=60, actor="historical_po_recovery_apply"
            )
    finally:
        exit_po_repair_writer_section(safety_db, fence)


# --- PR #43 safety review fixes ---


def test_audit_start_failure_releases_maintenance_lock(safety_db: Database) -> None:
    """A: start_run gagal setelah acquire → lock dilepas; acquire ulang OK."""
    m = _manifest()
    client = FakeAccurateClient({})
    real_start = audit.start_run

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("audit_start_injected")

    audit.start_run = boom  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="audit_start_injected"):
            run_historical_po_recovery(
                safety_db,
                approval=_apply_approval(m),
                client=client,
                mongo_guard="test",
                app_env="test",
            )
    finally:
        audit.start_run = real_start  # type: ignore[assignment]

    assert not is_po_repair_maintenance_active(safety_db)
    assert safety_db["accurate_sales_orders"].count_documents({}) == 0
    # Lock dapat diperoleh kembali setelah cleanup.
    handle = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=60, actor="probe_after_cleanup"
    )
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )


def test_audit_index_preflight_failure_releases_lock(
    safety_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A: index preflight gagal → release lock milik sendiri."""
    from app.services.index_preflight import MissingRequiredIndexError

    m = _manifest()
    client = FakeAccurateClient({})

    def fail_require(db):  # type: ignore[no-untyped-def]
        raise MissingRequiredIndexError(
            "indexes missing", missing=["uniq_hist_po_recovery_run_id"], conflict=[]
        )

    monkeypatch.setattr(
        audit, "require_historical_recovery_audit_indexes", fail_require
    )
    with pytest.raises(MissingRequiredIndexError):
        run_historical_po_recovery(
            safety_db,
            approval=_apply_approval(m),
            client=client,
            mongo_guard="test",
            app_env="test",
        )
    assert not is_po_repair_maintenance_active(safety_db)
    handle = acquire_po_repair_maintenance(
        safety_db, ttl_seconds=60, actor="probe_after_index_fail"
    )
    release_po_repair_maintenance(
        safety_db,
        owner_token=str(handle["owner_token"]),
        job_tokens=dict(handle["job_tokens"]),
    )


def test_cleanup_failure_does_not_mask_start_run_error(
    safety_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A: release gagal tidak menelan exception start_run."""
    m = _manifest()
    client = FakeAccurateClient({})

    def boom_start(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("start_run_primary")

    def boom_release(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("release_secondary")

    monkeypatch.setattr(audit, "start_run", boom_start)
    # release diimpor lokal di run_historical_po_recovery dari modul ini.
    monkeypatch.setattr(
        "app.services.po_repair_maintenance.release_po_repair_maintenance",
        boom_release,
    )
    with pytest.raises(RuntimeError, match="start_run_primary"):
        run_historical_po_recovery(
            safety_db,
            approval=_apply_approval(m),
            client=client,
            mongo_guard="test",
            app_env="test",
        )


def test_malformed_payload_durable_audit_and_stop(safety_db: Database) -> None:
    """B: APPLY + payload bukan Mapping → audit malformed_payload + STOP."""
    m = _manifest()
    entry0 = m.entries[0]

    class BadClient:
        def fetch_sales_order_detail(self, accurate_id: str | int) -> Any:
            if str(accurate_id) == entry0.accurate_po_id:
                return "not-a-mapping"
            raise LookupError(str(accurate_id))

    report = run_historical_po_recovery(
        safety_db,
        approval=_apply_approval(m),
        client=BadClient(),
        mongo_guard="test",
        app_env="test",
    )
    assert report["ok"] is False
    assert report["stopped"] is True
    assert report["stop_reason"] == "malformed_payload"
    assert report["summary"]["failed"] == 1
    assert report["summary"]["writes"] == 0
    assert count_by_accurate_id(safety_db, entry0.accurate_po_id) == 0
    assert not is_po_repair_maintenance_active(safety_db)

    run = safety_db[audit.RUNS].find_one({"run_id": report["run_id"]})
    assert run is not None
    assert run["status"] == "stopped"
    assert run["stop_reason"] == "malformed_payload"
    item = audit.find_item(
        safety_db, run_id=str(report["run_id"]), accurate_po_id=entry0.accurate_po_id
    )
    assert item is not None
    assert item["outcome"] == "failed"
    assert item["error_code"] == "malformed_payload"
    # Tidak ada payload mentah di audit.
    for coll in (audit.ITEMS, audit.EVENTS, audit.RUNS):
        for doc in safety_db[coll].find({}):
            blob = str(doc)
            assert "not-a-mapping" not in blob
            assert "payload" not in doc or doc.get("payload") is None


def test_malformed_payload_audit_write_failure_not_success(
    safety_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B/C: audit item gagal saat malformed → jangan klaim completion sukses."""
    m = _manifest()
    entry0 = m.entries[0]

    class BadClient:
        def fetch_sales_order_detail(self, accurate_id: str | int) -> Any:
            return ["list-not-mapping"]

    def boom_mark(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("item_audit_down")

    monkeypatch.setattr(audit, "mark_item_processing", boom_mark)
    report = run_historical_po_recovery(
        safety_db,
        approval=_apply_approval(m),
        client=BadClient(),
        mongo_guard="test",
        app_env="test",
    )
    assert report["ok"] is False
    assert report["stopped"] is True
    assert report["stop_reason"] == "audit_write_failed"
    assert report["summary"]["writes"] == 0
    run = safety_db[audit.RUNS].find_one({"run_id": report["run_id"]})
    assert run is not None
    assert run["status"] == "stopped"
    assert run["status"] != "completed"
    assert count_by_accurate_id(safety_db, entry0.accurate_po_id) == 0


def test_review_fix_no_side_effect_mutations(safety_db: Database) -> None:
    """C: malformed STOP tidak menyentuh Accepted/Payment/stok/Loading/Delivery."""
    m = _manifest()

    class BadClient:
        def fetch_sales_order_detail(self, accurate_id: str | int) -> Any:
            return 42

    before = {
        "po": safety_db["accurate_sales_orders"].count_documents({}),
        "tasks": safety_db["operational_tasks"].count_documents({}),
        "loading": safety_db["loading_events"].count_documents({}),
        "delivery": safety_db["delivery_events"].count_documents({}),
        "stock": safety_db["stock_movements"].count_documents({}),
        "receipts": safety_db["receipts"].count_documents({}),
    }
    run_historical_po_recovery(
        safety_db,
        approval=_apply_approval(m),
        client=BadClient(),
        mongo_guard="test",
        app_env="test",
    )
    assert safety_db["accurate_sales_orders"].count_documents({}) == before["po"]
    assert safety_db["operational_tasks"].count_documents({}) == before["tasks"]
    assert safety_db["loading_events"].count_documents({}) == before["loading"]
    assert safety_db["delivery_events"].count_documents({}) == before["delivery"]
    assert safety_db["stock_movements"].count_documents({}) == before["stock"]
    assert safety_db["receipts"].count_documents({}) == before["receipts"]
