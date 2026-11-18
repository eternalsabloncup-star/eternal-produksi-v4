"""Focused Tahap 01 tests — domain PO / process mapping."""

from __future__ import annotations

import pytest

from app.domain.po import (
    PoHeaderModel,
    build_demo_line_id,
    build_demo_source_po_id,
    build_line_item,
)
from app.domain.process_mapping import map_process_from_source
from app.integrations.accurate.client import AccurateReadClient, AccurateReadStatus


def test_process_mapping_allowlist_cetak_tidak_cetak() -> None:
    assert map_process_from_source("Cetak")["process_kind"] == "Cetak"
    assert map_process_from_source("Tidak Cetak")["process_kind"] == "Non-Cetak"
    assert map_process_from_source("Polos")["ok"] is False
    assert map_process_from_source("Cup PET 16oz + sablon")["ok"] is False


def test_po_with_multiple_line_identities() -> None:
    po_no = "PO-1101"
    source = build_demo_source_po_id(po_no)
    a = build_line_item(
        po_no=po_no,
        source_po_id=source,
        item_code="BRG-2000",
        item_name="Cup A",
        qty=1000,
        unit="PCS",
        process_raw="Cetak",
    )
    b = build_line_item(
        po_no=po_no,
        source_po_id=source,
        item_code="BRG-2001",
        item_name="Bonus",
        qty=50,
        unit="PACK",
        process_raw="Tidak Cetak",
    )
    assert a.line_id != b.line_id
    assert a.line_id == build_demo_line_id(po_no, "BRG-2000")
    assert a.process_kind == "Cetak"
    assert b.process_kind == "Non-Cetak"


def test_demo_and_accurate_origins_distinct() -> None:
    demo = PoHeaderModel(origin="demo", source_po_id="demo:PO-1", po_no="PO-1")
    accurate = PoHeaderModel(
        origin="accurate",
        source_po_id="accurate:95106",
        accurate_id="95106",
        po_no="PO/18/11/2026/000010",
    )
    assert demo.origin != accurate.origin


def test_accurate_client_status_requires_account_token_secret() -> None:
    partial = AccurateReadClient(
        base_url="https://x",
        api_token="t",
        signature_secret="",
        db_id="",
    )
    assert partial.status == AccurateReadStatus.CONFIG_PRESENT
    ready = AccurateReadClient(
        account_base_url="https://account.example",
        api_token="t",
        signature_secret="s",
    )
    assert ready.status == AccurateReadStatus.READY
    empty = AccurateReadClient("", "", "", "")
    assert empty.status == AccurateReadStatus.NOT_CONFIGURED
