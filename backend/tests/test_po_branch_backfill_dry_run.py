"""Dry-run Accurate branchId classifier (no Mongo write)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "po_branch_backfill_dry_run",
    ROOT / "scripts" / "po_branch_backfill_dry_run.py",
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_missing() -> None:
    row = mod.classify_po({"accurate_id": "1"}, master_ids={"50"})
    assert row["category"] == "MISSING_BRANCH_ID"


def test_ready_to_backfill() -> None:
    row = mod.classify_po(
        {
            "accurate_id": "2",
            "header": {"raw_sync_meta": {"accurate_branch_id": "150"}},
        },
        master_ids={"50", "150"},
    )
    assert row["category"] == "READY_TO_BACKFILL"
    assert row["accurate_branch_id"] == "150"


def test_has_valid() -> None:
    row = mod.classify_po(
        {"accurate_id": "3", "branch_id": "50"},
        master_ids={"50", "150"},
    )
    assert row["category"] == "HAS_VALID_BRANCH_ID"


def test_conflict_top_vs_meta() -> None:
    row = mod.classify_po(
        {
            "accurate_id": "4",
            "branch_id": "50",
            "header": {"raw_sync_meta": {"accurate_branch_id": "150"}},
        },
        master_ids={"50", "150"},
    )
    assert row["category"] == "CONFLICT"


def test_execute_rejected() -> None:
    import os
    import subprocess
    import sys

    script = ROOT / "scripts" / "po_branch_backfill_dry_run.py"
    env = {**os.environ, "ETERNAL_V4_PO_BRANCH_BACKFILL_WRITE": "1"}
    proc = subprocess.run(
        [sys.executable, str(script), "--mongo-uri", "mongodb://127.0.0.1:27018", "--execute"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert proc.returncode == 3
