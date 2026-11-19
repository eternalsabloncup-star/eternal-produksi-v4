"""Normalizer SJ terhadap kontrak empiris Accurate live (fixture tersanitasi)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.repositories import delivery_repository as deliveries

FIXTURE = Path(__file__).parent / "fixtures" / "accurate_delivery_order_live_shape_TEST.json"


def _payload(**overrides) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


def test_live_shape_single_so_two_items() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    assert note["accurate_sj_id"] == "880101"
    assert note["sj_no"] == "TEST-SJ/19/11/2026/000099"
    assert note["accurate_po_id"] == "95106"
    assert note["po_no"] == "PO/18/11/2026/000010"
    assert note["customer_po_no"] == "CUST-PO-TEST-1"
    assert len(note["items"]) == 2
    assert note["items"][0]["qty"] == 12.5
    assert note["items"][0]["unit"] == "PCS"
    assert note["items"][0]["item_code"] == "BRG-TEST-001"
    assert note["items"][0]["warehouse_name"] == "GUDANG-TEST-A"
    assert note["items"][0]["detail_notes"] == "CATATAN-LIVE-TEST"
    assert note["items"][1]["unit"] == "PACK"


def test_header_po_number_and_contact_info_id_null_ok() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    assert "poNumber" in note["source_meta"]["absent_fields"]
    assert "contactInfoId" in note["source_meta"]["absent_fields"]
    assert note["contact_info"] == ""
    # customer_po_no diambil dari item, bukan dikarang dari NULL header.
    assert note["customer_po_no"] == "CUST-PO-TEST-1"


def test_header_sales_order_boolean_ignored() -> None:
    payload = _payload()
    assert payload["salesOrder"] is False
    note = deliveries.normalize_delivery_note(payload)
    assert note["accurate_po_id"] == "95106"


def test_sales_order_id_matches_sales_order_object() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    assert note["accurate_po_id"] == "95106"


def test_sales_order_id_conflict_rejected() -> None:
    payload = _payload()
    payload["detailItem"][0]["salesOrderId"] = 99999
    with pytest.raises(deliveries.DeliveryNoteIdentityError) as exc:
        deliveries.normalize_delivery_note(payload)
    assert exc.value.code == "SO_ID_CONFLICT"


def test_multi_so_rejected_with_specific_code() -> None:
    payload = _payload()
    payload["detailItem"][1]["salesOrder"] = {
        "id": 95999,
        "number": "PO/LAIN/TEST",
        "poNumber": "X",
    }
    payload["detailItem"][1]["salesOrderId"] = 95999
    with pytest.raises(deliveries.DeliveryNoteIdentityError) as exc:
        deliveries.normalize_delivery_note(payload)
    assert exc.value.code == "MULTI_SO_NOT_SUPPORTED"
    # Diagnostik aman: tanpa nama pelanggan.
    assert "PELANGGAN" not in str(exc.value)


def test_missing_item_identity_rejected() -> None:
    payload = _payload()
    payload["detailItem"][0].pop("id")
    with pytest.raises(deliveries.DeliveryNoteIdentityError):
        deliveries.normalize_delivery_note(payload)


def test_missing_sales_order_detail_id_rejected() -> None:
    payload = _payload()
    payload["detailItem"][0].pop("salesOrderDetailId")
    with pytest.raises(deliveries.DeliveryNoteIdentityError):
        deliveries.normalize_delivery_note(payload)


def test_char_field3_not_auto_verified_zone() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    assert note["char_field3"] == "K"
    assert note["source_meta"]["char_field3_zone_unproven"] is True
    assert note["source_meta"]["char_field3_raw"] == "K"
    assert "verified_ship_zone" not in note["source_meta"]


def test_master_salesman_id_stored_without_inventing_name() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    assert note["master_salesman_id"] == "150"
    assert note["salesman_name"] == ""
    assert note["created_by_id"] == "1162627"


def test_partial_sj_still_requires_so_relation() -> None:
    payload = _payload()
    payload["detailItem"] = [payload["detailItem"][0]]
    del payload["detailItem"][0]["salesOrder"]
    del payload["detailItem"][0]["salesOrderId"]
    with pytest.raises(deliveries.DeliveryNoteIdentityError):
        deliveries.normalize_delivery_note(payload)


def test_one_so_many_sj_normalize_independently() -> None:
    first = deliveries.normalize_delivery_note(_payload())
    second_payload = copy.deepcopy(_payload())
    second_payload["id"] = 880102
    second_payload["number"] = "TEST-SJ/19/11/2026/000100"
    second_payload["detailItem"] = [second_payload["detailItem"][0]]
    second_payload["detailItem"][0]["id"] = 990099
    second = deliveries.normalize_delivery_note(second_payload)
    assert first["accurate_po_id"] == second["accurate_po_id"] == "95106"
    assert first["accurate_sj_id"] != second["accurate_sj_id"]
