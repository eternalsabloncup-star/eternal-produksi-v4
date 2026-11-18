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


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _nested_name(obj: Any, *keys: str) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        hit = obj.get(key)
        if hit is None:
            continue
        if isinstance(hit, dict):
            name = hit.get("name") or hit.get("customerName") or hit.get("no")
            if name:
                return _as_str(name)
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

    customer = (
        _nested_name(payload, "customer", "customerName", "charField1")
        or _as_str(payload.get("charField1"))
    )

    return PoHeaderModel(
        origin="accurate",
        source_po_id=source_po_id,
        accurate_id=accurate_id,
        po_no=po_no,
        customer=customer,
        po_date=_as_str(payload.get("transDate") or payload.get("poDate")),
        payment_terms=_as_str(payload.get("paymentTerm") or payload.get("paymentTerms")),
        eta_ship=_as_str(payload.get("shipDate") or payload.get("etaShip")),
        seller=_nested_name(payload, "salesman", "seller"),
        expedition=_as_str(payload.get("shipment") or payload.get("expedition")),
        ship_zone=_as_str(payload.get("shipZone")),
        recipient=_as_str(payload.get("toAddressName") or payload.get("recipient")),
        address=_as_str(payload.get("toAddress") or payload.get("address")),
        user_name=_as_str(payload.get("createdByName") or payload.get("userName")),
        buffer_label=_as_str(payload.get("bufferLabel")),
        notes=_as_str(payload.get("description") or payload.get("notes")),
        urgent=bool(payload.get("urgent") or False),
        items=items,
        raw_sync_meta={
            "accurate_id": accurate_id,
            "line_count": len(items),
        },
    )
