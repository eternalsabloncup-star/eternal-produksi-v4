"""Normalisasi payload Sales Order Accurate → PoHeaderModel.

Field wajib Owner:
- Header identity: id → accurate_id
- Nomor PO: number
- detailItem[].id → accurate_line_id
- detailItem[].charField1 → proses (Cetak / Tidak Cetak)
- detailItem[].charField2 → warna

Tidak menebak proses dari nama barang.
"""

from __future__ import annotations

from typing import Any

from app.domain.po import (
    PoHeaderModel,
    build_accurate_line_id,
    build_accurate_source_po_id,
    build_line_item,
)
from app.integrations.accurate.fields import as_text as _as_str
from app.integrations.accurate.fields import business_label as _business_label
from app.integrations.accurate.fields import nested_label as _nested_name


def _item_code(detail: dict[str, Any]) -> str:
    item = detail.get("item")
    if isinstance(item, dict):
        for key in ("no", "itemNo", "code", "number"):
            if item.get(key) is not None and _as_str(item.get(key)):
                return _as_str(item.get(key))
    for key in ("itemNo", "itemCode", "no"):
        if detail.get(key) is not None and _as_str(detail.get(key)):
            return _as_str(detail.get(key))
    return ""


def _item_name(detail: dict[str, Any]) -> str:
    item = detail.get("item")
    if isinstance(item, dict):
        for key in ("name", "itemName"):
            if item.get(key) is not None and _as_str(item.get(key)):
                return _as_str(item.get(key))
    return _as_str(detail.get("detailName") or detail.get("itemName"))


def _item_unit(detail: dict[str, Any]) -> str:
    unit = detail.get("itemUnit") or detail.get("unit")
    if isinstance(unit, dict):
        return _as_str(unit.get("name") or unit.get("unitName"))
    return _as_str(unit)


def _item_qty(detail: dict[str, Any]) -> float | int:
    raw = detail.get("quantity", detail.get("qty", 0))
    try:
        if isinstance(raw, bool):
            return 0
        if isinstance(raw, int):
            return raw
        return float(raw)
    except (TypeError, ValueError):
        return 0


def _char_field_label(payload: dict[str, Any], index: int) -> str:
    """charFieldN string atau searchCharFieldN.name — tanpa mengubah makna label."""
    raw = payload.get(f"charField{index}")
    label = _business_label(raw) or _as_str(raw)
    if label:
        return label
    return _business_label(payload.get(f"searchCharField{index}"))


def _customer_default_salesman(payload: dict[str, Any]) -> str:
    customer = payload.get("customer")
    if not isinstance(customer, dict):
        return ""
    return _nested_name(customer, "salesman")


def _resolve_transaction_seller(
    payload: dict[str, Any], details: list[Any]
) -> tuple[str, dict[str, Any]]:
    """Penjual transaksi dari detailItem[].salesmanName (F12 terbukti).

    Kebijakan:
    - Semua baris setuju → pakai nama itu.
    - Konflik antar baris → seller kosong + meta konflik (jangan menebak).
    - Tidak ada salesmanName baris → seller kosong; JANGAN isi dari
      customer.salesman.name (itu default master pelanggan, bukan otomatis
      penjual transaksi).
    - masterSalesmanId hanya ID — tidak diubah jadi nama tanpa lookup.
    """
    meta: dict[str, Any] = {}
    customer_default = _customer_default_salesman(payload)
    if customer_default:
        meta["customer_default_salesman"] = customer_default
    master_id = payload.get("masterSalesmanId")
    if master_id is not None and _as_str(master_id):
        meta["master_salesman_id"] = _as_str(master_id)

    line_names: list[str] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        name = _as_str(detail.get("salesmanName"))
        if name:
            line_names.append(name)
    unique = list(dict.fromkeys(line_names))

    if len(unique) == 1:
        seller = unique[0]
        meta["seller_source"] = "detailItem.salesmanName"
        if customer_default and customer_default != seller:
            meta["seller_differs_from_customer_default"] = True
        return seller, meta
    if len(unique) > 1:
        meta["seller_source"] = "conflict"
        meta["seller_line_values"] = unique
        return "", meta

    meta["seller_source"] = "none"
    return "", meta


def _ship_address_pic(payload: dict[str, Any]) -> str:
    customer = payload.get("customer")
    if not isinstance(customer, dict):
        return ""
    ship = customer.get("shipAddress")
    if isinstance(ship, dict):
        return _business_label(ship.get("picName")) or _as_str(ship.get("picName"))
    return ""


