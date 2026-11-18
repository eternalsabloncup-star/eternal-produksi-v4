"""Label bisnis Accurate — object tidak boleh jadi JSON/str(dict) mentah."""

from __future__ import annotations

import copy

from app.api.po import _serialize_po_doc
from app.integrations.accurate.normalize import _business_label, normalize_sales_order
from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE


def test_business_label_from_object() -> None:
    assert _business_label({"name": "Net 14", "id": 3}) == "Net 14"
    assert _business_label({"name": "JNE Reguler"}) == "JNE Reguler"
    assert _business_label(None) == ""
    assert _business_label({}) == ""


def test_business_label_from_dict_repr_string() -> None:
    raw = "{'name': 'COD', 'id': 1}"
    assert _business_label(raw) == "COD"
    assert "{" not in _business_label(raw)


def test_normalize_payment_and_expedition_objects() -> None:
    payload = copy.deepcopy(SAMPLE_SALES_ORDER_STRUCTURE)
    payload["paymentTerm"] = {"id": 9, "name": "CBD 50%"}
    payload["shipment"] = {"id": 2, "name": "Ekspedisi Internal"}
    po = normalize_sales_order(payload)
    assert po.payment_terms == "CBD 50%"
    assert po.expedition == "Ekspedisi Internal"
    assert "{" not in po.payment_terms
    assert "{" not in po.expedition


def test_serialize_sanitizes_object_fields_without_db_write() -> None:
    doc = {
        "accurate_id": "95106",
        "source_po_id": "accurate:95106",
        "po_no": "PO/18/11/2026/000010",
        "origin": "accurate",
        "header": {
            "po_no": "PO/18/11/2026/000010",
            "customer": {"name": "Pelanggan Simulasi V4"},
            "payment_terms": {"name": "Net 7"},
            "expedition": {"name": "SiCepat"},
            "po_date": "18/11/2026",
            "items": [
                {
                    "line_id": "accurate-line:106318",
                    "accurate_line_id": "106318",
                    "item_code": "BRG-SIM-001",
                    "item_name": "Barang Simulasi Cetak",
                    "qty": 1000,
                    "unit": {"name": "PCS"},
                    "process_raw": "Cetak",
                    "process_kind": "Cetak",
                    "color": "Hijau Tua",
                },
                {
                    "line_id": "accurate-line:106319",
                    "accurate_line_id": "106319",
                    "item_code": "BRG-SIM-002",
                    "item_name": "Barang Simulasi Tidak Cetak",
                    "qty": 50,
                    "unit": "PACK",
                    "process_raw": "Tidak Cetak",
                    "process_kind": "Non-Cetak",
                    "color": "Putih",
                },
            ],
        },
        "operational": {},
        "line_ids": ["106318", "106319"],
    }
    out = _serialize_po_doc(doc)
    assert out["customer"] == "Pelanggan Simulasi V4"
    assert out["header"]["payment_terms"] == "Net 7"
    assert out["header"]["expedition"] == "SiCepat"
    assert out["header"]["items"][0]["unit"] == "PCS"
    assert len(out["header"]["items"]) == 2
    assert isinstance(out["header"]["payment_terms"], str)
    assert "{" not in out["header"]["payment_terms"]


def test_owner_po_95106_two_process_lines() -> None:
    po = normalize_sales_order(SAMPLE_SALES_ORDER_STRUCTURE)
    assert po.accurate_id == "95106"
    assert po.po_no == "PO/18/11/2026/000010"
    assert len(po.items) == 2
    kinds = {i.process_kind for i in po.items}
    assert kinds == {"Cetak", "Non-Cetak"}
