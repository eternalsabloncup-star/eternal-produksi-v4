"""Idempotensi sync + perubahan/penghapusan line + error Mongo."""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest
from pymongo.database import Database
from pymongo.errors import ServerSelectionTimeoutError

from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.services.po_sync import sync_sales_order_payload


def test_sync_detects_line_change_and_removal(db: Database) -> None:
    assert db.name.startswith("eternal_v4_test_")

    first = sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)
    assert first["line_diff"]["current_line_count"] == 2
    assert first["was_insert"] is True

    changed = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    changed["detailItem"][0]["charField2"] = "Biru Simulasi"
    changed["detailItem"][0]["quantity"] = 900
    # hapus line kedua
    changed["detailItem"] = [changed["detailItem"][0]]

    second = sync_sales_order_payload(db, changed)
    assert second["was_insert"] is False
    assert second["duplicate_prevented"] is True
    assert second["count_after"] == 1
    assert second["line_diff"]["removed_line_ids"] == ["106319"]
    assert "106318" in second["line_diff"]["changed_line_ids"]
    assert second["line_count"] == 1

    # proses tetap dari charField1
    items = second["document"]["header"]["items"]
    assert items[0]["process_raw"] == "Cetak"
    assert items[0]["process_kind"] == "Cetak"
    assert items[0]["color"] == "Biru Simulasi"


def test_mongo_connection_error_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_db = MagicMock()
    bad_db.name = "eternal_v4_test_mock"
    # get_by_accurate_id uses db[collection] — simulate timeout on access path
    from app.services import po_sync

    def boom(*_a, **_k):
        raise ServerSelectionTimeoutError("no primary")

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MONGODB_DB", "eternal_v4_test_mock")
    from app.config import clear_settings_cache

    clear_settings_cache()
    monkeypatch.setattr(po_sync, "get_by_accurate_id", boom)
    monkeypatch.setattr(po_sync, "_guard_db", lambda _db, **_kw: None)
    with pytest.raises(ConnectionError, match="MongoDB tidak terjangkau"):
        sync_sales_order_payload(bad_db, SAMPLE_SALES_ORDER_STRUCTURE)
