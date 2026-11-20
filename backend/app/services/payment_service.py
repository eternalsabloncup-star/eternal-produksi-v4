"""Gate Payment dari penerimaan Accurate + bypass berizin Master."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database
from pymongo.errors import ConnectionFailure, OperationFailure

from app.repositories import gate_repository as gates
from app.repositories import payment_repository as payments
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event
from app.services.task_engine import TaskPermissionError, require_action


class PaymentPermissionError(PermissionError):
    """Aktor tidak memiliki izin Bypass Payment."""


class PaymentGateConflictError(Exception):
    """Gate payment sudah terbuka tanpa bypass — tidak diubah menjadi bypass."""


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


def _payment_is_active_bypass(payment: dict[str, Any] | None) -> bool:
    if not isinstance(payment, dict):
        return False
    return (
        payment.get("state") == gates.GATE_OPEN
        and bool((payment.get("detail") or {}).get("bypass"))
    )


def bypass_payment_gate(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    reason: str,
) -> dict[str, Any]:
    """Canonical Payment Bypass: ``operations.full`` OR explicit bypass grant.

    Atomik: klaim gate + history + audit (+ derive tugas bila design open)
    dalam satu transaksi Mongo. Idempotent jika sudah bypass aktif.
    Tidak mengubah gate yang sudah open karena pembayaran normal.
    """
    try:
        require_action(db, user, task_type="payment-bypass", action="approve")
    except TaskPermissionError as exc:
        raise PaymentPermissionError(str(exc)) from exc

    cleaned = reason.strip()
    if len(cleaned) < 3:
        raise ValueError("Alasan bypass wajib diisi (minimal 3 karakter).")
    if len(cleaned) > 500:
        raise ValueError("Alasan bypass maksimal 500 karakter.")

    from app.repositories.po_repository import get_by_accurate_id
    from app.services.po_acceptance import is_po_accepted
    from app.services.po_tasks import derive_tasks_for_po

    po_id = str(accurate_po_id)
    po_doc = get_by_accurate_id(db, po_id)
    if po_doc is None:
        raise LookupError(f"PO tidak ditemukan: {accurate_po_id}")
    if not is_po_accepted(po_doc):
        raise PaymentPermissionError(
            "PO belum diterima Kepala Produksi — Bypass Payment ditolak."
        )

    actor = str(user.get("email_normalized") or "")
    bypass_reason = f"Bypass Payment: {cleaned}"

    from app.services.po_repair_maintenance import po_repair_writer_section

    # Fence di luar txn (pola accept_po) agar derive_tasks melihat lease.
    with po_repair_writer_section(db) as writer_fence:

        def _operation(session: Any) -> dict[str, Any]:
            writer_fence.assert_valid(db, session=session)
            current = gates.get_gates(db, po_id, session=session)
            payment = current.get("payment") or {}

            if _payment_is_active_bypass(payment):
                return {
                    **current,
                    "duplicate": True,
                    "workflow": None,
                }

            if payment.get("state") == gates.GATE_OPEN:
                raise PaymentGateConflictError(
                    "Gate payment sudah terbuka dari pembayaran terverifikasi — "
                    "tidak diubah menjadi bypass."
                )

            claimed = gates.claim_payment_bypass(
                db,
                accurate_po_id=po_id,
                reason=bypass_reason,
                actor_email=actor,
                session=session,
            )
            if claimed is None:
                # Race: pemenang lain sudah membuka gate.
                latest = gates.get_gates(db, po_id, session=session)
                if _payment_is_active_bypass(latest.get("payment")):
                    return {**latest, "duplicate": True, "workflow": None}
                raise PaymentGateConflictError(
                    "Gate payment sudah terbuka — bypass tidak diterapkan."
                )

            append_audit_event(
                db,
                action="payment.gate.bypass",
                actor_email=actor,
                target_email=None,
                detail={"accurate_po_id": po_id, "reason": cleaned},
                session=session,
            )

            workflow: dict[str, Any] | None = None
            design_state = (claimed.get("design") or {}).get("state")
            if design_state == gates.GATE_OPEN:
                live_po = get_by_accurate_id(db, po_id, session=session) or po_doc
                workflow = derive_tasks_for_po(db, live_po, session=session)

            return {
                **claimed,
                "duplicate": False,
                "workflow": workflow,
            }

        last_error: Exception | None = None
        for _ in range(8):
            try:
                return tasks.run_in_transaction(db, _operation, attempts=4)
            except PaymentGateConflictError:
                raise
            except (ConnectionFailure, OperationFailure) as exc:
                labels = getattr(exc, "has_error_label", None)
                if callable(labels) and (
                    labels("TransientTransactionError")
                    or labels("UnknownTransactionCommitResult")
                ):
                    last_error = exc
                    latest = gates.get_gates(db, po_id)
                    if _payment_is_active_bypass(latest.get("payment")):
                        return {**latest, "duplicate": True, "workflow": None}
                    continue
                raise
        raise (
            last_error
            if last_error
            else RuntimeError("Bypass Payment gagal tanpa detail.")
        )
