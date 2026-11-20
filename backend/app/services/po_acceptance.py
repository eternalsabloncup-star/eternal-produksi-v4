"""Gate Terima PO — Kepala Produksi mengaktifkan workflow Eternal.

PO Accurate hanya masuk Inbox sampai diterima. Penerimaan:
- permission: production-head / operations.full / super_admin
- idempotent + atomik (claim + derive tasks + audit)
- sync Accurate tidak menimpa field ``operational``
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database

from app.auth.permissions import PERM_OPERATIONS_FULL, has_permission
from app.auth.roles import is_super_admin
from app.domain.operational import normalize_operational_role
from app.repositories import po_repository as pos
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event
from app.services.po_tasks import derive_tasks_for_po, sync_design_gate_for_non_print_po
from app.services.task_engine import TaskPermissionError

ACCEPTANCE_PENDING = "pending"
ACCEPTANCE_ACCEPTED = "accepted"
INBOX_STATUS_NEW = "PO Baru"
INBOX_STATUS_ACCEPTED = "PO Diterima"


class PoAcceptanceError(RuntimeError):
    """Gagal menerima PO."""


def acceptance_block(po_doc: dict[str, Any] | None) -> dict[str, Any]:
    ops = (po_doc or {}).get("operational")
    ops = ops if isinstance(ops, dict) else {}
    block = ops.get("acceptance")
    block = block if isinstance(block, dict) else {}
    status = str(block.get("status") or ACCEPTANCE_PENDING)
    return {
        "status": status if status in (ACCEPTANCE_PENDING, ACCEPTANCE_ACCEPTED) else ACCEPTANCE_PENDING,
        "accepted_by": block.get("accepted_by"),
        "accepted_at": block.get("accepted_at"),
        "inbox_status": ops.get("inbox_status")
        or (INBOX_STATUS_ACCEPTED if status == ACCEPTANCE_ACCEPTED else INBOX_STATUS_NEW),
    }


def is_po_accepted(po_doc: dict[str, Any] | None) -> bool:
    return acceptance_block(po_doc)["status"] == ACCEPTANCE_ACCEPTED


def require_po_accepted(po_doc: dict[str, Any] | None) -> None:
    if not is_po_accepted(po_doc):
        raise PoAcceptanceError(
            "PO belum diterima Kepala Produksi — workflow produksi belum aktif."
        )


def can_accept_po(user: dict[str, Any]) -> bool:
    if is_super_admin(user.get("role")):
        return True
    if has_permission(user, PERM_OPERATIONS_FULL):
        return True
    return normalize_operational_role(user.get("operational_role")) == "production-head"


def require_accept_permission(user: dict[str, Any]) -> None:
    if not can_accept_po(user):
        raise TaskPermissionError(
            "Akses ditolak: hanya Kepala Produksi (atau operations.full) yang dapat menerima PO."
        )


def _acceptance_payload(*, actor_email: str, now: datetime) -> dict[str, Any]:
    return {
        "status": ACCEPTANCE_ACCEPTED,
        "accepted_by": actor_email,
        "accepted_at": now,
    }


def accept_po(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
) -> dict[str, Any]:
    """Terima PO → aktifkan derive tasks. Idempotent & fail-closed."""
    from app.services.po_repair_maintenance import (
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
    )

    writer_token = enter_po_repair_writer_section(db)
    try:
        return _accept_po_under_writer_lease(db, user, accurate_po_id=accurate_po_id)
    finally:
        exit_po_repair_writer_section(db, writer_token)


def _accept_po_under_writer_lease(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
) -> dict[str, Any]:
    require_accept_permission(user)
    po_id = str(accurate_po_id).strip()
    if not po_id:
        raise ValueError("accurate_po_id wajib.")

    po_doc = pos.get_by_accurate_id(db, po_id)
    if po_doc is None:
        raise LookupError(f"PO tidak ditemukan: {po_id}")

    # Branch scope wajib sebelum accept — termasuk jalur idempotent/duplicate.
    from app.services.object_auth import assert_branch_access

    assert_branch_access(user, scope_object=po_doc)

    if is_po_accepted(po_doc):
        derived = derive_tasks_for_po(db, po_doc)
        return {
            "accurate_po_id": po_id,
            "duplicate": True,
            "acceptance": acceptance_block(po_doc),
            "workflow": derived,
            "po": po_doc,
        }

    actor = str(user.get("email_normalized") or "")
    now = datetime.now(timezone.utc)

    def _operation(session: Any) -> dict[str, Any]:
        claimed = pos.po_collection(db).find_one_and_update(
            {
                "accurate_id": po_id,
                "operational.acceptance.status": {"$ne": ACCEPTANCE_ACCEPTED},
            },
            {
                "$set": {
                    "operational.acceptance": _acceptance_payload(
                        actor_email=actor, now=now
                    ),
                    "operational.inbox_status": INBOX_STATUS_ACCEPTED,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if claimed is None:
            current = pos.po_collection(db).find_one(
                {"accurate_id": po_id}, session=session
            )
            if current is None:
                raise LookupError(f"PO tidak ditemukan: {po_id}")
            if is_po_accepted(current):
                derived = derive_tasks_for_po(db, current, session=session)
                return {
                    "accurate_po_id": po_id,
                    "duplicate": True,
                    "acceptance": acceptance_block(current),
                    "workflow": derived,
                    "po": current,
                }
            raise PoAcceptanceError("Gagal mengklaim penerimaan PO.")

        derived = derive_tasks_for_po(db, claimed, session=session)
        append_audit_event(
            db,
            action="po.accept",
            actor_email=actor,
            target_email=None,
            detail={
                "accurate_po_id": po_id,
                "po_no": claimed.get("po_no"),
                "accepted_at": now.isoformat(),
                "created_task_keys": derived.get("created_task_keys") or [],
            },
            session=session,
        )
        return {
            "accurate_po_id": po_id,
            "duplicate": False,
            "acceptance": acceptance_block(claimed),
            "workflow": derived,
            "po": claimed,
        }

    from pymongo.errors import ConnectionFailure, OperationFailure

    last_error: Exception | None = None
    result: dict[str, Any] | None = None
    for _ in range(16):
        try:
            result = tasks.run_in_transaction(db, _operation, attempts=4)
            break
        except (ConnectionFailure, OperationFailure) as exc:
            # WriteConflict / TransientTransactionError — coba lagi.
            labels = getattr(exc, "has_error_label", None)
            if callable(labels) and (
                labels("TransientTransactionError") or labels("UnknownTransactionCommitResult")
            ):
                last_error = exc
                # Setelah konflik, pemenang mungkin sudah accept — idempotent.
                current = pos.get_by_accurate_id(db, po_id)
                if current is not None and is_po_accepted(current):
                    derived = derive_tasks_for_po(db, current)
                    result = {
                        "accurate_po_id": po_id,
                        "duplicate": True,
                        "acceptance": acceptance_block(current),
                        "workflow": derived,
                        "po": current,
                    }
                    break
                continue
            raise
    if result is None:
        raise last_error if last_error else PoAcceptanceError("Transaksi penerimaan gagal.")
    # Gate Design non-cetak di luar txn (set_gate punya preflight indeks).
    sync_design_gate_for_non_print_po(db, result["po"])
    return result
