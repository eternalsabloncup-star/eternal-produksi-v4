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

import ast
import json
from typing import Any

from app.domain.po import (
    PoHeaderModel,
    build_accurate_line_id,
    build_accurate_source_po_id,
    build_line_item,
)


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list, tuple)):
        # Jangan str(dict) → "{'name': ...}" di UI.
        return default
    text = str(value).strip()
    if text == "[object Object]":
        return default
    return text


_DICT_NAME_KEYS = (
    "name",
    "paymentTermName",
    "shipmentName",
    "customerName",
    "unitName",
    "no",
    "number",
    "code",
    "title",
)


def _label_from_mapping(mapping: dict[str, Any], default: str = "") -> str:
    for key in _DICT_NAME_KEYS:
        hit = mapping.get(key)
        if hit is None or isinstance(hit, (dict, list, tuple)):
            continue
        text = _as_str(hit)
        if text:
            return text
    return default


def _parse_dict_repr(text: str) -> dict[str, Any] | None:
    trimmed = text.strip()
    if not (trimmed.startswith("{") and trimmed.endswith("}")):
        return None
    for loader in (
        lambda s: ast.literal_eval(s),
        lambda s: json.loads(s.replace("'", '"')),
    ):
        try:
            parsed = loader(trimmed)
        except (ValueError, SyntaxError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _business_label(value: Any, default: str = "") -> str:
    """Object Accurate → label bisnis; kosong jika tidak ada nama."""
    if value is None:
        return default
    if isinstance(value, dict):
        return _label_from_mapping(value, default)
    if isinstance(value, (list, tuple)):
        parts = [_business_label(item) for item in value]
        joined = ", ".join(p for p in parts if p)
        return joined or default
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "[object Object]":
            return default
        parsed = _parse_dict_repr(text)
        if parsed is not None:
            return _label_from_mapping(parsed, default)
        return text
    return _as_str(value, default)


def _nested_name(obj: Any, *keys: str) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        hit = obj.get(key)
        if hit is None:
            continue
        if isinstance(hit, dict):
            name = _business_label(hit)
            if name:
                return name
            continue
        text = _as_str(hit)
        if text:
            return text
    return ""


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
    # charField3 ("K") — semantik zona BELUM TERBUKTI → jangan map ke ship_zone.
    ship_zone = ""
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
    }
    if payload.get("charField3") is not None and _as_str(payload.get("charField3")):
        meta["char_field3_raw"] = _as_str(payload.get("charField3"))
        meta["char_field3_zone_unproven"] = True

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
