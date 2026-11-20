"""Gate Payment dari penerimaan Accurate + bypass berizin Master."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.repositories import gate_repository as gates
from app.repositories import payment_repository as payments
from app.services.audit_log import append_audit_event
from app.services.task_engine import TaskPermissionError, require_action


class PaymentPermissionError(PermissionError):
    """Aktor tidak memiliki izin Bypass Payment."""


def ingest_receipts(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    po_no: str,
    receipts: list[dict[str, Any]],
    skip_unchanged: bool = False,
    raw_ref_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Simpan snapshot penerimaan Accurate lalu evaluasi gate.

    ``skip_unchanged=True``: receipt material identik → zero write, zero audit,
    zero gate history append.
    """
    stored = []
    written = 0
    for raw in receipts:
        receipt_id = str(raw.get("id") or raw.get("accurate_receipt_id") or "")
        invoice_no = raw.get("invoiceNo") or raw.get("invoice_no")
        amount = raw.get("amount")
        receipt_date = raw.get("transDate") or raw.get("receipt_date")
        raw_ref = {"source": "accurate", **dict(raw_ref_extra or {})}
        if skip_unchanged:
            existing = payments.receipts_collection(db).find_one(
                {"accurate_receipt_id": receipt_id}, {"_id": 0}
            )
            if existing is not None:
                from app.integrations.accurate.normalize_invoice_receipt import (
                    receipt_material_fingerprint,
                )

                candidate = {
                    "accurate_receipt_id": receipt_id,
                    "accurate_po_id": accurate_po_id,
                    "po_no": po_no,
                    "invoice_no": invoice_no,
                    "amount": float(amount) if amount is not None else None,
                    "receipt_date": receipt_date,
                    "raw_ref": raw_ref,
                }
                # Bandingkan amount dengan storage float existing.
                if amount is not None:
                    candidate["amount"] = payments._as_float(amount)
                if receipt_material_fingerprint(existing) == receipt_material_fingerprint(
                    candidate
                ):
                    stored.append(existing)
                    continue

        stored.append(
            payments.upsert_receipt(
                db,
                accurate_receipt_id=receipt_id,
                accurate_po_id=accurate_po_id,
                po_no=po_no,
                invoice_no=invoice_no,
                amount=amount if amount is not None else 0.0,
                receipt_date=receipt_date,
                raw_ref=raw_ref,
            )
        )
        written += 1

    if written == 0 and skip_unchanged:
        return {
            "receipts": stored,
            "gates": gates.get_gates(db, accurate_po_id),
            "written": 0,
            "gate_mutated": False,
            "audited": False,
        }

    gate, gate_mutated = evaluate_payment_gate(db, accurate_po_id, only_if_changed=True)
    append_audit_event(
        db,
        action="payment.receipts.ingest",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={
            "accurate_po_id": accurate_po_id,
            "receipt_count": len(stored),
            "written": written,
            "gate_state": gate["payment"]["state"],
        },
    )
    return {
        "receipts": stored,
        "gates": gate,
        "written": written,
        "gate_mutated": gate_mutated,
        "audited": True,
    }


def evaluate_payment_gate(
    db: Database,
    accurate_po_id: str,
    *,
    only_if_changed: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], bool]:
    """Kasus normal membuka gate; kasus belum diputuskan → REVIEW, bukan open.

    ``only_if_changed``: kembalikan ``(doc, mutated)`` dan skip history bila state sama.
    """
    current_doc = gates.get_gates(db, accurate_po_id)
    current = current_doc.get("payment", {})
    if (current.get("detail") or {}).get("bypass") is True:
        if only_if_changed:
            return current_doc, False
        return current_doc

    decision = payments.reconcile_po_payment(db, accurate_po_id)
    state = {
        "open": gates.GATE_OPEN,
        "review": gates.GATE_REVIEW,
        "closed": gates.GATE_CLOSED,
    }[decision["decision"]]
    detail = {
        "total_amount": decision["total_amount"],
        "receipt_ids": decision["receipt_ids"],
        "auto": True,
    }
    if only_if_changed:
        if (
            current.get("state") == state
            and current.get("reason") == decision["reason"]
            and (current.get("detail") or {}).get("receipt_ids") == detail["receipt_ids"]
            and (current.get("detail") or {}).get("total_amount") == detail["total_amount"]
        ):
            return current_doc, False

    doc = gates.set_gate(
        db,
        accurate_po_id=accurate_po_id,
        gate="payment",
        state=state,
        reason=decision["reason"],
        actor_email=None,
        detail=detail,
    )
    if only_if_changed:
        return doc, True
    return doc


def bypass_payment_gate(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    reason: str,
) -> dict[str, Any]:
    """Bypass mengikuti grant Master ``payment-bypass`` / approve — selalu teraudit.

    Super Admin lolos via task_engine.can_act. Role lain hanya jika Master
    memberikan aksi approve pada jenis pekerjaan payment-bypass.
    """
    try:
        require_action(db, user, task_type="payment-bypass", action="approve")
    except TaskPermissionError as exc:
        raise PaymentPermissionError(str(exc)) from exc
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
