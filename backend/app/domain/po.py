"""Model domain PO / item line — kontrak Tahap 01."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.process_mapping import ProcessKind, map_process_from_source

PoOrigin = Literal["demo", "accurate"]


class PoLineItemModel(BaseModel):
    line_id: str = Field(min_length=1)
    accurate_line_id: str | None = None
    source_po_id: str = Field(min_length=1)
    po_no: str = Field(min_length=1)
    item_code: str = Field(min_length=1)
    item_name: str
    qty: float | int
    unit: str
    process_raw: str
    process_kind: ProcessKind | None
    color: str = ""


class PoHeaderModel(BaseModel):
    origin: PoOrigin
    source_po_id: str = Field(min_length=1)
    accurate_id: str | None = None
    po_no: str = Field(min_length=1)
    customer: str = ""
    po_date: str = ""
    payment_terms: str = ""
    eta_ship: str = ""
    seller: str = ""
    expedition: str = ""
    ship_zone: str = ""
    recipient: str = ""
    address: str = ""
    user_name: str = ""
    buffer_label: str = ""
    notes: str = ""
    urgent: bool = False
    items: list[PoLineItemModel] = Field(default_factory=list)
    raw_sync_meta: dict[str, Any] = Field(default_factory=dict)


def build_demo_source_po_id(po_no: str) -> str:
    return f"demo:{po_no}"


def build_accurate_source_po_id(accurate_id: str | int) -> str:
    return f"accurate:{accurate_id}"


def build_demo_line_id(po_no: str, item_code: str) -> str:
    return f"demo:{po_no}:line:{item_code}"


def build_accurate_line_id(accurate_line_id: str | int) -> str:
    return f"accurate-line:{accurate_line_id}"


def build_line_item(
    *,
    po_no: str,
    source_po_id: str,
    item_code: str,
    item_name: str,
    qty: float | int,
    unit: str,
    process_raw: str,
    color: str = "",
    line_id: str | None = None,
    accurate_line_id: str | None = None,
) -> PoLineItemModel:
    mapped = map_process_from_source(process_raw)
    return PoLineItemModel(
        line_id=line_id
        or (
            build_accurate_line_id(accurate_line_id)
            if accurate_line_id
            else build_demo_line_id(po_no, item_code)
        ),
        accurate_line_id=str(accurate_line_id) if accurate_line_id is not None else None,
        source_po_id=source_po_id,
        po_no=po_no,
        item_code=item_code,
        item_name=item_name,
        qty=qty,
        unit=unit,
        process_raw=str(mapped["process_raw"]),
        process_kind=mapped["process_kind"],  # type: ignore[arg-type]
        color=color,
    )
