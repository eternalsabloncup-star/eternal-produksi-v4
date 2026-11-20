"""Tes tooling remediasi branch_id — isolated, zero production write."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
