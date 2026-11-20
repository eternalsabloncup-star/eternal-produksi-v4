"""Tes tooling remediasi branch_id — tanpa production write."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts" / "po_branch_verify_accurate_ro.py"
APPLY = ROOT / "scripts" / "po_branch_repair_apply.py"
DRY = ROOT / "scripts" / "po_branch_repair_dry_run.py"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_verify_classify_valid_and_floor() -> None:
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
    }
    assert mod._classify(row) == "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"
    row["accurate_trans_date"] = "31/10/2026"
    assert mod._classify(row) == "OUT_OF_SCOPE_BEFORE_FLOOR"
    row["accurate_trans_date"] = "05/11/2026"
    row["accurate_branch_id"] = "999"
    assert mod._classify(row) == "UNKNOWN_OR_INVALID_BRANCH"
    row["accurate_branch_id"] = "50"
    row["tasks"] = 1
    assert mod._classify(row) == "OPERATIONAL_DEPENDENCY_PRESENT"


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


def test_apply_refuses_without_owner_flags(tmp_path: Path) -> None:
    cand = tmp_path / "c.json"
    cand.write_text(json.dumps({"valid_repair_candidates": []}))
    # Invoke module main via subprocess pattern — call main() with argv
    mod = _load(APPLY)
    old = sys.argv
    try:
        sys.argv = [
            "po_branch_repair_apply.py",
            "--candidates",
            str(cand),
            "--expect-live-sha",
            "deadbeef",
            "--audit-out",
            str(tmp_path / "a.json"),
        ]
        rc = mod.main()
    finally:
        sys.argv = old
    assert rc == 2


def test_checkpoint_resume_dedup(tmp_path: Path) -> None:
    mod = _load(VERIFY)
    cp = tmp_path / "cp.jsonl"
    row = {"accurate_id": "1", "ok": True, "category": "VALID_IN_SCOPE_READY_FOR_BRANCH_REPAIR"}
    mod._append_checkpoint(cp, row)
    mod._append_checkpoint(cp, {"accurate_id": "2", "ok": False})
    done = mod._load_checkpoint(cp)
    assert "1" in done
    assert "2" not in done  # failed not treated as verified resume
