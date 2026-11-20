"""Unit tests for PO branch dry-run classifier (no Mongo write)."""

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


def test_classify_missing_accurate_id() -> None:
    row = mod.classify_po(
        {"accurate_id": "1", "header": {"po_no": "X"}},
        master_by_code={"HQ": {"code": "HQ", "is_active": True}},
    )
    assert row["category"] == "MISSING_ACCURATE_BRANCH_ID"


def test_classify_already_valid() -> None:
    row = mod.classify_po(
        {"accurate_id": "2", "branch_code": "HQ"},
        master_by_code={"HQ": {"code": "HQ", "is_active": True}},
    )
    assert row["category"] == "ALREADY_VALID"
    assert row["proposed_code"] == "HQ"


def test_classify_invalid_master_existing() -> None:
    row = mod.classify_po(
        {"accurate_id": "3", "branch_code": "XYZ"},
        master_by_code={"HQ": {"code": "HQ", "is_active": True}},
    )
    assert row["category"] == "INVALID_MASTER_BRANCH"


def test_classify_unmapped_without_db() -> None:
    row = mod.classify_po(
        {
            "accurate_id": "5",
            "header": {"raw_sync_meta": {"accurate_branch_id": "150"}},
        },
        master_by_code={"HQ": {"code": "HQ", "is_active": True}},
        db=None,
    )
    assert row["category"] == "UNMAPPED_ACCURATE_BRANCH_ID"
    assert row["accurate_branch_id"] == "150"
    assert row["proposed_code"] is None


def test_classify_ready_to_map_with_resolver(db) -> None:
    from app.repositories import org_master_repository as org

    org.upsert_branch(db, code="YGY", name="Yogyakarta", is_active=True)
    org.set_branch_accurate_mapping(db, "YGY", accurate_branch_id="150")
    row = mod.classify_po(
        {
            "accurate_id": "6",
            "header": {"raw_sync_meta": {"accurate_branch_id": "150"}},
        },
        master_by_code={"YGY": {"code": "YGY", "is_active": True}},
        db=db,
    )
    assert row["category"] == "READY_TO_MAP"
    assert row["proposed_code"] == "YGY"


def test_classify_stable_on_duplicate_execution(db) -> None:
    from app.repositories import org_master_repository as org

    org.upsert_branch(db, code="YGY", name="Yogyakarta", is_active=True)
    org.set_branch_accurate_mapping(db, "YGY", accurate_branch_id="150")
    doc = {
        "accurate_id": "7",
        "header": {"raw_sync_meta": {"accurate_branch_id": "150"}},
    }
    master = {"YGY": {"code": "YGY", "is_active": True}}
    a = mod.classify_po(doc, master_by_code=master, db=db)
    b = mod.classify_po(doc, master_by_code=master, db=db)
    assert a == b
    assert a["category"] == "READY_TO_MAP"


def test_execute_flag_rejected(tmp_path, monkeypatch) -> None:
    import subprocess
    import sys

    script = ROOT / "scripts" / "po_branch_backfill_dry_run.py"
    env = {**dict(**{k: v for k, v in __import__("os").environ.items()}), "ETERNAL_V4_PO_BRANCH_BACKFILL_WRITE": "1"}
    proc = subprocess.run(
        [sys.executable, str(script), "--mongo-uri", "mongodb://127.0.0.1:27018", "--execute"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )
    assert proc.returncode == 3
    assert "DISABLED" in proc.stderr or "tidak diaktifkan" in proc.stderr


def test_normalize_sales_order_does_not_invent_branch() -> None:
    from app.integrations.accurate.normalize import normalize_sales_order
    from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE

    header = normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE)
    data = header.model_dump() if hasattr(header, "model_dump") else dict(header)
    assert "branch_code" not in data or not data.get("branch_code")
    assert not data.get("branch_id")
