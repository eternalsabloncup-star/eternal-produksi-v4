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
from app.services.po_tasks import (
    attempt_sync_design_gate_for_non_print_po,
    derive_tasks_for_po,
    has_print_items,
)
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
        WriterFence,
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
    )

    writer_fence = enter_po_repair_writer_section(db)
    try:
        return _accept_po_under_writer_lease(
            db, user, accurate_po_id=accurate_po_id, writer_fence=writer_fence
        )
    finally:
        exit_po_repair_writer_section(db, writer_fence)


def _accept_po_under_writer_lease(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    writer_fence: WriterFence,
) -> dict[str, Any]:
    writer_fence.assert_valid(db)
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
        # derive standalone sudah attempt sync; pastikan field ada di respons.
        sync_info = derived.get("design_gate_sync") or attempt_sync_design_gate_for_non_print_po(
            db, po_doc, actor_email=str(user.get("email_normalized") or "")
        )
        return {
            "accurate_po_id": po_id,
            "duplicate": True,
            "acceptance": acceptance_block(po_doc),
            "workflow": derived,
            "design_gate_sync": sync_info,
            "po": po_doc,
        }

    actor = str(user.get("email_normalized") or "")
    now = datetime.now(timezone.utc)

    def _operation(session: Any) -> dict[str, Any]:
        # Fence di dalam txn: commit boundary gagal jika lease/epoch hilang.
        writer_fence.assert_valid(db, session=session)
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
    # Gate Design Non-Cetak di luar txn. Kegagalan sync TIDAK membatalkan acceptance.
    actor = str(user.get("email_normalized") or "")
    sync_info = attempt_sync_design_gate_for_non_print_po(
        db, result["po"], actor_email=actor
    )
    result["design_gate_sync"] = sync_info
    # Segarkan gates di workflow setelah sync (atau penanda Perlu Sinkronisasi).
    from app.repositories import gate_repository as gates

    po_id_out = str(result.get("accurate_po_id") or po_id)
    workflow = dict(result.get("workflow") or {})
    workflow["gates"] = gates.get_gates(db, po_id_out)
    workflow["scheduling_unlocked"] = gates.both_gates_open(db, po_id_out)
    workflow["design_gate_sync"] = sync_info
    result["workflow"] = workflow
    return result


class DesignGateSyncError(RuntimeError):
    """Gagal menyinkronkan Gate Design Non-Cetak (acceptance tetap sah)."""


def retry_design_gate_sync(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
) -> dict[str, Any]:
    """Retry idempotent: hanya memulihkan Gate Design Non-Cetak.

    Tidak accept ulang. Tidak membuka Payment Gate. Tidak menggandakan tugas.
    Fail-closed pada writer fence / permission / dependency.
    """
    from app.services.object_auth import assert_branch_access
    from app.services.po_repair_maintenance import (
        enter_po_repair_writer_section,
        exit_po_repair_writer_section,
    )

    require_accept_permission(user)
    po_id = str(accurate_po_id).strip()
    if not po_id:
        raise ValueError("accurate_po_id wajib.")

    writer_fence = enter_po_repair_writer_section(db)
    try:
        writer_fence.assert_valid(db)
        po_doc = pos.get_by_accurate_id(db, po_id)
        if po_doc is None:
            raise LookupError(f"PO tidak ditemukan: {po_id}")
        assert_branch_access(user, scope_object=po_doc)
        if not is_po_accepted(po_doc):
            raise PoAcceptanceError(
                "PO belum diterima — sync Gate Design hanya setelah Terima PO."
            )
        if has_print_items(po_doc):
            raise DesignGateSyncError(
                "PO memiliki item Cetak — Gate Design tidak di-auto-sync Non-Cetak."
            )

        actor = str(user.get("email_normalized") or "")
        sync_info = attempt_sync_design_gate_for_non_print_po(
            db, po_doc, actor_email=actor
        )
        if not sync_info.get("synced"):
            raise DesignGateSyncError(
                sync_info.get("error")
                or "Sinkronisasi Gate Design gagal — tetap Perlu Sinkronisasi."
            )

        # Derive ulang (idempotent) agar penjadwalan muncul jika payment sudah open.
        derived = derive_tasks_for_po(db, po_doc)
        from app.repositories import gate_repository as gates

        return {
            "accurate_po_id": po_id,
            "design_gate_sync": sync_info,
            "workflow": derived,
            "gates": gates.get_gates(db, po_id),
            "acceptance": acceptance_block(po_doc),
            "po": po_doc,
        }
    finally:
        exit_po_repair_writer_section(db, writer_fence)
