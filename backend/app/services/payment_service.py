"""Gate Payment dari penerimaan Accurate + bypass berizin Master."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.auth.permissions import PERM_BYPASS_PAYMENT, has_permission
from app.repositories import gate_repository as gates
from app.repositories import payment_repository as payments
from app.services.audit_log import append_audit_event


class PaymentPermissionError(PermissionError):
    """Aktor tidak memiliki izin Bypass Payment."""


def ingest_receipts(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    po_no: str,
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Simpan snapshot penerimaan Accurate (idempoten) lalu evaluasi gate."""
    stored = []
    for raw in receipts:
        stored.append(
            payments.upsert_receipt(
                db,
                accurate_receipt_id=str(raw.get("id") or raw.get("accurate_receipt_id") or ""),
                accurate_po_id=accurate_po_id,
                po_no=po_no,
                invoice_no=(raw.get("invoiceNo") or raw.get("invoice_no")),
                amount=raw.get("amount"),
                receipt_date=(raw.get("transDate") or raw.get("receipt_date")),
                raw_ref={"source": "accurate"},
            )
        )
    gate = evaluate_payment_gate(db, accurate_po_id)
    append_audit_event(
        db,
        action="payment.receipts.ingest",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={
            "accurate_po_id": accurate_po_id,
            "receipt_count": len(stored),
            "gate_state": gate["payment"]["state"],
        },
    )
    return {"receipts": stored, "gates": gate}


def evaluate_payment_gate(db: Database, accurate_po_id: str) -> dict[str, Any]:
    """Kasus normal membuka gate; kasus belum diputuskan → REVIEW, bukan open."""
    current = gates.get_gates(db, accurate_po_id).get("payment", {})
    if (current.get("detail") or {}).get("bypass") is True:
        return gates.get_gates(db, accurate_po_id)

    decision = payments.reconcile_po_payment(db, accurate_po_id)
    state = {
        "open": gates.GATE_OPEN,
        "review": gates.GATE_REVIEW,
        "closed": gates.GATE_CLOSED,
    }[decision["decision"]]
    return gates.set_gate(
        db,
        accurate_po_id=accurate_po_id,
        gate="payment",
        state=state,
        reason=decision["reason"],
        actor_email=None,
        detail={
            "total_amount": decision["total_amount"],
            "receipt_ids": decision["receipt_ids"],
            "auto": True,
        },
    )


def bypass_payment_gate(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    reason: str,
) -> dict[str, Any]:
    """Bypass hanya untuk pemegang izin ``payment.bypass`` — selalu teraudit."""
    if not has_permission(user.get("role"), PERM_BYPASS_PAYMENT):
        raise PaymentPermissionError("Akses Bypass Payment ditolak.")
    if not reason.strip():
        raise ValueError("Alasan bypass wajib diisi.")

    actor = str(user.get("email_normalized") or "")
    doc = gates.set_gate(
        db,
        accurate_po_id=accurate_po_id,
        gate="payment",
        state=gates.GATE_OPEN,
        reason=f"Bypass Payment: {reason.strip()}",
        actor_email=actor,
        detail={"bypass": True},
    )
    append_audit_event(
        db,
        action="payment.gate.bypass",
        actor_email=actor,
        target_email=None,
        detail={"accurate_po_id": accurate_po_id, "reason": reason.strip()},
    )
    return doc