def _resolve_ship_zone(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Zona Pengiriman dari charField3 / searchCharField3 (keputusan Owner).

    Prioritas sama dengan field char Accurate lain: charField3 lalu
    searchCharField3.name. Hanya trim whitespace; nilai Exact Accurate
    (termasuk ``K``) tidak diterjemahkan dan tidak dihitung dari alamat.
    """
    meta: dict[str, Any] = {}
    raw_char = payload.get("charField3")
    raw_text = _as_str(raw_char)
    if raw_text:
        meta["char_field3_raw"] = raw_text
    search = payload.get("searchCharField3")
    if isinstance(search, dict) and search:
        search_name = _business_label(search)
        if search_name:
            meta["search_char_field3_name"] = search_name

    zone = _char_field_label(payload, 3)
    if zone:
        if raw_text:
            meta["ship_zone_source"] = "charField3"
        elif meta.get("search_char_field3_name"):
            meta["ship_zone_source"] = "searchCharField3"
        else:
            meta["ship_zone_source"] = "charField3"
        meta["ship_zone_owner_confirmed"] = True
        return zone, meta

    meta["ship_zone_source"] = "none"
    return "", meta


def normalize_sales_order(payload: dict[str, Any]) -> PoHeaderModel:
    if not isinstance(payload, dict):
        raise ValueError("Payload Sales Order harus berupa objek.")

    accurate_id_raw = payload.get("id", payload.get("accurate_id"))
    if accurate_id_raw is None or _as_str(accurate_id_raw) == "":
        raise ValueError("Sales Order Accurate wajib memiliki id (accurate_id).")
    accurate_id = _as_str(accurate_id_raw)

    po_no = _as_str(payload.get("number") or payload.get("poNo") or payload.get("po_no"))
    if not po_no:
        raise ValueError("Sales Order Accurate wajib memiliki number (nomor PO).")

    details = payload.get("detailItem") or payload.get("detailItems") or []
    if not isinstance(details, list):
        raise ValueError("detailItem harus berupa list.")

    source_po_id = build_accurate_source_po_id(accurate_id)
    items = []
    seen_line_ids: set[str] = set()

    for detail in details:
        if not isinstance(detail, dict):
            continue
        line_raw = detail.get("id", detail.get("accurate_line_id"))
        if line_raw is None or _as_str(line_raw) == "":
            raise ValueError("Setiap detailItem wajib memiliki id (accurate_line_id).")
        accurate_line_id = _as_str(line_raw)
        if accurate_line_id in seen_line_ids:
            raise ValueError(f"accurate_line_id duplikat dalam payload: {accurate_line_id}")
        seen_line_ids.add(accurate_line_id)

        process_raw = _as_str(detail.get("charField1"))
        color = _as_str(detail.get("charField2"))
        code = _item_code(detail) or f"LINE-{accurate_line_id}"
        name = _item_name(detail) or code

        items.append(
            build_line_item(
                po_no=po_no,
                source_po_id=source_po_id,
                item_code=code,
                item_name=name,
                qty=_item_qty(detail),
                unit=_item_unit(detail) or "PCS",
                process_raw=process_raw,
                color=color,
                line_id=build_accurate_line_id(accurate_line_id),
                accurate_line_id=accurate_line_id,
            )
        )

    # Pelanggan: d.customer.name (F12). Jangan pakai charField1 header.
    customer = _nested_name(payload, "customer", "customerName")

    seller, seller_meta = _resolve_transaction_seller(payload, details)
    buffer_label = _char_field_label(payload, 5)
    priority_label = _char_field_label(payload, 4)
    # Owner (final): charField3 / searchCharField3 = Zona Pengiriman.
    # Nilai Exact Accurate (mis. "K") — jangan diterjemahkan / dihitung dari alamat.
    ship_zone, ship_zone_meta = _resolve_ship_zone(payload)
    # Penerima aktual tidak terbukti di F12; PIC alamat ≠ penerima.
    recipient = ""
    address_pic_name = _ship_address_pic(payload)
    address = _business_label(payload.get("toAddress") or payload.get("address"))
    # printUserName ≠ creator; createdBy hanya ID.
    print_user_name = _as_str(payload.get("printUserName"))
    created_by_id = _as_str(payload.get("createdBy"))
    user_name = ""

    meta: dict[str, Any] = {
        "accurate_id": accurate_id,
        "line_count": len(items),
        **seller_meta,
        **ship_zone_meta,
    }
    # Accurate SO terbukti live mengirim branchId numerik — simpan sebagai jejak
    # otoritatif Accurate. JANGAN mengarang branch_code Master dari ID ini.
    branch_id_raw = payload.get("branchId")
    if branch_id_raw is None and isinstance(payload.get("branch"), dict):
        branch_id_raw = payload["branch"].get("id") or payload["branch"].get("no")
    if branch_id_raw is not None and str(branch_id_raw).strip() not in {"", "None"}:
        meta["accurate_branch_id"] = str(branch_id_raw).strip()
        meta["accurate_branch_id_source"] = "branchId"

    return PoHeaderModel(
        origin="accurate",
        source_po_id=source_po_id,
        accurate_id=accurate_id,
        po_no=po_no,
        customer=customer,
        po_date=_as_str(payload.get("transDate") or payload.get("poDate")),
        payment_terms=_business_label(
            payload.get("paymentTerm") or payload.get("paymentTerms")
        ),
        eta_ship=_as_str(payload.get("shipDate") or payload.get("etaShip")),
        seller=seller,
        expedition=_business_label(
            payload.get("shipment") or payload.get("expedition")
        ),
        ship_zone=ship_zone,
        recipient=recipient,
        address=address,
        user_name=user_name,
        buffer_label=buffer_label,
        notes=_as_str(payload.get("description") or payload.get("notes")),
        urgent=bool(payload.get("urgent") or False),
        address_pic_name=address_pic_name,
        print_user_name=print_user_name,
        created_by_id=created_by_id,
        priority_label=priority_label,
        items=items,
        raw_sync_meta=meta,
    )
