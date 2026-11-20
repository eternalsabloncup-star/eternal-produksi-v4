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


def test_classify_missing_source() -> None:
    row = mod.classify_po(
        {"accurate_id": "1", "header": {"po_no": "X"}},
        master_codes={"HQ"},
        name_to_codes={},
    )
    assert row["category"] == "missing_source"


def test_classify_already_valid() -> None:
    row = mod.classify_po(
        {"accurate_id": "2", "branch_code": "HQ"},
        master_codes={"HQ"},
        name_to_codes={},
    )
    assert row["category"] == "already_valid"
    assert row["proposed_code"] == "HQ"


def test_classify_name_only_without_master_is_missing() -> None:
    row = mod.classify_po(
        {"accurate_id": "3", "branch": {"name": "Yogyakarta"}},
        master_codes=set(),
        name_to_codes={},
    )
    assert row["category"] == "missing_source"
    assert row["note"] == "name_without_master_mapping"


def test_classify_authoritative_code_shaped() -> None:
    row = mod.classify_po(
        {"accurate_id": "4", "header": {"branchCode": "SMG"}},
        master_codes={"SMG", "HQ"},
        name_to_codes={},
    )
    assert row["category"] == "authoritative"
    assert row["proposed_code"] == "SMG"


def test_classify_accurate_branch_id_needs_owner_mapping() -> None:
    row = mod.classify_po(
        {
            "accurate_id": "5",
            "header": {"raw_sync_meta": {"accurate_branch_id": "150"}},
        },
        master_codes={"HQ"},
        name_to_codes={},
    )
    assert row["category"] == "ambiguous"
    assert "master_mapping_required" in (row.get("note") or "")


def test_normalize_sales_order_does_not_invent_branch() -> None:
    from app.integrations.accurate.normalize import normalize_sales_order
    from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE

    header = normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE)
    data = header.model_dump() if hasattr(header, "model_dump") else dict(header)
    assert "branch_code" not in data or not data.get("branch_code")
    assert not data.get("branch_id")
