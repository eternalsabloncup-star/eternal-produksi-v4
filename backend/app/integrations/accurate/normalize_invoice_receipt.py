"""Normalizer Faktur & Penerimaan — field dari kontrak live Accurate (read-only).

Relasi terbukti:
- Invoice line ``detailItem[].salesOrderId`` → Accurate SO / PO id.
- Receipt ``detailInvoice[].invoiceId`` + ``paymentAmount`` → alokasi faktur.
Header ``salesOrder`` pada invoice sering boolean — jangan dipakai sebagai id.

Nominal: fail-closed — nilai invalid tidak diubah diam-diam menjadi 0.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.integrations.accurate.fields import as_text

_MONEY_QUANT = Decimal("0.01")


def parse_money(value: Any) -> tuple[Decimal | None, str | None]:
    """Parse nominal finansial. None + reason bila invalid/nonfinite/malformed.

    Tidak mengembalikan 0 sebagai pengganti nilai rusak.
    """
    if value is None:
        return None, "nominal kosong"
    if isinstance(value, bool):
        return None, "nominal boolean tidak valid"
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/Inf
            return None, "nominal nonfinite"
    text = str(value).replace(",", "").strip()
    if not text:
        return None, "nominal kosong"
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, "nominal malformed"
    if not amount.is_finite():
        return None, "nominal nonfinite"
    # Simpan skala 2 desimal (kontrak uang existing); rounding hanya setelah valid.
    return amount.quantize(_MONEY_QUANT), None


def money_to_storage(amount: Decimal) -> float:
    """Representasi storage float dari Decimal tervalidasi (kompatibel payment repo)."""
    return float(amount)


_TRUE_TOKENS = frozenset({"true", "1", "yes", "ya"})
_FALSE_TOKENS = frozenset({"false", "0", "no", "tidak"})


def parse_accurate_bool(value: Any) -> tuple[bool | None, str | None]:
    """Parse boolean kontrak Accurate (voidCheq dkk).

    Returns ``(parsed, error)``:
    - ``(True/False, None)`` — nilai dikenal
    - ``(None, None)`` — null/missing (caller memutuskan default kontrak)
    - ``(None, reason)`` — ambigu/tidak dikenal → REVIEW, jangan diam-diam False

    Tidak memakai ``bool(str)`` — ``bool("false")`` adalah True di Python.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (
            value != value or value in (float("inf"), float("-inf"))
        ):
            return None, "boolean nonfinite"
        if value == 1:
            return True, None
        if value == 0:
            return False, None
        return None, "boolean numerik tidak dikenal"
    text = as_text(value).strip().lower()
    if not text:
        return None, None
    if text in _TRUE_TOKENS:
        return True, None
    if text in _FALSE_TOKENS:
        return False, None
    return None, f"boolean ambigu: {text!r}"


def _material_invoice_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "accurate_invoice_id": doc.get("accurate_invoice_id"),
        "invoice_no": doc.get("invoice_no"),
        "trans_date": doc.get("trans_date"),
        "status": doc.get("status"),
        "total_amount": doc.get("total_amount"),
        "sub_total": doc.get("sub_total"),
        "accurate_po_ids": list(doc.get("accurate_po_ids") or []),
        "primary_accurate_po_id": doc.get("primary_accurate_po_id"),
        "lines": list(doc.get("lines") or []),
        "review_required": bool(doc.get("review_required")),
        "review_reason": doc.get("review_reason"),
        # Additive identity/shape fields (M9.3C) — do not invent values.
        "customer_id": doc.get("customer_id"),
        "branch_id": doc.get("branch_id"),
        "document_kind": doc.get("document_kind"),
        "has_detail_expense": bool(doc.get("has_detail_expense")),
        "currency": doc.get("currency"),
    }


def invoice_material_equal(a: dict[str, Any] | None, b: dict[str, Any]) -> bool:
    if a is None:
        return False
    return _material_invoice_fields(a) == _material_invoice_fields(b)


