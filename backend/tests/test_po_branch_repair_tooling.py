"""Tes tooling remediasi branch_id — isolated, zero production write."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts" / "po_branch_verify_accurate_ro.py"
APPLY = ROOT / "scripts" / "po_branch_repair_apply.py"
DRY = ROOT / "scripts" / "po_branch_repair_dry_run.py"
SAFETY = ROOT / "scripts" / "lib" / "po_branch_repair_safety.py"


def _load(path: Path):
    # Ensure scripts/ on path for lib imports inside verify/apply
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    name = path.stem if path.name != "__init__.py" else path.parent.name
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
    row["accurate_trans_date"] = "31/10/2026"
    assert mod._classify(row) == "OUT_OF_SCOPE_BEFORE_FLOOR"


def test_identity_match_requires_both_sides() -> None:
    mod = _load(VERIFY)
    assert mod._identity_match(mongo_po_no="PO/1", accurate_number="PO/1") is True
    assert mod._identity_match(mongo_po_no="PO/1", accurate_number="PO/2") is False
    assert mod._identity_match(mongo_po_no=None, accurate_number="PO/1") is False
    assert mod._identity_match(mongo_po_no="PO/1", accurate_number=None) is False


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


def test_sha_gate_fail_closed_missing_repo(safety, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="live_repo_missing"):
        safety.resolve_live_sha(
            live_repo=tmp_path / "nope",
            expect_sha="a" * 40,
        )
    with pytest.raises(ValueError, match="invalid_live_sha"):
        safety.require_full_sha("abc")


def test_db_gate_rejects_wrong_port_and_legacy(safety) -> None:
    with pytest.raises(ValueError, match="mongo_port_legacy_27017"):
        safety.require_safe_db_target(
            app_env="production",
            mongodb_uri="mongodb://u:p@127.0.0.1:27017/eternal_v4",
            db_name="eternal_v4",
            require_production=True,
        )
    with pytest.raises(ValueError, match="db_name_mismatch"):
        safety.require_safe_db_target(
            app_env="production",
            mongodb_uri="mongodb://u:p@127.0.0.1:27018/other",
            db_name="other",
            require_production=True,
        )
    with pytest.raises(ValueError, match="app_env_not_production"):
        safety.require_safe_db_target(
            app_env="development",
            mongodb_uri="mongodb://u:p@127.0.0.1:27018/eternal_v4",
            db_name="eternal_v4",
            require_production=True,
        )
    ok = safety.require_safe_db_target(
        app_env="production",
        mongodb_uri="mongodb://u:p@127.0.0.1:27018/eternal_v4",
        db_name="eternal_v4",
        require_production=True,
    )
    assert ok["port"] == 27018


def test_backup_meta_freshness_and_db(safety, tmp_path: Path) -> None:
    archive = tmp_path / "dump.archive"
    archive.write_bytes(b"abc")
    sha = safety.file_sha256(archive)
    meta = {
        "db": "eternal_v4",
        "port": 27018,
        "archive_sha256": sha,
        "archive_path": str(archive),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = tmp_path / "meta.json"
    path.write_text(json.dumps(meta))
    loaded = safety.load_backup_meta(path)
    assert loaded["archive_sha256"] == sha

    meta["created_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat()
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="backup_stale"):
        safety.load_backup_meta(path)

    meta["created_at"] = datetime.now(timezone.utc).isoformat()
    meta["db"] = "wrong"
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="backup_db_mismatch"):
        safety.load_backup_meta(path)


def test_sealed_manifest_checksum_bind(safety, tmp_path: Path) -> None:
    items = [
        {
            "accurate_id": "2",
            "expected_branch_id": "150",
            "accurate_trans_date": "05/11/2026",
            "po_no": "PO/2",
        },
        {
            "accurate_id": "1",
            "expected_branch_id": "50",
            "accurate_trans_date": "06/11/2026",
            "po_no": "PO/1",
        },
    ]
    sealed = safety.sealed_manifest_from_verify_report(
        {"valid_repair_candidates": items, "generated_at": "t"}
    )
    path = tmp_path / "sealed.json"
    path.write_text(json.dumps(sealed))
    loaded = safety.load_sealed_candidates(
        path,
        expect_sha256=sealed["manifest_sha256"],
        expect_count=2,
    )
    assert [x["accurate_id"] for x in loaded] == ["1", "2"]

    sealed["valid_repair_candidates"].append(
        {"accurate_id": "3", "expected_branch_id": "50"}
    )
    path.write_text(json.dumps(sealed))
    with pytest.raises(ValueError, match="manifest_internal_checksum_mismatch"):
        safety.load_sealed_candidates(
            path,
            expect_sha256=sealed["manifest_sha256"],
            expect_count=3,
        )


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
        "category": "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR",
    }
    stale = {
        **good,
        "accurate_id": "2",
        "verified_at": (
            datetime.now(timezone.utc) - timedelta(hours=20)
        ).isoformat(),
    }
    foreign = {**good, "accurate_id": "3", "delta_sha256": "e" * 64}
    bad_id = {**good, "accurate_id": "4", "identity_match": False}
    for row in (good, stale, foreign, bad_id, {"accurate_id": "5", "ok": False}):
        mod._append_checkpoint(cp, row)
    done = mod._load_checkpoint(cp, delta_sha256=delta_sha)
    assert set(done) == {"1"}


def test_apply_refuses_without_owner_flags(tmp_path: Path) -> None:
    cand = tmp_path / "c.json"
    cand.write_text(
        json.dumps(
            {
                "sealed": True,
                "manifest_sha256": "0" * 64,
                "valid_repair_candidates": [],
            }
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


def test_apply_refuses_missing_live_repo(tmp_path: Path, safety) -> None:
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
    archive = tmp_path / "a.bin"
    archive.write_bytes(b"x")
    meta = {
        "db": "eternal_v4",
        "port": 27018,
        "archive_sha256": safety.file_sha256(archive),
        "archive_path": str(archive),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (tmp_path / "backup.json").write_text(json.dumps(meta))
    (tmp_path / "lock.json").write_text(
        json.dumps(
            {
                "token": "x" * 16,
                "workers_cleared": True,
                "po_auto_sync": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
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
            "b" * 40,
            "--expect-manifest-sha256",
            sealed["manifest_sha256"],
            "--expect-candidate-count",
            "1",
            "--backup-meta",
            str(tmp_path / "backup.json"),
            "--maintenance-lock",
            str(tmp_path / "lock.json"),
            "--live-repo",
            str(tmp_path / "no-repo"),
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
    assert "live_repo_missing" in audit["reason"]
    assert audit["wrote_mongo"] is False


def test_maintenance_lock_requires_cleared_workers(safety, tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    path.write_text(
        json.dumps(
            {
                "token": "y" * 16,
                "workers_cleared": False,
                "po_auto_sync": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    with pytest.raises(ValueError, match="workers_not_cleared"):
        safety.load_maintenance_lock(path)
