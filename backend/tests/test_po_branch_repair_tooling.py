"""Tes tooling remediasi branch_id — isolated, zero production write."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pymongo.database import Database

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts" / "po_branch_verify_accurate_ro.py"
APPLY = ROOT / "scripts" / "po_branch_repair_apply.py"
DRY = ROOT / "scripts" / "po_branch_repair_dry_run.py"
SAFETY = ROOT / "scripts" / "lib" / "po_branch_repair_safety.py"


def _load(path: Path):
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    name = path.stem
    if path.name == "po_branch_repair_safety.py":
        name = "lib.po_branch_repair_safety"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def safety():
    return _load(SAFETY)


@pytest.fixture()
def ingest_db(db: Database) -> Database:
    """Isolated Mongo test DB (from conftest) — clear locks between cases."""
    db["sync_locks"].delete_many({})
    db["accurate_sales_orders"].delete_many({})
    return db


def test_verify_classify_rejects_identity_mismatch() -> None:
    mod = _load(VERIFY)
    row = {
        "branch_id": None,
        "accurate_branch_id": "50",
        "detail_verified": True,
        "master_branch_active": True,
        "accurate_trans_date": "05/11/2026",
        "acceptance": None,
        "tasks": 0,
        "alloc": 0,
        "sj": 0,
        "schedule": 0,
        "identity_match": False,
    }
    assert mod._classify(row) == "REVIEW_REQUIRED_IDENTITY"
    row["identity_match"] = True
    assert mod._classify(row) == "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"


def test_dry_run_classify_requires_detail_for_ready() -> None:
    mod = _load(DRY)
    snap = {
        "tasks": 0,
        "alloc": 0,
        "sj": 0,
        "branch_id": None,
        "meta_branch": "50",
        "stored_po_date": "2026-11-05",
        "detail_verified": False,
    }
    assert mod._classify(snap) == "CANDIDATE_FROM_MONGO_SNAPSHOT"
    snap["detail_verified"] = True
    assert mod._classify(snap) == "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"


def test_backup_archive_required_and_checksum(safety, tmp_path: Path) -> None:
    meta_path = tmp_path / "meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "db": "eternal_v4",
                "port": 27018,
                "archive_sha256": "a" * 64,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    with pytest.raises(ValueError, match="backup_archive_path_missing"):
        safety.load_backup_meta(meta_path)

    archive = tmp_path / "dump.archive"
    archive.write_bytes(b"payload")
    sha = safety.file_sha256(archive)
    meta_path.write_text(
        json.dumps(
            {
                "db": "eternal_v4",
                "port": 27018,
                "archive_path": str(tmp_path / "missing.bin"),
                "archive_sha256": sha,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    with pytest.raises(ValueError, match="backup_archive_missing"):
        safety.load_backup_meta(meta_path)

    meta_path.write_text(
        json.dumps(
            {
                "db": "eternal_v4",
                "port": 27018,
                "archive_path": str(archive),
                "archive_sha256": "b" * 64,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    with pytest.raises(ValueError, match="backup_sha256_mismatch"):
        safety.load_backup_meta(meta_path)

    meta_path.write_text(
        json.dumps(
            {
                "db": "eternal_v4",
                "port": 27018,
                "archive_path": str(archive),
                "archive_sha256": sha,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    loaded = safety.load_backup_meta(meta_path)
    assert loaded["archive_sha256"] == sha


def test_snapshot_args_required(safety) -> None:
    with pytest.raises(ValueError, match="snapshot_args_required"):
        safety.require_snapshot_args(expect_total_po=None, expect_missing_po=10)
    with pytest.raises(ValueError, match="snapshot_args_required"):
        safety.require_snapshot_args(expect_total_po=10, expect_missing_po=None)
    assert safety.require_snapshot_args(expect_total_po=547, expect_missing_po=347) == (
        547,
        347,
    )


def test_snapshot_drift_and_manifest(safety, ingest_db: Database) -> None:
    ingest_db["accurate_sales_orders"].insert_many(
        [
            {"accurate_id": "1", "branch_id": "50"},
            {"accurate_id": "2"},
            {"accurate_id": "3", "branch_id": ""},
        ]
    )
    items = [
        {"accurate_id": "2", "expected_branch_id": "50", "po_no": "A"},
        {"accurate_id": "3", "expected_branch_id": "150", "po_no": "B"},
    ]
    snap = safety.assert_snapshot_and_manifest_consistency(
        ingest_db, items=items, expect_total_po=3, expect_missing_po=2
    )
    assert snap["candidates"] == 2
    with pytest.raises(ValueError, match="snapshot_total_drift"):
        safety.assert_snapshot_and_manifest_consistency(
            ingest_db, items=items, expect_total_po=9, expect_missing_po=2
        )
    with pytest.raises(ValueError, match="snapshot_missing_drift"):
        safety.assert_snapshot_and_manifest_consistency(
            ingest_db, items=items, expect_total_po=3, expect_missing_po=1
        )
    # Candidate already branched → refuse
    ingest_db["accurate_sales_orders"].update_one(
        {"accurate_id": "2"}, {"$set": {"branch_id": "50"}}
    )
    with pytest.raises(ValueError, match="manifest_candidate_already_branched"):
        safety.assert_snapshot_and_manifest_consistency(
            ingest_db,
            items=items,
            expect_total_po=3,
            expect_missing_po=1,
        )


def test_maintenance_gate_blocks_accept_and_alloc(ingest_db: Database) -> None:
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        refuse_if_po_repair_maintenance,
        release_po_repair_maintenance,
    )
    from app.services import po_acceptance

    handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=120)
    try:
        with pytest.raises(PoRepairMaintenanceError):
            refuse_if_po_repair_maintenance(ingest_db)
        with pytest.raises(PoRepairMaintenanceError):
            po_acceptance.accept_po(
                ingest_db,
                {
                    "email_normalized": "t@example.com",
                    "role": "super_admin",
                    "effective_module_permissions": ["operations.full"],
                },
                accurate_po_id="99901",
            )
    finally:
        release_po_repair_maintenance(
            ingest_db,
            owner_token=handle["owner_token"],
            job_tokens=handle.get("job_tokens"),
        )
    # After release, refuse no longer fires
    refuse_if_po_repair_maintenance(ingest_db)


def test_maintenance_blocks_concurrent_task_writer(ingest_db: Database) -> None:
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        release_po_repair_maintenance,
    )
    from app.services.po_tasks import derive_tasks_for_po

    handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=120)
    try:
        with pytest.raises(PoRepairMaintenanceError):
            derive_tasks_for_po(
                ingest_db,
                {"accurate_id": "991", "po_no": "PO/X", "header": {"items": []}},
            )
    finally:
        release_po_repair_maintenance(
            ingest_db,
            owner_token=handle["owner_token"],
            job_tokens=handle.get("job_tokens"),
        )


def test_stale_operator_lock_refused(safety, tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    path.write_text(
        json.dumps(
            {
                "token": "z" * 16,
                "workers_cleared": True,
                "po_auto_sync": False,
                "created_at": (
                    datetime.now(timezone.utc) - timedelta(hours=10)
                ).isoformat(),
            }
        )
    )
    with pytest.raises(ValueError, match="maintenance_lock_stale"):
        safety.load_maintenance_lock(path)


def test_apply_refuses_without_owner_flags(tmp_path: Path) -> None:
    cand = tmp_path / "c.json"
    cand.write_text(
        json.dumps(
            {"sealed": True, "manifest_sha256": "0" * 64, "valid_repair_candidates": []}
        )
    )
    mod = _load(APPLY)
    old = sys.argv
    try:
        sys.argv = [
            "po_branch_repair_apply.py",
            "--candidates",
            str(cand),
            "--expect-live-sha",
            "a" * 40,
            "--expect-manifest-sha256",
            "0" * 64,
            "--expect-candidate-count",
            "0",
            "--backup-meta",
            str(tmp_path / "missing.json"),
            "--maintenance-lock",
            str(tmp_path / "missing_lock.json"),
            "--expect-total-po",
            "0",
            "--expect-missing-po",
            "0",
            "--audit-out",
            str(tmp_path / "a.json"),
        ]
        rc = mod.main()
    finally:
        sys.argv = old
    assert rc == 2
    audit = json.loads((tmp_path / "a.json").read_text())
    assert audit["refused"] is True
    assert audit["wrote_mongo"] is False


def test_apply_refuses_missing_live_and_missing_backup(
    tmp_path: Path, safety
) -> None:
    items = [
        {
            "accurate_id": "1",
            "expected_branch_id": "50",
            "accurate_trans_date": "05/11/2026",
            "po_no": "PO/1",
        }
    ]
    sealed = safety.sealed_manifest_from_verify_report(
        {"valid_repair_candidates": items}
    )
    cand = tmp_path / "sealed.json"
    cand.write_text(json.dumps(sealed))
    (tmp_path / "lock.json").write_text(
        json.dumps(
            {
                "token": "y" * 16,
                "workers_cleared": True,
                "po_auto_sync": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    # Missing backup meta file
    mod = _load(APPLY)
    old = sys.argv
    try:
        sys.argv = [
            "po_branch_repair_apply.py",
            "--candidates",
            str(cand),
            "--expect-live-sha",
            "b" * 40,
            "--expect-manifest-sha256",
            sealed["manifest_sha256"],
            "--expect-candidate-count",
            "1",
            "--backup-meta",
            str(tmp_path / "no-backup.json"),
            "--maintenance-lock",
            str(tmp_path / "lock.json"),
            "--live-repo",
            str(tmp_path / "no-repo"),
            "--expect-total-po",
            "1",
            "--expect-missing-po",
            "1",
            "--audit-out",
            str(tmp_path / "audit.json"),
            "--execute",
            "--i-understand-this-writes-production",
        ]
        rc = mod.main()
    finally:
        sys.argv = old
    assert rc == 3
    audit = json.loads((tmp_path / "audit.json").read_text())
    assert audit["wrote_mongo"] is False
    assert "live_repo_missing" in audit["reason"] or "backup" in audit["reason"]


def test_checkpoint_bound_to_delta_and_freshness(tmp_path: Path) -> None:
    mod = _load(VERIFY)
    cp = tmp_path / "cp.jsonl"
    delta_sha = "d" * 64
    mod._ensure_checkpoint_meta(cp, delta_sha256=delta_sha)
    good = {
        "accurate_id": "1",
        "ok": True,
        "detail_verified": True,
        "identity_match": True,
        "delta_sha256": delta_sha,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    mod._append_checkpoint(cp, good)
    mod._append_checkpoint(
        cp, {**good, "accurate_id": "2", "identity_match": False}
    )
    done = mod._load_checkpoint(cp, delta_sha256=delta_sha)
    assert set(done) == {"1"}


def test_inflight_writer_blocks_maintenance_acquire(ingest_db: Database) -> None:
    """Writer sudah melewati gate (pegang lease) → repair tidak boleh acquire."""
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
        release_po_repair_maintenance,
    )

    fence = enter_po_repair_writer_section(ingest_db)
    try:
        with pytest.raises(PoRepairMaintenanceError, match="writers_in_flight"):
            acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
    finally:
        exit_po_repair_writer_section(ingest_db, fence)
    handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
    release_po_repair_maintenance(
        ingest_db,
        owner_token=handle["owner_token"],
        job_tokens=handle.get("job_tokens"),
    )


def test_maintenance_then_writer_enter_refused(ingest_db: Database) -> None:
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        enter_po_repair_writer_section,
        release_po_repair_maintenance,
    )

    handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
    try:
        with pytest.raises(PoRepairMaintenanceError):
            enter_po_repair_writer_section(ingest_db)
    finally:
        release_po_repair_maintenance(
            ingest_db,
            owner_token=handle["owner_token"],
            job_tokens=handle.get("job_tokens"),
        )


def test_writer_finishes_before_ttl(ingest_db: Database) -> None:
    from app.services.po_repair_maintenance import (
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
    )

    fence = enter_po_repair_writer_section(ingest_db, ttl_seconds=60)
    fence.assert_valid(ingest_db)
    fence.assert_valid(ingest_db)  # renewal
    exit_po_repair_writer_section(ingest_db, fence)


def test_stale_writer_past_ttl_cannot_commit_after_maintenance(
    ingest_db: Database,
) -> None:
    """Writer melewati TTL; maintenance acquire; stale process tidak boleh commit."""
    import threading

    from app.services.po_repair_maintenance import (
        WRITER_ID_PREFIX,
        WriterFenceLostError,
        acquire_po_repair_maintenance,
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
        release_po_repair_maintenance,
    )

    fence = enter_po_repair_writer_section(ingest_db, ttl_seconds=60)
    # Simulasi TTL habis tanpa sleep.
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    ingest_db["sync_locks"].update_one(
        {"_id": f"{WRITER_ID_PREFIX}{fence.token}"},
        {"$set": {"expires_at": past}},
    )
    # Renew gagal sebelum maintenance.
    with pytest.raises(WriterFenceLostError, match="lease_expired"):
        fence.assert_valid(ingest_db)

    # Maintenance boleh acquire (unexpired count = 0) + bump epoch.
    handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
    try:
        with pytest.raises(WriterFenceLostError):
            fence.assert_valid(ingest_db)
        committed = {"ok": False}

        def stale_resume() -> None:
            try:
                fence.assert_valid(ingest_db)
                committed["ok"] = True
            except WriterFenceLostError:
                committed["ok"] = False

        t = threading.Thread(target=stale_resume)
        t.start()
        t.join(timeout=2)
        assert committed["ok"] is False
    finally:
        release_po_repair_maintenance(
            ingest_db,
            owner_token=handle["owner_token"],
            job_tokens=handle.get("job_tokens"),
        )
        exit_po_repair_writer_section(ingest_db, fence)


def test_renewal_keeps_maintenance_blocked(ingest_db: Database) -> None:
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
    )

    fence = enter_po_repair_writer_section(ingest_db, ttl_seconds=30)
    try:
        fence.assert_valid(ingest_db)
        with pytest.raises(PoRepairMaintenanceError, match="writers_in_flight"):
            acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
    finally:
        exit_po_repair_writer_section(ingest_db, fence)


def test_concurrent_writer_and_maintenance_only_one_wins(ingest_db: Database) -> None:
    import threading

    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
        release_po_repair_maintenance,
    )

    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def writer() -> None:
        barrier.wait(timeout=5)
        try:
            fence = enter_po_repair_writer_section(ingest_db, ttl_seconds=60)
            fence.assert_valid(ingest_db)
            outcomes["writer"] = "held"
            exit_po_repair_writer_section(ingest_db, fence)
        except PoRepairMaintenanceError:
            outcomes["writer"] = "refused"

    def maint() -> None:
        barrier.wait(timeout=5)
        try:
            handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
            outcomes["maint"] = "held"
            release_po_repair_maintenance(
                ingest_db,
                owner_token=handle["owner_token"],
                job_tokens=handle.get("job_tokens"),
            )
        except PoRepairMaintenanceError:
            outcomes["maint"] = "refused"

    tw = threading.Thread(target=writer)
    tm = threading.Thread(target=maint)
    tw.start()
    tm.start()
    tw.join(timeout=5)
    tm.join(timeout=5)
    assert set(outcomes) == {"writer", "maint"}
    # Tidak boleh keduanya held tanpa salah satu refuse — setidaknya satu held.
    assert "held" in outcomes.values()
    if outcomes["writer"] == "held" and outcomes["maint"] == "held":
        raise AssertionError("both held — exclusivity broken")


def test_stale_writer_mutate_cannot_commit_after_maintenance(
    ingest_db: Database,
) -> None:
    """T2 assert PASS → T3 TTL expire → T4 maint → T7 mutate wajib gagal + DB bersih."""
    import threading

    from app.services.po_repair_maintenance import (
        WRITER_ID_PREFIX,
        WriterFenceLostError,
        acquire_po_repair_maintenance,
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
        mutate_under_writer_fence,
        release_po_repair_maintenance,
    )

    barrier = threading.Barrier(2)
    outcomes: dict[str, Any] = {}
    marker = "stale_writer_marker_991"

    fence = enter_po_repair_writer_section(ingest_db, ttl_seconds=120)
    try:
        fence.assert_valid(ingest_db)  # T2
        # T3: lease expired sementara proses writer masih hidup.
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        ingest_db["sync_locks"].update_one(
            {"_id": f"{WRITER_ID_PREFIX}{fence.token}"},
            {"$set": {"expires_at": past}},
        )

        def writer() -> None:
            barrier.wait(timeout=5)
            try:

                def _write(session: Any) -> None:
                    ingest_db["accurate_sales_orders"].insert_one(
                        {
                            "accurate_id": marker,
                            "branch_id": "50",
                            "po_no": "STALE",
                        },
                        session=session,
                    )

                mutate_under_writer_fence(ingest_db, fence, _write)
                outcomes["writer"] = "committed"
            except WriterFenceLostError:
                outcomes["writer"] = "fence_lost"
            except Exception as exc:  # noqa: BLE001
                outcomes["writer"] = type(exc).__name__

        def maint() -> None:
            barrier.wait(timeout=5)
            handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
            outcomes["maint"] = "held"
            ingest_db["accurate_sales_orders"].insert_one(
                {
                    "accurate_id": "repair_ok_1",
                    "branch_id": "150",
                    "po_no": "REPAIR",
                }
            )
            release_po_repair_maintenance(
                ingest_db,
                owner_token=handle["owner_token"],
                job_tokens=handle.get("job_tokens"),
            )

        tw = threading.Thread(target=writer)
        tm = threading.Thread(target=maint)
        tw.start()
        tm.start()
        tw.join(timeout=10)
        tm.join(timeout=10)

        assert outcomes.get("maint") == "held"
        assert outcomes.get("writer") != "committed"
        assert ingest_db["accurate_sales_orders"].find_one({"accurate_id": marker}) is None
        assert (
            ingest_db["accurate_sales_orders"].find_one({"accurate_id": "repair_ok_1"})
            is not None
        )
    finally:
        exit_po_repair_writer_section(ingest_db, fence)


def test_ttl_expired_then_maint_blocks_stale_resume(ingest_db: Database) -> None:
    """TTL habis + maint acquire → resume mutate gagal; DB tanpa write stale."""
    from app.services.po_repair_maintenance import (
        WRITER_ID_PREFIX,
        WriterFenceLostError,
        acquire_po_repair_maintenance,
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
        mutate_under_writer_fence,
        release_po_repair_maintenance,
    )

    fence = enter_po_repair_writer_section(ingest_db, ttl_seconds=60)
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    ingest_db["sync_locks"].update_one(
        {"_id": f"{WRITER_ID_PREFIX}{fence.token}"},
        {"$set": {"expires_at": past}},
    )
    handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
    try:

        def _write(session: Any) -> None:
            ingest_db["accurate_sales_orders"].insert_one(
                {"accurate_id": "ttl_stale_x", "branch_id": "50"},
                session=session,
            )

        with pytest.raises(WriterFenceLostError):
            mutate_under_writer_fence(ingest_db, fence, _write)
        assert (
            ingest_db["accurate_sales_orders"].find_one({"accurate_id": "ttl_stale_x"})
            is None
        )
    finally:
        release_po_repair_maintenance(
            ingest_db,
            owner_token=handle["owner_token"],
            job_tokens=handle.get("job_tokens"),
        )
        exit_po_repair_writer_section(ingest_db, fence)


def test_dual_maintenance_only_one_wins(ingest_db: Database) -> None:
    import threading

    from app.services.accurate_auto_sync.locks import AutoSyncLockError
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        release_po_repair_maintenance,
    )

    barrier = threading.Barrier(2)
    wins: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        barrier.wait(timeout=5)
        try:
            handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
            with lock:
                wins.append(name)
            release_po_repair_maintenance(
                ingest_db,
                owner_token=handle["owner_token"],
                job_tokens=handle.get("job_tokens"),
            )
        except (PoRepairMaintenanceError, AutoSyncLockError):
            pass

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert len(wins) == 1


def test_orphan_expired_lease_cleaned_after_epoch_bump(ingest_db: Database) -> None:
    """Crash writer: lease orphan expired → maintenance bump epoch + hapus aman."""
    from app.services.po_repair_maintenance import (
        WRITER_ID_PREFIX,
        WRITER_KIND,
        acquire_po_repair_maintenance,
        enter_po_repair_writer_section,
        release_po_repair_maintenance,
    )

    fence = enter_po_repair_writer_section(ingest_db, ttl_seconds=60)
    # Crash: jangan exit; expire lease.
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    ingest_db["sync_locks"].update_one(
        {"_id": f"{WRITER_ID_PREFIX}{fence.token}"},
        {"$set": {"expires_at": past}},
    )
    handle = acquire_po_repair_maintenance(ingest_db, ttl_seconds=60)
    try:
        assert (
            ingest_db["sync_locks"].count_documents({"kind": WRITER_KIND}) == 0
        )
        assert handle.get("fence_epoch", 0) >= 1
    finally:
        release_po_repair_maintenance(
            ingest_db,
            owner_token=handle["owner_token"],
            job_tokens=handle.get("job_tokens"),
        )


def test_apply_loop_lease_loss_and_partial_audit(tmp_path: Path, ingest_db: Database) -> None:
    loop = _load(ROOT / "scripts" / "lib" / "po_branch_repair_apply_loop.py")
    items = [
        {"accurate_id": "a1", "expected_branch_id": "50", "po_no": "P1"},
        {"accurate_id": "a2", "expected_branch_id": "50", "po_no": "P2"},
        {"accurate_id": "a3", "expected_branch_id": "150", "po_no": "P3"},
    ]
    for it in items:
        ingest_db["accurate_sales_orders"].insert_one(
            {"accurate_id": it["accurate_id"], "po_no": it["po_no"]}
        )

    filled_ids: list[str] = []

    def set_branch(db, aid, branch):
        filled_ids.append(aid)
        db["accurate_sales_orders"].update_one(
            {"accurate_id": aid}, {"$set": {"branch_id": branch}}
        )
        return {"accurate_id": aid, "branch_id": branch}

    def prewrite(db, **kwargs):
        return None

    refreshes = {"n": 0}

    def refresh(db, **kwargs):
        refreshes["n"] += 1
        return False  # lease lost on first refresh

    audit_path = tmp_path / "audit.json"
    report = loop.run_cas_apply_loop(
        ingest_db,
        items,
        audit_path=audit_path,
        base_report={"mode": "test"},
        set_branch_fn=set_branch,
        prewrite_fn=prewrite,
        refresh_fn=refresh,
        maint_handle={"owner_token": "t", "job_tokens": {}},
        refresh_every=1,
        verify_maintenance_owner=False,
    )
    assert report["ok"] is False
    assert report["stop_reason"] == "maintenance_lease_lost"
    assert report["filled"] == 1
    assert report["skipped"] >= 1
    saved = json.loads(audit_path.read_text())
    assert saved["filled"] == 1
    assert any(i.get("status") == "filled" for i in saved["items"])
    assert any(i.get("status") == "skipped_after_stop" for i in saved["items"])


def test_apply_loop_exception_keeps_filled_count(
    tmp_path: Path, ingest_db: Database
) -> None:
    loop = _load(ROOT / "scripts" / "lib" / "po_branch_repair_apply_loop.py")
    items = [
        {"accurate_id": "b1", "expected_branch_id": "50"},
        {"accurate_id": "b2", "expected_branch_id": "50"},
    ]

    def set_branch(db, aid, branch):
        if aid == "b2":
            raise RuntimeError("boom")
        return {"accurate_id": aid, "branch_id": branch}

    def prewrite(db, **kwargs):
        return None

    audit_path = tmp_path / "audit_exc.json"
    report = loop.run_cas_apply_loop(
        ingest_db,
        items,
        audit_path=audit_path,
        base_report={"mode": "test"},
        set_branch_fn=set_branch,
        prewrite_fn=prewrite,
        refresh_fn=None,
        maint_handle=None,
    )
    assert report["ok"] is False
    assert report["filled"] == 1
    assert report["stop_reason"] == "write_error"
    saved = json.loads(audit_path.read_text())
    assert saved["filled"] == 1
    assert saved["items"][0]["status"] == "filled"
    assert saved["items"][1]["status"] == "write_error"


def test_apply_loop_stops_on_audit_persist_failure(
    tmp_path: Path, ingest_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _load(ROOT / "scripts" / "lib" / "po_branch_repair_apply_loop.py")
    items = [
        {"accurate_id": "c1", "expected_branch_id": "50"},
        {"accurate_id": "c2", "expected_branch_id": "50"},
        {"accurate_id": "c3", "expected_branch_id": "50"},
    ]
    calls = {"n": 0}
    real_persist = loop.persist_audit

    def flaky(path, report):
        calls["n"] += 1
        # Gagal pada checkpoint setelah fill pertama (ada item filled).
        if any(i.get("status") == "filled" for i in report.get("items") or []):
            if report.get("stop_reason") != "audit_persist_failed":
                raise loop.AuditPersistError("disk_full")
        return real_persist(path, report)

    monkeypatch.setattr(loop, "persist_audit", flaky)

    def set_branch(db, aid, branch):
        return {"accurate_id": aid, "branch_id": branch}

    def prewrite(db, **kwargs):
        return None

    audit_path = tmp_path / "audit_disk.json"
    report = loop.run_cas_apply_loop(
        ingest_db,
        items,
        audit_path=audit_path,
        base_report={"mode": "test"},
        set_branch_fn=set_branch,
        prewrite_fn=prewrite,
        refresh_fn=None,
        maint_handle=None,
    )
    assert report["ok"] is False
    assert report["stop_reason"] == "audit_persist_failed"
    assert report["filled"] == 1
    assert report["skipped"] == 2
