"""Edit identitas cabang PO — izin khusus, audit, fail-closed pada turunan."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.auth.permissions import PERM_EDIT_PO_BRANCH, has_permission
from app.repositories import org_master_repository as org
from app.repositories.po_repository import (
    get_by_accurate_id,
    set_po_branch_id,
)
from app.services.audit_log import append_audit_event
from app.services.branch_identity import normalize_branch_identity
from app.services.object_auth import actor_email, object_branch_code
from app.services.task_engine import TaskPermissionError


class PoBranchConflictError(RuntimeError):
    """Perubahan cabang tidak aman terhadap transaksi turunan."""


def _require_edit_perm(user: dict[str, Any]) -> None:
    if not has_permission(user, PERM_EDIT_PO_BRANCH):
        raise TaskPermissionError("Akses ditolak — memerlukan po.branch.edit")


def _related_conflict(db: Database, po_doc: dict[str, Any], new_id: str) -> str | None:
    """Tolak bila alokasi/task/SJ punya identitas cabang berbeda yang eksplisit."""
    accurate_id = str(po_doc.get("accurate_id") or "")
    current = object_branch_code(po_doc)

    allocs = list(
        db["production_allocations"].find(
            {"accurate_po_id": accurate_id},
            {"_id": 0, "allocation_id": 1, "branch_code": 1, "branch_id": 1},
        )
    )
    for alloc in allocs:
        ab = object_branch_code(alloc)
        if ab and ab != new_id and ab != current:
            return f"alokasi {alloc.get('allocation_id')} cabang {ab}"

    tasks = list(
        db["operational_tasks"].find(
            {"ref.accurate_po_id": accurate_id},
            {"_id": 0, "task_id": 1, "branch_code": 1, "branch_id": 1, "ref": 1},
        )
    )
    for task in tasks:
        tb = object_branch_code(task)
        if tb and tb != new_id and tb != current:
            return f"task {task.get('task_id')} cabang {tb}"

    notes = list(
        db["accurate_delivery_notes"].find(
            {"accurate_po_id": accurate_id},
            {"_id": 0, "accurate_sj_id": 1, "branch_code": 1, "branch_id": 1},
        )
    )
    for note in notes:
        nb = object_branch_code(note)
        if nb and nb != new_id and nb != current:
            return f"SJ {note.get('accurate_sj_id')} cabang {nb}"
    return None


def update_po_branch(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    branch_id: str,
    reason: str,
) -> dict[str, Any]:
    _require_edit_perm(user)
    if not (reason or "").strip():
        raise ValueError("Alasan perubahan cabang wajib.")
    target = normalize_branch_identity(branch_id)
    if not target:
        raise ValueError("branch_id wajib.")

    master = org.get_branch(db, target)
    if master is None:
        raise ValueError(f"Cabang tujuan tidak dikenal di Master: {target}")
    if not bool(master.get("is_active", False)):
        raise ValueError(f"Cabang tujuan tidak aktif: {target}")

    po = get_by_accurate_id(db, accurate_po_id)
    if po is None:
        raise LookupError("PO tidak ditemukan.")

    before = object_branch_code(po)
    if before == target:
        return {"po": po, "unchanged": True, "before": before, "after": target}

    conflict = _related_conflict(db, po, target)
    if conflict:
        raise PoBranchConflictError(
            f"Perubahan cabang ditolak — konflik turunan: {conflict}"
        )

    updated = set_po_branch_id(
        db,
        accurate_po_id,
        branch_id=target,
        expected_previous=before,
    )
    if updated is None:
        raise PoBranchConflictError(
            "Perubahan cabang gagal — dokumen berubah concurrently."
        )

    append_audit_event(
        db,
        action="po.branch.update",
        actor_email=actor_email(user),
        target_email=None,
        detail={
            "accurate_po_id": str(accurate_po_id),
            "before": {"branch_id": before},
            "after": {"branch_id": target},
            "reason": reason.strip(),
            "wrote_accurate": False,
        },
    )
    return {"po": updated, "unchanged": False, "before": before, "after": target}
