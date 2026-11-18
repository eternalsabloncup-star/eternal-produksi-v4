"""Normalisasi header Accurate — key F12 terbukti; tanpa menebak alias."""

from __future__ import annotations

import copy

from app.api.po import _serialize_po_doc
from app.integrations.accurate.normalize import normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE


def test_sample_po_date_and_two_process_lines() -> None:
    po = normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE)
    assert po.accurate_id == "95106"
    assert po.po_no == "PO/18/11/2026/000010"
    assert po.po_date == "18/11/2026"
    assert len(po.items) == 2
    kinds = {i.process_kind for i in po.items}
    assert kinds == {"Cetak", "Non-Cetak"}
    assert po.po_date == SAMPLE_SALES_ORDER_STRUCTURE["transDate"]


def test_nested_objects_on_proven_f12_keys() -> None:
    """paymentTerm/shipment/toAddress/charField* — seller dari detailItem.salesmanName."""
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["paymentTerm"] = {"id": 1, "name": "COD"}
    payload["shipment"] = {"id": 2, "name": "JNE"}
    payload["toAddress"] = "Jl. Contoh No. 1"
    payload["shipDate"] = "25/11/2026"
    payload["description"] = "Catatan SO"
    payload["charField4"] = "Normal"
    payload["charField5"] = "Non - Buffer"
    payload["printUserName"] = "print.op"
    payload["createdBy"] = 99
    payload["customer"] = {
        "name": "Pelanggan Simulasi V4 (BUKAN DATA NYATA)",
        "salesman": {"name": "Default Customer"},
        "shipAddress": {"picName": "PIC Contoh"},
    }
    payload["detailItem"][0]["salesmanName"] = "Sales Transaksi"
    payload["detailItem"][1]["salesmanName"] = "Sales Transaksi"

    po = normalize_sales_order(payload)
    assert po.payment_terms == "COD"
    assert po.expedition == "JNE"
    assert po.address == "Jl. Contoh No. 1"
    assert po.eta_ship == "25/11/2026"
    assert po.notes == "Catatan SO"
    assert po.seller == "Sales Transaksi"
    assert po.buffer_label == "Non - Buffer"
    assert po.priority_label == "Normal"
    assert po.print_user_name == "print.op"
    assert po.created_by_id == "99"
    assert po.user_name == ""
    assert po.address_pic_name == "PIC Contoh"
    assert po.recipient == ""
    assert po.ship_zone == ""
    assert po.raw_sync_meta.get("seller_differs_from_customer_default") is True


def test_empty_optional_fields_stay_honest() -> None:
    po = normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE)
    assert po.eta_ship == ""
    assert po.seller == ""
    assert po.recipient == ""
    assert po.user_name == ""
    assert po.ship_zone == ""
    assert po.buffer_label == ""
    assert po.priority_label == ""
    assert po.print_user_name == ""
    assert po.created_by_id == ""
    assert po.address_pic_name == ""


def test_no_duplicate_line_ids() -> None:
    po = normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE)
    ids = [i.accurate_line_id for i in po.items]
    assert len(ids) == len(set(ids))
    assert set(ids) == {"106318", "106319"}


def test_serialize_preserves_header_dates_for_api() -> None:
    po = normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE)
    doc = {
        "accurate_id": po.accurate_id,
        "source_po_id": po.source_po_id,
        "po_no": po.po_no,
        "origin": "accurate",
        "header": po.model_dump(),
        "operational": {"status": "PO Masuk"},
        "line_ids": [i.accurate_line_id for i in po.items],
    }
    out = _serialize_po_doc(doc)
    assert out["header"]["po_date"] == "18/11/2026"
    assert out["item_count"] == 2
    assert out["operational"]["status"] == "PO Masuk"
    kinds = {i["process_kind"] for i in out["header"]["items"]}
    assert kinds == {"Cetak", "Non-Cetak"}


def test_header_salesman_alias_not_used_as_transaction_seller() -> None:
    """Header salesman / salesmanName tidak menggantikan detailItem.salesmanName."""
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    del payload["transDate"]
    payload["salesman"] = {"name": "Header Salesman"}
    payload["salesmanName"] = "Nama Header Alias"
    payload["createdUserName"] = "user.tanpa.bukti"
    po = normalize_sales_order(payload)
    assert po.po_date == ""
    assert po.seller == ""
    assert po.user_name == ""


def test_production_95106_mongo_shape_dates_notes_empty_optional() -> None:
    """Snapshot lama: dates/notes terisi; penjual kosong jika baris tanpa salesmanName."""
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["customer"] = {"name": "MUDITA"}
    payload["transDate"] = "18/11/2026"
    payload["shipDate"] = "21/11/2026"
    payload["description"] = "Catatan SO production terisi"
    payload["detailItem"][0]["quantity"] = 1000
    payload["detailItem"][1]["quantity"] = 1000
    payload["detailItem"][1]["itemUnit"] = {"name": "PCS"}
    po = normalize_sales_order(payload)
    assert po.po_date == "18/11/2026"
    assert po.eta_ship == "21/11/2026"
    assert po.notes == "Catatan SO production terisi"
    assert po.recipient == ""
    assert po.seller == ""
    assert po.user_name == ""
    assert po.ship_zone == ""
    assert po.buffer_label == ""
    assert {i.process_kind for i in po.items} == {"Cetak", "Non-Cetak"}
    assert sum(float(i.qty) for i in po.items) == 2000

    out = _serialize_po_doc(
        {
            "accurate_id": po.accurate_id,
            "source_po_id": po.source_po_id,
            "po_no": po.po_no,
            "origin": "accurate",
            "header": po.model_dump(),
            "operational": {"inbox_status": "PO Masuk"},
            "line_ids": [i.accurate_line_id for i in po.items],
        }
    )
    assert out["header"]["po_date"] == "18/11/2026"
    assert out["header"]["eta_ship"] == "21/11/2026"
    assert out["header"]["notes"] == "Catatan SO production terisi"
    assert out["header"]["seller"] == ""
    assert out["operational"]["inbox_status"] == "PO Masuk"