def normalize_sales_invoice(payload: dict[str, Any]) -> dict[str, Any]:
    accurate_id = as_text(payload.get("id"))
    if not accurate_id:
        raise ValueError("Sales invoice Accurate tanpa id.")

    review_reasons: list[str] = []
    po_ids: list[str] = []
    lines: list[dict[str, Any]] = []
    for raw in payload.get("detailItem") or []:
        if not isinstance(raw, dict):
            continue
        so_id = as_text(raw.get("salesOrderId"))
        if so_id and so_id not in po_ids:
            po_ids.append(so_id)
        qty, qty_err = parse_money(raw.get("quantity"))
        if qty_err:
            review_reasons.append(f"qty line invalid: {qty_err}")
            qty_store: float | None = None
        else:
            assert qty is not None
            qty_store = money_to_storage(qty)
        lines.append(
            {
                "accurate_line_id": as_text(raw.get("id")),
                "sales_order_id": so_id,
                "sales_order_detail_id": as_text(raw.get("salesOrderDetailId")),
                "item_id": as_text(raw.get("itemId")),
                "qty": qty_store,
            }
        )

    total_amount, total_err = parse_money(payload.get("totalAmount"))
    sub_total, sub_err = parse_money(payload.get("subTotal"))
    if total_err:
        review_reasons.append(f"totalAmount: {total_err}")
    if sub_err:
        review_reasons.append(f"subTotal: {sub_err}")

    if not po_ids:
        review_reasons.append(
            "Invoice tanpa salesOrderId pada detailItem — relasi PO tidak terverifikasi."
        )
    elif len(po_ids) > 1:
        review_reasons.append(
            "Invoice terkait lebih dari satu Sales Order. "
            "Aturan multi-SO per faktur belum diputuskan Owner."
        )

    review_reason = "; ".join(review_reasons) if review_reasons else None

    invoice_no = as_text(payload.get("number"))
    expense_rows = [
        row for row in (payload.get("detailExpense") or []) if isinstance(row, dict)
    ]
    has_detail_expense = len(expense_rows) > 0
    # Local import avoids circular import with finance classification helpers.
    from app.services.invoice_finance_classification import infer_document_kind
    from app.services.branch_identity import normalize_branch_identity

    customer_id = as_text(payload.get("customerId")).strip()
    branch_id = normalize_branch_identity(payload.get("branchId"))
    currency = ""
    raw_currency = payload.get("currency")
    if isinstance(raw_currency, dict):
        currency = as_text(raw_currency.get("code") or raw_currency.get("id"))
    else:
        currency = as_text(payload.get("currencyCode") or raw_currency)

    document_kind = infer_document_kind(
        invoice_no=invoice_no,
        has_detail_expense=has_detail_expense,
        item_line_count=len(lines),
    )

    return {
        "accurate_invoice_id": accurate_id,
        "invoice_no": invoice_no,
        "trans_date": as_text(payload.get("transDate")),
        "status": as_text(payload.get("status") or payload.get("statusName")),
        "total_amount": money_to_storage(total_amount) if total_amount is not None else None,
        "sub_total": money_to_storage(sub_total) if sub_total is not None else None,
        "accurate_po_ids": po_ids,
        "primary_accurate_po_id": po_ids[0] if len(po_ids) == 1 else "",
        "lines": lines,
        "review_required": bool(review_reason),
        "review_reason": review_reason,
        "customer_id": customer_id or None,
        "branch_id": branch_id,
        "document_kind": document_kind,
        "has_detail_expense": has_detail_expense,
        "currency": currency or None,
    }


