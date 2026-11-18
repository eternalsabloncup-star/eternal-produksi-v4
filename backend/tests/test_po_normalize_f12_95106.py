"""Regresi mapping F12 Accurate detail.do SO 95106 — payload sanitasi dari lampiran Owner."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app.api.po import _serialize_po_doc
from app.integrations.accurate.normalize import normalize_sales_order

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "accurate_so_95106_f12_sanitized.json"
)


def _f12_payload() -> dict:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert raw.get("s") is True
    d = raw["d"]
    assert isinstance(d, dict)
    return d


def test_f12_95106_full_header_and_lines() -> None:
    po = normalize_sales_order(_f12_payload())

    assert po.accurate_id == "95106"
    assert po.po_no == "PO/18/11/2026/000010"
    assert po.po_date == "18/11/2026"
    assert po.eta_ship == "21/11/2026"
    assert po.customer == "PELANGGAN-SANITIZED"
    assert po.notes.startswith("CATATAN-SANITIZED")
    assert po.payment_terms == "TRANSFER BANK"
    assert po.expedition == "OUTLET"
    assert po.address.startswith("ALAMAT-SANITIZED")

    # Buffer/Prioritas — nilai Exact Accurate (spasi di "Non - Buffer" dipertahankan).
    assert po.buffer_label == "Non - Buffer"
    assert po.priority_label == "Normal"
    assert po.urgent is False

    # Penjual transaksi dari detailItem[].salesmanName (konsensus).
    assert po.seller == "Sales Transaksi Sanitized"
    assert po.raw_sync_meta.get("seller_source") == "detailItem.salesmanName"
    assert po.raw_sync_meta.get("customer_default_salesman") == "Sales Transaksi Sanitized"
    assert po.raw_sync_meta.get("seller_differs_from_customer_default") is not True
    assert po.raw_sync_meta.get("master_salesman_id") == "150"

    # printUserName ≠ creator; createdBy hanya ID.
    assert po.print_user_name == "print.user.sanitized"
    assert po.created_by_id == "1162627"
    assert po.user_name == ""

    # PIC alamat ≠ penerima aktual.
    assert po.address_pic_name == "PIC-SANITIZED"
    assert po.recipient == ""

    # Zona: charField3="K" belum terbukti → ship_zone kosong + meta.
    assert po.ship_zone == ""
    assert po.raw_sync_meta.get("char_field3_raw") == "K"
    assert po.raw_sync_meta.get("char_field3_zone_unproven") is True

    assert len(po.items) == 2
    assert [i.accurate_line_id for i in po.items] == ["106318", "106319"]
    assert [i.process_kind for i in po.items] == ["Cetak", "Non-Cetak"]
    assert [i.process_raw for i in po.items] == ["Cetak", "Tidak Cetak"]
    assert [i.color for i in po.items] == ["PUTIH", "BENING"]
    assert [i.item_code for i in po.items] == ["SKU-CETAK-SAN", "SKU-NON-SAN"]
    assert sum(float(i.qty) for i in po.items) == 2000
    assert all(i.unit == "PCS" for i in po.items)


def test_f12_95106_api_serializer_and_no_demo() -> None:
    po = normalize_sales_order(_f12_payload())
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
    h = out["header"]
    assert out["origin"] == "accurate"
    assert h["po_date"] == "18/11/2026"
    assert h["eta_ship"] == "21/11/2026"
    assert h["buffer_label"] == "Non - Buffer"
    assert h["priority_label"] == "Normal"
    assert h["seller"] == "Sales Transaksi Sanitized"
    assert h["print_user_name"] == "print.user.sanitized"
    assert h["created_by_id"] == "1162627"
    assert h["user_name"] == ""
    assert h["address_pic_name"] == "PIC-SANITIZED"
    assert h["recipient"] == ""
    assert h["ship_zone"] == ""
    assert out["item_count"] == 2
    assert {i["process_kind"] for i in h["items"]} == {"Cetak", "Non-Cetak"}
    blob = str(out)
    assert "PELANGGAN-SANITIZED" in blob
    assert "print.user.sanitized" in blob
    assert "Sales Transaksi Sanitized" in blob


def test_buffer_fallback_search_char_field5() -> None:
    payload = _f12_payload()
    payload["charField5"] = ""
    po = normalize_sales_order(payload)
    assert po.buffer_label == "Non - Buffer"


def test_seller_conflict_leaves_empty_and_reports() -> None:
    payload = _f12_payload()
    payload["detailItem"][0]["salesmanName"] = "Sales A"
    payload["detailItem"][1]["salesmanName"] = "Sales B"
    po = normalize_sales_order(payload)
    assert po.seller == ""
    assert po.raw_sync_meta.get("seller_source") == "conflict"
    assert po.raw_sync_meta.get("seller_line_values") == ["Sales A", "Sales B"]


def test_seller_not_filled_from_customer_default_alone() -> None:
    payload = _f12_payload()
    for line in payload["detailItem"]:
        line["salesmanName"] = ""
    payload["customer"]["salesman"]["name"] = "Hanya Default Customer"
    po = normalize_sales_order(payload)
    assert po.seller == ""
    assert po.raw_sync_meta.get("seller_source") == "none"
    assert po.raw_sync_meta.get("customer_default_salesman") == "Hanya Default Customer"


def test_seller_differs_from_customer_default_flag() -> None:
    payload = _f12_payload()
    payload["customer"]["salesman"]["name"] = "Default Lain"
    po = normalize_sales_order(payload)
    assert po.seller == "Sales Transaksi Sanitized"
    assert po.raw_sync_meta.get("seller_differs_from_customer_default") is True


def test_print_user_never_becomes_creator_name() -> None:
    payload = _f12_payload()
    payload["createdByName"] = "jangan.dipakai"
    po = normalize_sales_order(payload)
    assert po.user_name == ""
    assert po.print_user_name == "print.user.sanitized"
    assert po.created_by_id == "1162627"


def test_char_field3_not_mapped_as_zone() -> None:
    payload = copy.deepcopy(_f12_payload())
    payload["shipZone"] = {"name": "Jawa"}  # key tidak terbukti F12 sebagai SoT zona
    po = normalize_sales_order(payload)
    assert po.ship_zone == ""
    assert po.raw_sync_meta.get("char_field3_raw") == "K"


def test_f12_sync_idempotent_preserves_lines_and_operational(db) -> None:
    """Sync F12 sanitasi: 1 PO, 2 line, operational tidak hilang."""
    from app.repositories.po_repository import count_by_accurate_id, get_by_accurate_id
    from app.services.po_sync import sync_sales_order_payload

    assert db.name.startswith("eternal_v4_test_")
    assert db.name != "eternal_v4"

    payload = _f12_payload()
    first = sync_sales_order_payload(db, payload)
    db["accurate_sales_orders"].update_one(
        {"accurate_id": "95106"},
        {"$set": {"operational": {"inbox_status": "PO Masuk"}}},
    )
    second = sync_sales_order_payload(db, payload)

    assert first["was_insert"] is True
    assert second["was_insert"] is False
    assert second["operational_preserved"] is True
    assert count_by_accurate_id(db, "95106") == 1

    stored = get_by_accurate_id(db, "95106")
    assert stored is not None
    assert stored["operational"] == {"inbox_status": "PO Masuk"}
    assert stored["line_ids"] == ["106318", "106319"]
    assert len(stored["header"]["items"]) == 2
    assert stored["header"]["buffer_label"] == "Non - Buffer"
    assert stored["header"]["seller"] == "Sales Transaksi Sanitized"
    assert stored["header"]["print_user_name"] == "print.user.sanitized"
    assert stored["header"]["user_name"] == ""
    assert {i["process_kind"] for i in stored["header"]["items"]} == {
        "Cetak",
        "Non-Cetak",
    }
    assert sum(float(i["qty"]) for i in stored["header"]["items"]) == 2000
