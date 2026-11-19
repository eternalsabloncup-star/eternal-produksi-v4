"""Identitas domain operasional — tidak menggabungkan item line yang berbeda."""

from __future__ import annotations

import pytest

from app.domain.operational import (
    DEFAULT_TASK_TYPE_BY_ID,
    OPERATIONAL_ROLES,
    TaskRef,
    build_allocation_id,
    build_task_key,
    normalize_operational_role,
)


def test_six_operational_roles_locked() -> None:
    assert set(OPERATIONAL_ROLES) == {
        "production-head",
        "designer",
        "helper",
        "screener",
        "operator",
        "driver",
    }


def test_locked_default_role_mapping() -> None:
    expected = {
        "design": "designer",
        "penjadwalan": "production-head",
        "penjadwalan-ulang": "production-head",
        "approval-sampel": "production-head",
        "qty-loading-sj-berbeda": "production-head",
        "pengiriman-gagal-koordinasi": "production-head",
        "persiapan-cup": "helper",
        "qc-packing": "helper",
        "screen": "screener",
        "konfirmasi-sample": "operator",
        "cetak": "operator",
        "pengiriman": "driver",
    }
    for task_type, role in expected.items():
        assert DEFAULT_TASK_TYPE_BY_ID[task_type].default_role == role


def test_coordination_tasks_marked() -> None:
    assert DEFAULT_TASK_TYPE_BY_ID["qty-loading-sj-berbeda"].coordination is True
    assert DEFAULT_TASK_TYPE_BY_ID["pengiriman-gagal-koordinasi"].coordination is True
    assert DEFAULT_TASK_TYPE_BY_ID["design"].coordination is False


def test_design_task_is_one_per_po_not_per_line() -> None:
    assert DEFAULT_TASK_TYPE_BY_ID["design"].scope == "po"
    ref_a = TaskRef(accurate_po_id="95106", accurate_line_id="106318")
    ref_b = TaskRef(accurate_po_id="95106", accurate_line_id="106319")
    assert build_task_key("design", "po", ref_a) == build_task_key("design", "po", ref_b)


def test_same_item_code_different_line_is_never_merged() -> None:
    ref_a = TaskRef(accurate_po_id="95106", accurate_line_id="106318", item_code="SAMA")
    ref_b = TaskRef(accurate_po_id="95106", accurate_line_id="106319", item_code="SAMA")
    assert build_task_key("persiapan-cup", "po_line", ref_a) != build_task_key(
        "persiapan-cup", "po_line", ref_b
    )


def test_screen_key_is_per_allocation() -> None:
    base = {"accurate_po_id": "95106", "accurate_line_id": "106318"}
    a = TaskRef(**base, allocation_id="alloc:106318:1")
    b = TaskRef(**base, allocation_id="alloc:106318:2")
    assert build_task_key("screen", "po_line_allocation", a) != build_task_key(
        "screen", "po_line_allocation", b
    )


def test_delivery_task_key_uses_accurate_sj_id() -> None:
    ref = TaskRef(accurate_delivery_note_id="SJ-900", sj_no="SJ/18/11/2026/0001")
    assert build_task_key("pengiriman", "delivery_note", ref) == "task:pengiriman:sj:SJ-900"


def test_missing_identity_rejected() -> None:
    with pytest.raises(ValueError, match="line"):
        build_task_key("screen", "po_line", TaskRef(accurate_po_id="95106"))
    with pytest.raises(ValueError, match="sj"):
        build_task_key("pengiriman", "delivery_note", TaskRef())


def test_allocation_id_unique_per_line_sequence() -> None:
    assert build_allocation_id("106318", 1) != build_allocation_id("106318", 2)
    assert build_allocation_id("106318", 1) != build_allocation_id("106319", 1)
    with pytest.raises(ValueError):
        build_allocation_id("106318", 0)


def test_normalize_operational_role() -> None:
    assert normalize_operational_role("Operator") == "operator"
    assert normalize_operational_role("super_admin") is None
    assert normalize_operational_role(None) is None