def normalize_sales_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    accurate_id = as_text(payload.get("id"))
    if not accurate_id:
        raise ValueError("Sales receipt Accurate tanpa id.")

    review_reasons: list[str] = []
    allocations: list[dict[str, Any]] = []
    alloc_sum = Decimal("0")

    for raw in payload.get("detailInvoice") or []:
        if not isinstance(raw, dict):
            continue
        invoice_id = as_text(raw.get("invoiceId"))
        invoice_obj = raw.get("invoice") if isinstance(raw.get("invoice"), dict) else {}
        invoice_no = as_text(invoice_obj.get("number")) if invoice_obj else ""
        # Jangan fallback ke totalPayment untuk menebak alokasi.
        if "paymentAmount" not in raw:
            review_reasons.append("alokasi tanpa paymentAmount")
            amount_store: float | None = None
            amount_dec: Decimal | None = None
        else:
            amount_dec, amount_err = parse_money(raw.get("paymentAmount"))
            if amount_err:
                review_reasons.append(f"paymentAmount: {amount_err}")
                amount_store = None
            else:
                assert amount_dec is not None
                amount_store = money_to_storage(amount_dec)
                alloc_sum += amount_dec
        allocations.append(
            {
                "allocation_id": as_text(raw.get("id")),
                "invoice_id": invoice_id,
                "invoice_no": invoice_no,
                "payment_amount": amount_store,
            }
        )

    total_payment, total_err = parse_money(payload.get("totalPayment"))
    if total_err:
        review_reasons.append(f"totalPayment: {total_err}")

    invoice_ids = sorted({a["invoice_id"] for a in allocations if a["invoice_id"]})

    if not allocations:
        review_reasons.append("Receipt tanpa detailInvoice — alokasi faktur kosong.")
    if any(a["payment_amount"] is not None and a["payment_amount"] < 0 for a in allocations):
        review_reasons.append(
            "Receipt berisi paymentAmount negatif (refund/reversal). "
            "Aturan refund belum diputuskan Owner."
        )
    if any(not a["invoice_id"] for a in allocations):
        review_reasons.append("Receipt memiliki alokasi tanpa invoiceId.")
    if len(invoice_ids) > 1:
        review_reasons.append(
            "Receipt dialokasikan ke lebih dari satu faktur. "
            "Aturan multi-faktur belum diputuskan Owner."
        )
    if any(a["payment_amount"] is None for a in allocations):
        review_reasons.append("Receipt memiliki alokasi dengan nominal tidak valid.")

    if total_payment is not None and allocations and all(
        a["payment_amount"] is not None for a in allocations
    ):
        if alloc_sum.quantize(_MONEY_QUANT) != total_payment.quantize(_MONEY_QUANT):
            review_reasons.append(
                "totalPayment tidak cocok dengan jumlah paymentAmount alokasi."
            )

    # Kontrak live: voidCheq boolean. Missing/null = bukan void (False).
    # Ambigu → REVIEW fail-closed; storage void_cheq True agar tidak dianggap bersih.
    void_parsed, void_err = parse_accurate_bool(payload.get("voidCheq"))
    if void_err is not None:
        review_reasons.append(
            f"voidCheq tidak dapat ditafsirkan ({void_err}). "
            "Aturan void ambigu — gate dievaluasi REVIEW."
        )
        void_cheq = True
    elif void_parsed is True:
        review_reasons.append(
            "Receipt ditandai void/reversal (voidCheq). "
            "Aturan void belum diputuskan Owner — gate tidak boleh tetap terbuka."
        )
        void_cheq = True
    else:
        void_cheq = False

    review_reason = "; ".join(dict.fromkeys(review_reasons)) if review_reasons else None
    return {
        "accurate_receipt_id": accurate_id,
        "receipt_no": as_text(payload.get("number")),
        "trans_date": as_text(payload.get("transDate")),
        "total_payment": money_to_storage(total_payment) if total_payment is not None else None,
        "allocations": allocations,
        "invoice_ids": invoice_ids,
        "void_cheq": void_cheq,
        "review_required": bool(review_reason),
        "review_reason": review_reason,
    }


def receipt_material_fingerprint(doc: dict[str, Any]) -> dict[str, Any]:
    """Field material untuk idempotency (tanpa synced_at)."""
    raw = doc.get("raw_ref") if isinstance(doc.get("raw_ref"), dict) else {}
    return {
        "accurate_receipt_id": str(doc.get("accurate_receipt_id") or ""),
        "accurate_po_id": str(doc.get("accurate_po_id") or ""),
        "po_no": doc.get("po_no"),
        "invoice_no": doc.get("invoice_no"),
        "amount": doc.get("amount"),
        "receipt_date": doc.get("receipt_date"),
        "review_required": bool(raw.get("review_required")),
        "review_reason": raw.get("review_reason"),
        "receipt_no": raw.get("receipt_no"),
        "invoice_ids": list(raw.get("invoice_ids") or []),
        "void_cheq": True if raw.get("void_cheq") is True else False,
        "allocations": list(raw.get("allocations") or []),
    }
