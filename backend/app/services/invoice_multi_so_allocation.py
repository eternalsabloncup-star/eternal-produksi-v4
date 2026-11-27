"""M9.3D — deterministic Multi-SO invoice allocation (read-only finance).

Accounting basis (Owner-audited):
  ALLOCATION_PER_SO = sum(detailItem.totalPrice) grouped by salesOrderId
  SUM(SO allocations) + UNALLOCATED = invoice totalAmount
  Rounding: Decimal quantize 0.01

Does NOT allocate receipts, open Payment Gate, or mutate review/payment status.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Final, Iterable

from app.integrations.accurate.fields import as_text
from app.services.branch_identity import normalize_branch_identity

_MONEY_QUANT: Final = Decimal("0.01")

ALLOC_FULLY: Final = "FULLY_ALLOCATED"
ALLOC_PARTIAL: Final = "PARTIALLY_ALLOCATED"
ALLOC_UNRESOLVED: Final = "UNRESOLVED"
ALLOC_UNSUPPORTED_TAX_DISCOUNT: Final = "UNSUPPORTED_TAX_DISCOUNT"
ALLOC_NOT_MULTI: Final = "NOT_MULTI_SO"
ALLOC_EXCLUDED: Final = "EXCLUDED"

_BASIS: Final = "detailItem.totalPrice"


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not amount.is_finite():
        return None
    return amount.quantize(_MONEY_QUANT)


def _money_out(amount: Decimal | None) -> str | None:
    if amount is None:
        return None
    return format(amount.quantize(_MONEY_QUANT), "f")


def _tax_discount_unsupported(doc: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    taxable = doc.get("taxable")
    if taxable is True:
        reasons.append("taxable invoice — tax allocation basis HOLD")
    tax1 = _as_decimal(doc.get("tax1_amount"))
    if tax1 is not None and tax1 != 0:
        reasons.append("non-zero tax1_amount — tax allocation basis HOLD")
    cash = _as_decimal(doc.get("cash_discount"))
    if cash is not None and cash != 0:
        reasons.append("non-zero cash_discount — discount allocation basis HOLD")
    if bool(doc.get("has_detail_expense")):
        reasons.append("detailExpense present — expense allocation not in M9.3D contract")
    return reasons


def _empty_allocation(
    *,
    status: str,
    reasons: list[str],
    invoice_total: Decimal | None = None,
) -> dict[str, Any]:
    return {
        "allocation_status": status,
        "allocation_basis": _BASIS,
        "verified_parent_so_ids": [],
        "allocations_by_so": [],
        "allocated_total": _money_out(Decimal("0.00")),
        "unallocated_total": _money_out(invoice_total)
        if invoice_total is not None
        else _money_out(Decimal("0.00")),
        "reconciliation_difference": _money_out(Decimal("0.00"))
        if invoice_total is None
        else _money_out(invoice_total),
        "payment_allocation": False,
        "payment_gate_eligible": False,
        "reasons": reasons,
    }


def allocate_multi_so_invoice(
    doc: dict[str, Any] | None,
    *,
    parent_so_docs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive SO allocations from stored invoice lines.

    ``parent_so_docs`` maps accurate SO id → local Accurate SO snapshot (optional).
    When provided, missing parents or branch/customer mismatches fail-closed to
    UNRESOLVED. When omitted, parent verification is skipped (pure line math only)
    but status will note ``parent_verification=skipped``.
    """
    if not isinstance(doc, dict):
        return _empty_allocation(status=ALLOC_UNRESOLVED, reasons=["invoice missing"])

    document_kind = as_text(doc.get("document_kind")).upper()
    lines = [ln for ln in (doc.get("lines") or []) if isinstance(ln, dict)]
    invoice_total = _as_decimal(doc.get("total_amount"))

    if document_kind == "SRT" or (
        bool(doc.get("has_detail_expense")) and not lines
    ):
        return _empty_allocation(
            status=ALLOC_EXCLUDED,
            reasons=["SRT/expense-only — not multi-SO sales allocation"],
            invoice_total=invoice_total,
        )

    if not lines:
        return _empty_allocation(
            status=ALLOC_EXCLUDED,
            reasons=["empty detailItem — empty-detail manual review (M9.3E)"],
            invoice_total=invoice_total,
        )

    po_ids = sorted(
        {
            as_text(x).strip()
            for x in (doc.get("accurate_po_ids") or [])
            if as_text(x).strip()
        }
    )
    line_so_ids = sorted(
        {
            as_text(ln.get("sales_order_id")).strip()
            for ln in lines
            if as_text(ln.get("sales_order_id")).strip()
        }
    )
    unique_sos = sorted({*po_ids, *line_so_ids})
    if len(unique_sos) <= 1:
        return _empty_allocation(
            status=ALLOC_NOT_MULTI,
            reasons=["not a multi-SO invoice"],
            invoice_total=invoice_total,
        )

    tax_reasons = _tax_discount_unsupported(doc)
    if tax_reasons:
        return {
            **_empty_allocation(
                status=ALLOC_UNSUPPORTED_TAX_DISCOUNT,
                reasons=tax_reasons,
                invoice_total=invoice_total,
            ),
            "verified_parent_so_ids": unique_sos,
        }

    if invoice_total is None:
        return _empty_allocation(
            status=ALLOC_UNRESOLVED,
            reasons=["invoice total_amount missing"],
        )

    customer_id = as_text(doc.get("customer_id")).strip()
    branch_id = normalize_branch_identity(doc.get("branch_id"))
    if not customer_id or not branch_id:
        return _empty_allocation(
            status=ALLOC_UNRESOLVED,
            reasons=["customer/branch identity missing — fail-closed"],
            invoice_total=invoice_total,
        )

    reasons: list[str] = []
    by_so: dict[str, Decimal] = {sid: Decimal("0.00") for sid in unique_sos}
    unallocated = Decimal("0.00")
    line_rows: list[dict[str, Any]] = []
    unresolved_lines = 0

    for ln in lines:
        so = as_text(ln.get("sales_order_id")).strip()
        line_id = as_text(ln.get("accurate_line_id")).strip()
        sod = as_text(ln.get("sales_order_detail_id")).strip()
        total_price = _as_decimal(ln.get("total_price"))
        if not line_id:
            unresolved_lines += 1
            reasons.append("line missing accurate_line_id")
            continue
        if total_price is None:
            unresolved_lines += 1
            reasons.append(f"line {line_id}: total_price missing/invalid")
            # Cannot assign — do not guess SO amount.
            continue
        if not so:
            unallocated += total_price
            unresolved_lines += 1
            reasons.append(f"line {line_id}: sales_order_id missing")
            line_rows.append(
                {
                    "accurate_line_id": line_id,
                    "sales_order_id": None,
                    "sales_order_detail_id": sod or None,
                    "total_price": _money_out(total_price),
                    "allocated": False,
                }
            )
            continue
        if so not in by_so:
            by_so[so] = Decimal("0.00")
        by_so[so] += total_price
        line_rows.append(
            {
                "accurate_line_id": line_id,
                "sales_order_id": so,
                "sales_order_detail_id": sod or None,
                "item_id": as_text(ln.get("item_id")) or None,
                "qty": ln.get("qty"),
                "total_price": _money_out(total_price),
                "allocated": True,
            }
        )

    # Parent SO verification when snapshots provided.
    parent_issues: list[str] = []
    verified_parents: list[str] = []
    if parent_so_docs is None:
        reasons.append("parent_verification=skipped")
        verified_parents = sorted(by_so.keys())
    else:
        for sid in sorted(by_so.keys()):
            parent = parent_so_docs.get(sid)
            if not parent:
                parent_issues.append(f"parent SO {sid} missing locally")
                continue
            parent_branch = normalize_branch_identity(parent.get("branch_id"))
            if parent_branch and branch_id and parent_branch != branch_id:
                parent_issues.append(
                    f"parent SO {sid} branch mismatch ({parent_branch}≠{branch_id})"
                )
                continue
            # Customer: header.customer is a name in ASO; customer_id may be absent.
            # Fail-closed only when parent exposes an explicit customer_id that differs.
            parent_cust = as_text(
                parent.get("customer_id")
                or (parent.get("header") or {}).get("customer_id")
                or (parent.get("header") or {}).get("customerId")
            ).strip()
            if parent_cust and customer_id and parent_cust != customer_id:
                parent_issues.append(
                    f"parent SO {sid} customer mismatch ({parent_cust}≠{customer_id})"
                )
                continue
            verified_parents.append(sid)

    if parent_issues:
        return {
            **_empty_allocation(
                status=ALLOC_UNRESOLVED,
                reasons=reasons + parent_issues,
                invoice_total=invoice_total,
            ),
            "verified_parent_so_ids": verified_parents,
            "line_allocations": line_rows,
        }

    allocated_total = sum(by_so.values(), Decimal("0.00")).quantize(_MONEY_QUANT)
    unallocated = unallocated.quantize(_MONEY_QUANT)
    recon = (invoice_total - (allocated_total + unallocated)).quantize(_MONEY_QUANT)

    allocations_by_so = [
        {
            "accurate_so_id": sid,
            "allocated_amount": _money_out(amt.quantize(_MONEY_QUANT)),
            "parent_verified": sid in verified_parents,
        }
        for sid, amt in sorted(by_so.items(), key=lambda x: x[0])
        if amt != 0 or sid in verified_parents
    ]

    if recon != 0:
        status = ALLOC_UNRESOLVED
        reasons.append(
            f"reconciliation mismatch difference={_money_out(recon)} "
            f"(allocated={_money_out(allocated_total)} unallocated={_money_out(unallocated)} "
            f"invoice_total={_money_out(invoice_total)})"
        )
    elif unresolved_lines or unallocated != 0:
        status = ALLOC_PARTIAL
        reasons.append("one or more lines unallocated — remainder not assigned to primary SO")
    else:
        status = ALLOC_FULLY
        reasons.append("multi-SO allocation fully reconciles on detailItem.totalPrice basis")

    return {
        "allocation_status": status,
        "allocation_basis": _BASIS,
        "verified_parent_so_ids": verified_parents,
        "allocations_by_so": allocations_by_so,
        "allocated_total": _money_out(allocated_total),
        "unallocated_total": _money_out(unallocated),
        "reconciliation_difference": _money_out(recon),
        "line_allocations": line_rows,
        "payment_allocation": False,
        "payment_gate_eligible": False,
        "reasons": reasons,
    }


def load_parent_so_docs(
    db: Any, so_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Fetch local Accurate SO snapshots for parent verification (read-only)."""
    from app.repositories.po_repository import get_by_accurate_id

    out: dict[str, dict[str, Any]] = {}
    for sid in so_ids:
        sid_s = as_text(sid).strip()
        if not sid_s:
            continue
        doc = get_by_accurate_id(db, sid_s)
        if doc:
            out[sid_s] = doc
    return out
