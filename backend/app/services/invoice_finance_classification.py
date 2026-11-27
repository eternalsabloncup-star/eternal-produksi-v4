"""M9.3C — derived finance classification for Accurate sales invoices.

Independent dimensions (conceptual contract):
- financial document class
- PO association class
- original review status (unchanged by this module)
- PO Payment Gate eligibility

Pure / deterministic / fail-closed. Does not mutate invoice documents,
resolve reviews, invent SO/PO links, or open Payment Gate.
"""

from __future__ import annotations

from typing import Any, Final

from app.integrations.accurate.fields import as_text
from app.services.branch_identity import normalize_branch_identity

FINANCE_VALID: Final = "FINANCE_VALID"
FINANCE_UNKNOWN: Final = "FINANCE_UNKNOWN"
FINANCE_INCOMPLETE_DETAIL: Final = "FINANCE_INCOMPLETE_DETAIL"
FINANCE_EXCLUDED_SRT_EXPENSE: Final = "FINANCE_EXCLUDED_SRT_EXPENSE"
FINANCE_NOT_STANDALONE: Final = "FINANCE_NOT_STANDALONE"

PO_ASSOCIATION_NONE: Final = "NONE"
PO_ASSOCIATION_SINGLE: Final = "SINGLE"
PO_ASSOCIATION_MULTI: Final = "MULTI"
PO_ASSOCIATION_UNKNOWN: Final = "UNKNOWN"

DOC_KIND_INV: Final = "INV"
DOC_KIND_SRT: Final = "SRT"
DOC_KIND_UNKNOWN: Final = "UNKNOWN"

_LABELS_FINANCE = {
    FINANCE_VALID: "Valid",
    FINANCE_UNKNOWN: "Belum Terverifikasi",
    FINANCE_INCOMPLETE_DETAIL: "Detail Tidak Lengkap",
    FINANCE_EXCLUDED_SRT_EXPENSE: "Dokumen Beban/SRT",
    FINANCE_NOT_STANDALONE: "Bukan Standalone",
}

_LABELS_PO = {
    PO_ASSOCIATION_NONE: "Tanpa SO",
    PO_ASSOCIATION_SINGLE: "Satu SO",
    PO_ASSOCIATION_MULTI: "Multi-SO",
    PO_ASSOCIATION_UNKNOWN: "Belum Terverifikasi",
}


def infer_document_kind(
    *,
    invoice_no: str | None,
    has_detail_expense: bool,
    item_line_count: int,
) -> str:
    """Classify source document shape without inventing types.

    SRT expense-only (expense lines, no item detail) must not be treated as INV.
    """
    number = as_text(invoice_no).strip().upper()
    looks_srt = number.startswith("SRT.") or number.startswith("SRT/") or number.startswith(
        "SRT"
    )
    if looks_srt or (has_detail_expense and item_line_count == 0):
        return DOC_KIND_SRT
    if number:
        return DOC_KIND_INV
    return DOC_KIND_UNKNOWN


