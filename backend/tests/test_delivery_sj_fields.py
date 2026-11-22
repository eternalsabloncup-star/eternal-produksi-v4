"""Pemetaan field Surat Jalan Accurate (Delivery Order) ke model V4.

Sumber struktur: ``columnDef`` + ``memorizeFieldDef.detailField.detailItem``
halaman Accurate ``DeliveryOrder`` (module ``DO``). Payload uji disintesis dan
diberi label TEST — integrasi SJ live masih BLOCKED.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pymongo.database import Database

from app.repositories import delivery_repository as deliveries
from app.services import delivery_service as delivery

FIXTURE = Path(__file__).parent / "fixtures" / "accurate_delivery_order_TEST.json"


def _payload(**overrides) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


def test_fixture_is_labelled_test_data() -> None:
    assert "TEST" in _payload()["_label"]
    assert _payload()["number"].startswith("TEST-")


def test_header_fields_mapped_from_delivery_order_payload() -> None:
    note = deliveries.normalize_delivery_note(_payload())

    assert note["accurate_sj_id"] == "770001"
    assert note["sj_no"] == "TEST-SJ/18/11/2026/000021"
    assert note["sj_date"] == "18/11/2026"
    assert note["ship_date"] == "18/11/2026"
    assert note["customer_name"] == "PELANGGAN-TEST A"
    assert note["customer_no"] == "C-TEST-001"
    assert note["destination"].startswith("ALAMAT-TEST")
    assert note["contact_info"] == "KONTAK-TEST"
    assert note["shipment"] == "EKSPEDISI-TEST"
    assert note["description"] == "KETERANGAN-TEST"
    assert note["branch_name"] == "CABANG-TEST"
    assert note["salesman_name"] == "PENJUAL-TEST"
    assert note["ship_zone"] == "ZONA-TEST"
    assert note["printed_by_user"] == "user.cetak.test"
    assert note["printed_time"] == "18/11/2026 10:05:00"
    assert note["packing_number"] == "PACK-TEST-0001"
    assert note["created_by_user_name"] == "user.pembuat.test"
    assert note["accurate_status_name"] == "Terkirim Sebagian"
    assert note["attachment_exist"] is False


def test_printed_by_user_is_not_the_data_creator() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    assert note["printed_by_user"] != note["created_by_user_name"]


def test_customer_po_number_is_separate_from_sales_order_number() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    # DO.poNumber = nomor PO milik pelanggan; identitas PO V4 = nomor Sales Order.
    assert note["customer_po_no"] == "PO-PELANGGAN-TEST-9"
    assert note["po_no"] == "TEST-PO/18/11/2026/000010"
    assert note["accurate_po_id"] == "95106"


def test_ship_zone_from_char_field3_is_flagged_unproven() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    assert note["char_field3"] == "ZONA-TEST"
    assert note["source_meta"]["char_field3_raw"] == "ZONA-TEST"
    assert note["source_meta"]["char_field3_zone_unproven"] is True
    # Bukan klaim zona terverifikasi — hanya salinan mentah untuk tampilan.
    assert "verified_ship_zone" not in note
    assert "verified_ship_zone" not in note["source_meta"]


def test_absent_header_fields_stay_empty_and_are_recorded() -> None:
    payload = _payload()
    for key in ("charField3", "masterSalesman", "deliveryPackingNumber", "contactInfo"):
        payload.pop(key)
    note = deliveries.normalize_delivery_note(payload)

    assert note["ship_zone"] == ""
    assert note["char_field3"] == ""
    assert note["salesman_name"] == ""
    assert note["packing_number"] == ""
    assert note["contact_info"] == ""
    assert "char_field3_zone_unproven" not in note["source_meta"]
    assert set(note["source_meta"]["absent_fields"]) >= {
        "charField3",
        "masterSalesman",
        "deliveryPackingNumber",
        "contactInfo",
    }


def test_phone_stays_empty_when_source_has_none() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    assert note["contact_phone"] == ""


def test_item_fields_mapped_including_warehouse_and_notes() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    first, second, third = note["items"]

    assert first["sj_line_id"] == "880001"
    assert first["accurate_line_id"] == "106318"
    assert first["item_code"] == "BRG-TEST-001"
    assert first["item_name"] == "Cup Test 16oz Motif A"
    assert first["qty"] == 2000
    assert first["unit"] == "PCS"
    assert first["warehouse_name"] == "GUDANG-TEST-A"
    assert first["detail_notes"] == "CATATAN-ITEM-TEST-1"

    assert second["accurate_line_id"] == "106319"
    assert third["unit"] == "PACK"
    assert third["detail_notes"] == ""


def test_same_item_code_on_two_lines_stays_separate() -> None:
    note = deliveries.normalize_delivery_note(_payload())
    same_code = [i for i in note["items"] if i["item_code"] == "BRG-TEST-001"]

    assert len(same_code) == 2
    assert {i["sj_line_id"] for i in same_code} == {"880001", "880002"}
    assert {i["accurate_line_id"] for i in same_code} == {"106318", "106319"}
    assert sorted(i["qty"] for i in same_code) == [500, 2000]


def test_sales_order_relation_read_from_item_when_header_has_none() -> None:
    payload = _payload()
    payload.pop("salesOrderId", None)
    note = deliveries.normalize_delivery_note(payload)
    assert note["accurate_po_id"] == "95106"


def test_two_sales_orders_in_one_delivery_note_is_native_multi_po() -> None:
    payload = _payload()
    payload["detailItem"][2]["salesOrder"] = {"id": 95999, "number": "TEST-PO/LAIN"}
    payload["detailItem"][2]["salesOrderId"] = 95999
    note = deliveries.normalize_delivery_note(payload)
    assert note["is_multi_po"] is True
    assert "accurate_po_id" not in note or not note.get("accurate_po_id")
    assert set(note["accurate_po_ids"]) == {"95106", "95999"}
    assert note["items"][2]["accurate_po_id"] == "95999"


def test_lookup_objects_never_leak_into_labels() -> None:
    payload = _payload()
    payload["shipment"] = {"id": 3}
    payload["branch"] = "[object Object]"
    note = deliveries.normalize_delivery_note(payload)
    assert note["shipment"] == ""
    assert note["branch_name"] == ""


def test_fields_survive_resync_without_false_revision(db: Database) -> None:
    deliveries.upsert_delivery_note(db, _payload())
    again = deliveries.upsert_delivery_note(db, _payload())

    assert again["source_changed"] is False
    assert again["sj_version"] == 1
    note = again["delivery_note"]
    assert note["salesman_name"] == "PENJUAL-TEST"
    assert note["branch_name"] == "CABANG-TEST"
    assert note["items"][0]["warehouse_name"] == "GUDANG-TEST-A"


def test_header_only_change_is_recorded_without_bumping_sj_version(
    db: Database,
) -> None:
    deliveries.upsert_delivery_note(db, _payload())
    result = deliveries.upsert_delivery_note(db, _payload(description="KETERANGAN-BARU"))

    assert result["sj_version"] == 1
    assert result["source_changed"] is False
    assert result["changed_header_fields"] == [
        {"field": "description", "before": "KETERANGAN-TEST", "after": "KETERANGAN-BARU"}
    ]
    assert (
        deliveries.get_delivery_note(db, "770001")["description"] == "KETERANGAN-BARU"
    )


def test_item_revision_still_bumps_version_and_logs_header_change(
    db: Database,
) -> None:
    deliveries.upsert_delivery_note(db, _payload())
    revised = copy.deepcopy(_payload())
    revised["description"] = "KETERANGAN-REVISI"
    revised["detailItem"][0]["quantity"] = 1500
    result = deliveries.upsert_delivery_note(db, revised)

    assert result["sj_version"] == 2
    assert result["source_changed"] is True
    assert [c["field"] for c in result["changed_header_fields"]] == ["description"]
    revisions = deliveries.list_note_revisions(db, "770001")
    assert [r["version"] for r in revisions] == [1, 2]
    assert revisions[1]["changed_items"][0]["qty_after"] == 1500


def test_one_po_many_delivery_notes_stay_separate(db: Database) -> None:
    deliveries.upsert_delivery_note(db, _payload())
    second = _payload(id=770002, number="TEST-SJ/18/11/2026/000022")
    second["detailItem"] = [
        {
            "id": 880011,
            "salesOrderDetailId": 106318,
            "salesOrder": {"id": 95106, "number": "TEST-PO/18/11/2026/000010"},
            "item": {"no": "BRG-TEST-001", "name": "Cup Test 16oz"},
            "detailName": "Cup Test 16oz Motif A",
            "quantity": 100,
            "itemUnit": {"name": "PCS"},
            "warehouse": {"name": "GUDANG-TEST-A"},
        }
    ]
    deliveries.upsert_delivery_note(db, second)

    notes = deliveries.list_delivery_notes(db, accurate_po_id="95106")
    assert [n["accurate_sj_id"] for n in notes] == ["770001", "770002"]
    assert [len(n["items"]) for n in notes] == [3, 1]


def test_schedule_row_exposes_header_and_aggregates(db: Database) -> None:
    deliveries.upsert_delivery_note(db, _payload())
    schedule = delivery.delivery_schedule(db)
    row = schedule["items"][0]

    assert row["sj_no"] == "TEST-SJ/18/11/2026/000021"
    assert row["customer_name"] == "PELANGGAN-TEST A"
    assert row["salesman_name"] == "PENJUAL-TEST"
    assert row["ship_zone"] == "ZONA-TEST"
    assert row["branch_name"] == "CABANG-TEST"
    assert row["created_by_user_name"] == "user.pembuat.test"
    assert row["printed_by_user"] == "user.cetak.test"
    assert row["accurate_status_name"] == "Terkirim Sebagian"
    assert row["item_count"] == 3
    assert row["qty_by_unit"] == {"PCS": 2500.0, "PACK": 20.0}
    assert row["loaded"] is False
    assert row["needs_reloading"] is False
    assert row["delivery_status"] == deliveries.DELIVERY_PENDING
    assert row["driver_email"] == ""


def test_schedule_summary_does_not_merge_units(db: Database) -> None:
    deliveries.upsert_delivery_note(db, _payload())
    summary = delivery.delivery_schedule(db)["summary"]

    assert summary["sj_count"] == 1
    assert summary["qty_by_unit"] == {"PCS": 2500.0, "PACK": 20.0}
    assert summary["destination_count"] == 1
