"""Normalizer Faktur & Penerimaan — field dari kontrak live Accurate (read-only).

Relasi terbukti:
- Invoice line ``detailItem[].salesOrderId`` → Accurate SO / PO id.
- Receipt ``detailInvoice[].invoiceId`` + ``paymentAmount`` → alokasi faktur.
Header ``salesOrder`` pada invoice sering boolean — jangan dipakai sebagai id.
"""

from __future__ import annotations

from typing import Any

from app.integrations.accurate.fields import as_text


def _as_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def normalize_sales_invoice(payload: dict[str, Any]) -> dict[str, Any]:
    accurate_id = as_text(payload.get("id"))
    if not accurate_id:
        raise ValueError("Sales invoice Accurate tanpa id.")

    po_ids: list[str] = []
    lines: list[dict[str, Any]] = []
    for raw in payload.get("detailItem") or []:
        if not isinstance(raw, dict):
            continue
        so_id = as_text(raw.get("salesOrderId"))
        if so_id and so_id not in po_ids:
            po_ids.append(so_id)
        line_id = as_text(raw.get("id"))
        lines.append(
            {
                "accurate_line_id": line_id,
                "sales_order_id": so_id,
                "sales_order_detail_id": as_text(raw.get("salesOrderDetailId")),
                "item_id": as_text(raw.get("itemId")),
                "qty": _as_float(raw.get("quantity")),
            }
        )

    review_reason = None
    if not po_ids:
        review_reason = "Invoice tanpa salesOrderId pada detailItem — relasi PO tidak terverifikasi."
    elif len(po_ids) > 1:
        review_reason = (
            "Invoice terkait lebih dari satu Sales Order. "
            "Aturan multi-SO per faktur belum diputuskan Owner."
        )

    return {
        "accurate_invoice_id": accurate_id,
        "invoice_no": as_text(payload.get("number")),
        "trans_date": as_text(payload.get("transDate")),
        "status": as_text(payload.get("status") or payload.get("statusName")),
        "total_amount": _as_float(payload.get("totalAmount")),
        "sub_total": _as_float(payload.get("subTotal")),
        "accurate_po_ids": po_ids,
        "primary_accurate_po_id": po_ids[0] if len(po_ids) == 1 else "",
        "lines": lines,
        "review_required": bool(review_reason),
        "review_reason": review_reason,
    }


def normalize_sales_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    accurate_id = as_text(payload.get("id"))
    if not accurate_id:
        raise ValueError("Sales receipt Accurate tanpa id.")

    allocations: list[dict[str, Any]] = []
    for raw in payload.get("detailInvoice") or []:
        if not isinstance(raw, dict):
            continue
        invoice_id = as_text(raw.get("invoiceId"))
        invoice_obj = raw.get("invoice") if isinstance(raw.get("invoice"), dict) else {}
        invoice_no = as_text(invoice_obj.get("number")) if invoice_obj else ""
        amount = _as_float(raw.get("paymentAmount"))
        allocations.append(
            {
                "allocation_id": as_text(raw.get("id")),
                "invoice_id": invoice_id,
                "invoice_no": invoice_no,
                "payment_amount": amount,
            }
        )

    invoice_ids = sorted({a["invoice_id"] for a in allocations if a["invoice_id"]})
    review_reason = None
    if not allocations:
        review_reason = "Receipt tanpa detailInvoice — alokasi faktur kosong."
    elif any(a["payment_amount"] < 0 for a in allocations):
        review_reason = (
            "Receipt berisi paymentAmount negatif (refund/reversal). "
            "Aturan refund belum diputuskan Owner."
        )
    elif any(not a["invoice_id"] for a in allocations):
        review_reason = "Receipt memiliki alokasi tanpa invoiceId."
    elif len(invoice_ids) > 1:
        review_reason = (
            "Receipt dialokasikan ke lebih dari satu faktur. "
            "Aturan multi-faktur belum diputuskan Owner."
        )

    return {
        "accurate_receipt_id": accurate_id,
        "receipt_no": as_text(payload.get("number")),
        "trans_date": as_text(payload.get("transDate")),
        "total_payment": _as_float(payload.get("totalPayment")),
        "allocations": allocations,
        "invoice_ids": invoice_ids,
        "review_required": bool(review_reason),
        "review_reason": review_reason,
    }