def _line_so_ids(lines: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        so = as_text(line.get("sales_order_id")).strip()
        if so:
            out.append(so)
    return out


def _po_association(
    *,
    po_ids: list[str],
    line_so_ids: list[str],
    item_line_count: int,
) -> str:
    unique = sorted({*po_ids, *line_so_ids})
    if unique:
        return PO_ASSOCIATION_MULTI if len(unique) > 1 else PO_ASSOCIATION_SINGLE
    if item_line_count <= 0:
        return PO_ASSOCIATION_UNKNOWN
    return PO_ASSOCIATION_NONE


def _payment_gate_eligible(doc: dict[str, Any]) -> bool:
    """Mirror existing receipt→gate prerequisites — never invent eligibility."""
    if bool(doc.get("review_required")):
        return False
    primary = as_text(doc.get("primary_accurate_po_id")).strip()
    po_ids = [as_text(x).strip() for x in (doc.get("accurate_po_ids") or []) if as_text(x).strip()]
    if not primary or len(po_ids) != 1 or po_ids[0] != primary:
        return False
    return True


def classify_invoice_finance(doc: dict[str, Any] | None) -> dict[str, Any]:
    """Derive finance / association / gate dimensions from a stored invoice doc.

    Standalone FINANCE_VALID requires authoritative combination:
    - INV document kind (not SRT/expense-only)
    - non-empty item detail lines
    - verified absence of SO on all lines and accurate_po_ids
    - customer_id and branch_id present
    - total_amount present (may be 0.0)

    ``salesOrderId`` null alone is never sufficient.
    """
    if not isinstance(doc, dict) or not as_text(doc.get("accurate_invoice_id")).strip():
        return {
            "finance_class": FINANCE_UNKNOWN,
            "finance_label": _LABELS_FINANCE[FINANCE_UNKNOWN],
            "po_association": PO_ASSOCIATION_UNKNOWN,
            "po_association_label": _LABELS_PO[PO_ASSOCIATION_UNKNOWN],
            "review_required": True,
            "review_reason": None,
            "payment_gate_eligible": False,
            "payment_gate_label": "Tidak Berlaku",
            "document_kind": DOC_KIND_UNKNOWN,
            "reasons": ["invoice identity missing"],
        }

    lines = [ln for ln in (doc.get("lines") or []) if isinstance(ln, dict)]
    item_line_count = len(lines)
    has_detail_expense = bool(doc.get("has_detail_expense"))
    document_kind = as_text(doc.get("document_kind")).strip().upper() or infer_document_kind(
        invoice_no=doc.get("invoice_no"),
        has_detail_expense=has_detail_expense,
        item_line_count=item_line_count,
    )
    if document_kind not in {DOC_KIND_INV, DOC_KIND_SRT, DOC_KIND_UNKNOWN}:
        document_kind = DOC_KIND_UNKNOWN

    po_ids = [as_text(x).strip() for x in (doc.get("accurate_po_ids") or []) if as_text(x).strip()]
    line_sos = _line_so_ids(lines)
    association = _po_association(
        po_ids=po_ids, line_so_ids=line_sos, item_line_count=item_line_count
    )

    customer_id = as_text(doc.get("customer_id")).strip()
    branch_id = normalize_branch_identity(doc.get("branch_id"))
    total_amount = doc.get("total_amount")
    review_required = bool(doc.get("review_required"))
    review_reason = doc.get("review_reason")
    gate_eligible = _payment_gate_eligible(doc)

    reasons: list[str] = []
    finance_class = FINANCE_UNKNOWN

    if document_kind == DOC_KIND_SRT or (has_detail_expense and item_line_count == 0):
        finance_class = FINANCE_EXCLUDED_SRT_EXPENSE
        reasons.append("SRT/expense-only document — not a standalone sales invoice")
        association = (
            association if association in {PO_ASSOCIATION_SINGLE, PO_ASSOCIATION_MULTI} else PO_ASSOCIATION_NONE
        )
    elif association in {PO_ASSOCIATION_SINGLE, PO_ASSOCIATION_MULTI}:
        finance_class = FINANCE_NOT_STANDALONE
        reasons.append("invoice has verified Sales Order association")
    elif item_line_count <= 0:
        finance_class = FINANCE_INCOMPLETE_DETAIL
        reasons.append("detailItem/item lines empty — incomplete source detail")
        association = PO_ASSOCIATION_UNKNOWN
    elif association != PO_ASSOCIATION_NONE:
        finance_class = FINANCE_UNKNOWN
        reasons.append("PO association unresolved")
    elif not customer_id:
        finance_class = FINANCE_UNKNOWN
        reasons.append("customer identity missing")
    elif not branch_id:
        finance_class = FINANCE_UNKNOWN
        reasons.append("branch identity missing")
    elif total_amount is None:
        finance_class = FINANCE_UNKNOWN
        reasons.append("financial total missing")
    else:
        # Verified standalone: item lines present, zero SO links, identities present.
        finance_class = FINANCE_VALID
        reasons.append("standalone INV with complete item detail and identities")

    # Standalone finance-valid can never drive Payment Gate.
    if finance_class == FINANCE_VALID:
        gate_eligible = False

    return {
        "finance_class": finance_class,
        "finance_label": _LABELS_FINANCE.get(finance_class, finance_class),
        "po_association": association,
        "po_association_label": _LABELS_PO.get(association, association),
        "review_required": review_required,
        "review_reason": review_reason,
        "payment_gate_eligible": bool(gate_eligible),
        "payment_gate_label": (
            "Memenuhi Prasyarat Mapping"
            if gate_eligible
            else "Tidak Berlaku"
        ),
        "document_kind": document_kind,
        "reasons": reasons,
        "accurate_invoice_id": as_text(doc.get("accurate_invoice_id")),
        "invoice_no": as_text(doc.get("invoice_no")),
        "customer_id": customer_id or None,
        "branch_id": branch_id,
        "status": as_text(doc.get("status")) or None,
        "total_amount": total_amount,
        "trans_date": as_text(doc.get("trans_date")) or None,
        "currency": as_text(doc.get("currency")) or None,
    }


def finance_invoice_view(
    doc: dict[str, Any],
    *,
    db: Any | None = None,
) -> dict[str, Any]:
    """Additive finance read-model row — does not replace invoice source of truth.

    When ``db`` is provided, Multi-SO parent SO snapshots are loaded for
    fail-closed verification (M9.3D). Allocation never opens Payment Gate.
    """
    classified = classify_invoice_finance(doc)
    from app.services.invoice_multi_so_allocation import (
        allocate_multi_so_invoice,
        load_parent_so_docs,
    )

    parent_docs = None
    if db is not None and classified.get("po_association") == PO_ASSOCIATION_MULTI:
        so_ids = [
            as_text(x).strip()
            for x in (doc.get("accurate_po_ids") or [])
            if as_text(x).strip()
        ]
        # Also include line SO ids for completeness.
        for ln in doc.get("lines") or []:
            if isinstance(ln, dict):
                sid = as_text(ln.get("sales_order_id")).strip()
                if sid and sid not in so_ids:
                    so_ids.append(sid)
        parent_docs = load_parent_so_docs(db, so_ids)

    allocation = allocate_multi_so_invoice(doc, parent_so_docs=parent_docs)
    # Hard isolation: allocation never grants payment eligibility.
    payment_gate_eligible = False
    if classified.get("po_association") == PO_ASSOCIATION_MULTI:
        payment_gate_eligible = False
    else:
        payment_gate_eligible = bool(classified.get("payment_gate_eligible"))

    return {
        **classified,
        "payment_gate_eligible": payment_gate_eligible,
        "payment_gate_label": (
            "Memenuhi Prasyarat Mapping"
            if payment_gate_eligible
            else "Tidak Berlaku"
        ),
        "source_status": as_text(doc.get("status")) or None,
        "accurate_po_ids": [
            as_text(x).strip()
            for x in (doc.get("accurate_po_ids") or [])
            if as_text(x).strip()
        ],
        "primary_accurate_po_id": as_text(doc.get("primary_accurate_po_id")).strip() or "",
        "allocation_status": allocation.get("allocation_status"),
        "allocation_basis": allocation.get("allocation_basis"),
        "verified_parent_so_ids": allocation.get("verified_parent_so_ids") or [],
        "allocations_by_so": allocation.get("allocations_by_so") or [],
        "allocated_total": allocation.get("allocated_total"),
        "unallocated_total": allocation.get("unallocated_total"),
        "reconciliation_difference": allocation.get("reconciliation_difference"),
        "payment_allocation": False,
        "allocation_reasons": allocation.get("reasons") or [],
    }
