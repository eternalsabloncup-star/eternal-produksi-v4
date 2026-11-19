"""Normalisasi Accurate + sync idempotent — DB test terisolasi (bukan eternal_v4 prod)."""

from __future__ import annotations

import copy

import pytest
from pymongo.database import Database

from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.repositories.po_repository import count_by_accurate_id, get_by_accurate_id
from app.services.po_sync import sync_sales_order_payload


def test_normalize_header_and_lines_owner_example() -> None:
    po = normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE)
    assert po.origin == "accurate"
    assert po.accurate_id == "95106"
    assert po.po_no == "PO/18/11/2026/000010"
    assert po.source_po_id == "accurate:95106"
    assert len(po.items) == 2

    a, b = po.items
    assert a.accurate_line_id == "106318"
    assert b.accurate_line_id == "106319"
    assert a.line_id == "accurate-line:106318"
    assert b.line_id == "accurate-line:106319"
    assert a.item_code != a.line_id
    assert a.process_raw == "Cetak"
    assert a.process_kind == "Cetak"
    assert a.color == "Hijau Tua"
    assert b.process_raw == "Tidak Cetak"
    assert b.process_kind == "Non-Cetak"
    assert b.color == "Putih"


def test_process_from_charfield1_not_item_name() -> None:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["detailItem"][0]["item"]["name"] = "Cup Polos Tanpa Cetak Fake"
    payload["detailItem"][0]["charField1"] = "Cetak"
    po = normalize_sales_order(payload)
    assert po.items[0].process_kind == "Cetak"


def test_rejects_missing_accurate_ids() -> None:
    bad = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    del bad["id"]
    with pytest.raises(ValueError, match="accurate_id"):
        normalize_sales_order(bad)

    bad2 = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    del bad2["detailItem"][0]["id"]
    with pytest.raises(ValueError, match="accurate_line_id"):
        normalize_sales_order(bad2)


def test_sync_idempotent_no_duplicate(db: Database) -> None:
    assert db.name.startswith("eternal_v4_test_")
    assert db.name != "eternal_v4"

    first = sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)
    # Field operasional V4 — sync tidak boleh menimpa.
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "95106"},
        {"$set": {"operational": {"inbox_status": "PO Masuk", "note": "keep-ops"}}},
    )
    second = sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)
    third_payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    third_payload["detailItem"][0]["charField2"] = "Merah Simulasi"
    third = sync_sales_order_payload(db, third_payload)

    assert first["was_insert"] is True
    assert second["was_insert"] is False
    assert third["was_insert"] is False
    assert first["duplicate_prevented"] is True
    assert second["duplicate_prevented"] is True
    assert third["duplicate_prevented"] is True
    assert second["operational_preserved"] is True
    assert third["operational_preserved"] is True
    assert count_by_accurate_id(db, "95106") == 1

    stored = get_by_accurate_id(db, "95106")
    assert stored is not None
    assert stored["po_no"] == "PO/18/11/2026/000010"
    assert stored["line_ids"] == ["106318", "106319"]
    assert stored["operational"] == {"inbox_status": "PO Masuk", "note": "keep-ops"}
    header_items = stored["header"]["items"]
    assert header_items[0]["color"] == "Merah Simulasi"
    assert len(header_items) == 2
    kinds = {i["process_kind"] for i in header_items}
    assert kinds == {"Cetak", "Non-Cetak"}


def test_mongo_url_alias_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DB", raising=False)
    monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017/test")
    monkeypatch.setenv("DB_NAME", "eternal_v4_alias_check")
    from app.config import Settings, clear_settings_cache

    clear_settings_cache()
    settings = Settings()  # type: ignore[call-arg]
    assert settings.mongodb_uri.startswith("mongodb://")
    assert settings.mongodb_db == "eternal_v4_alias_check"
    summary = settings.safe_summary()
    assert "accurate_api_token" not in summary
    assert "mongodb_uri" not in summary
