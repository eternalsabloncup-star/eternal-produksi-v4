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
from app.services.object_auth import (
    ObjectAuthError,
    actor_email,
    assert_branch_access,
    bypasses_branch,
    object_branch_code,
    user_branch_ids,
)
from app.services.task_engine import TaskPermissionError


class PoBranchConflictError(RuntimeError):
    """Perubahan cabang tidak aman terhadap transaksi turunan."""


def _require_edit_perm(user: dict[str, Any]) -> None:
    if not has_permission(user, PERM_EDIT_PO_BRANCH):
        raise TaskPermissionError("Akses ditolak — memerlukan po.branch.edit")


def _assert_target_branch_access(user: dict[str, Any], target: str) -> None:
    """Tanpa lintas cabang, tujuan harus termasuk branch_ids akun."""
    if bypasses_branch(user):
        return
    allowed = user_branch_ids(user)
    if not allowed or target not in allowed:
        raise ObjectAuthError(
            "Cabang tujuan di luar izin akun ini — akses ditolak."
        )


def _related_conflict(db: Database, po_doc: dict[str, Any], new_id: str) -> str | None:
    """Tolak bila transaksi turunan sudah terbentuk (tidak ada migrate atomik).

    Tidak memindahkan data turunan diam-diam. Perubahan cabang hanya aman
    bila belum ada alokasi/task/SJ terkait.
    """
    del new_id  # target tidak dipakai — keberadaan turunan saja sudah konflik
    accurate_id = str(po_doc.get("accurate_id") or "")
    if not accurate_id:
        return "accurate_po_id kosong"

    if db["production_allocations"].count_documents(
        {"accurate_po_id": accurate_id}, limit=1
    ):
        return "alokasi produksi sudah terbentuk"
    if db["operational_tasks"].count_documents(
        {"ref.accurate_po_id": accurate_id}, limit=1
    ):
        return "task operasional sudah terbentuk"
    if db["accurate_delivery_notes"].count_documents(
        {"accurate_po_id": accurate_id}, limit=1
    ):
        return "surat jalan sudah terbentuk"
    if db["schedule_commands"].count_documents(
        {"accurate_po_id": accurate_id}, limit=1
    ):
        return "perintah jadwal sudah terbentuk"
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

    # Scope cabang asal (fail-closed) + otorisasi tujuan.
    assert_branch_access(user, scope_object=po)
    _assert_target_branch_access(user, target)

    before = object_branch_code(po)
    if before == target:
        return {"po": po, "unchanged": True, "before": before, "after": target}

    conflict = _related_conflict(db, po, target)
    if conflict:
        raise PoBranchConflictError(
            f"Perubahan cabang ditolak — konflik turunan: {conflict}. "
            "Perlu penanganan terpisah; data turunan tidak dipindahkan otomatis."
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
