"""Object-level authorization for evidence content streams.

Role ``view`` alone is not universal access. Callers must pass the
authoritative scope object (PO / allocation / SJ) already loaded by id.
"""

from __future__ import annotations

from typing import Any, Final

from pymongo.database import Database

from app.auth.permissions import (
    PERM_ACCESS_ALL_BRANCHES,
    PERM_OPERATIONS_FULL,
    has_permission,
)
from app.auth.roles import is_super_admin
from app.models.user import normalize_email
from app.services.task_engine import can_act

SCOPE_DESIGN: Final = "design"
SCOPE_SAMPLE: Final = "sample"
SCOPE_DELIVERY: Final = "delivery"


class EvidenceAccessDenied(PermissionError):
    """Aktor tidak berizin melihat bukti pada object scope ini."""

    def __init__(self, message: str = "Tidak berizin melihat bukti ini.") -> None:
        super().__init__(message)


def _actor_email(user: dict[str, Any]) -> str:
    return normalize_email(str(user.get("email_normalized") or user.get("email") or ""))


def _user_branch_ids(user: dict[str, Any]) -> set[str]:
    raw = user.get("branch_ids") or []
    out: set[str] = set()
    for item in raw:
        code = str(item or "").strip().upper()
        if code:
            out.add(code)
    return out


def _object_branch_code(obj: dict[str, Any] | None) -> str | None:
    """Cabang otoritatif bila ada; None = sumber cabang tidak terbukti pada object."""
    if not isinstance(obj, dict):
        return None
    for key in ("branch_code", "branch_id", "branch"):
        val = obj.get(key)
        if val is not None and str(val).strip():
            return str(val).strip().upper()
    name = obj.get("branch_name")
    # branch_name tanpa kode bukan identitas otoritatif untuk filter cabang.
    if name is not None and str(name).strip() and obj.get("branch_code"):
        return str(obj.get("branch_code")).strip().upper()
    return None


def _bypass_object_scope(user: dict[str, Any]) -> bool:
    if is_super_admin(user.get("role")):
        return True
    if has_permission(user, PERM_OPERATIONS_FULL):
        return True
    if has_permission(user, PERM_ACCESS_ALL_BRANCHES):
        return True
    return False


def assert_branch_scope(
    user: dict[str, Any],
    *,
    scope_object: dict[str, Any] | None,
) -> None:
    """Fail-closed untuk user terbatas cabang bila object tanpa cabang terbukti."""
    if _bypass_object_scope(user):
        return
    branches = _user_branch_ids(user)
    if not branches:
        # User tanpa daftar cabang + tanpa bypass → tidak boleh mengandalkan role view saja
        # untuk object lintas cabang; izinkan hanya jika object juga tanpa cabang
        # (legacy) DAN assignment lolos di pemanggil. Di sini: object dengan cabang → tolak.
        obj_branch = _object_branch_code(scope_object)
        if obj_branch is not None:
            raise EvidenceAccessDenied(
                "Bukti di luar cabang yang diizinkan untuk akun ini."
            )
        return
    obj_branch = _object_branch_code(scope_object)
    if obj_branch is None:
        raise EvidenceAccessDenied(
            "Scope cabang bukti tidak dapat divalidasi — akses ditolak."
        )
    if obj_branch not in branches:
        raise EvidenceAccessDenied(
            "Bukti di luar cabang yang diizinkan untuk akun ini."
        )


def assert_design_evidence_access(
    db: Database,
    user: dict[str, Any],
    *,
    po_doc: dict[str, Any],
) -> None:
    if not (
        can_act(db, user, task_type="design", action="view")
        or can_act(db, user, task_type="design", action="manage_evidence")
        or can_act(db, user, task_type="design", action="confirm")
        or _bypass_object_scope(user)
    ):
        raise EvidenceAccessDenied("Tidak berizin melihat bukti desain.")
    assert_branch_scope(user, scope_object=po_doc)


def assert_sample_evidence_access(
    db: Database,
    user: dict[str, Any],
    *,
    allocation: dict[str, Any],
    po_doc: dict[str, Any] | None = None,
) -> None:
    """Operator hanya alokasi miliknya; KP/approval/ops melihat terkait izinnya."""
    if not (
        can_act(db, user, task_type="konfirmasi-sample", action="view")
        or can_act(db, user, task_type="konfirmasi-sample", action="manage_evidence")
        or can_act(db, user, task_type="approval-sampel", action="view")
        or can_act(db, user, task_type="approval-sampel", action="approve")
        or _bypass_object_scope(user)
    ):
        raise EvidenceAccessDenied("Tidak berizin melihat bukti sampel.")

    if not _bypass_object_scope(user):
        is_approver = can_act(
            db, user, task_type="approval-sampel", action="view"
        ) or can_act(db, user, task_type="approval-sampel", action="approve")
        if not is_approver:
            assigned = normalize_email(str(allocation.get("operator_email") or ""))
            actor = _actor_email(user)
            if not assigned or not actor or assigned != actor:
                raise EvidenceAccessDenied(
                    "Bukti sampel di luar assignment operator Anda."
                )

    assert_branch_scope(user, scope_object=po_doc or allocation)


def assert_delivery_evidence_access(
    db: Database,
    user: dict[str, Any],
    *,
    note: dict[str, Any],
) -> None:
    if not (
        can_act(db, user, task_type="pengiriman", action="view")
        or can_act(db, user, task_type="pengiriman", action="confirm")
        or can_act(db, user, task_type="pengiriman", action="manage_evidence")
        or can_act(db, user, task_type="pengiriman-gagal-koordinasi", action="view")
        or can_act(db, user, task_type="pengiriman-gagal-koordinasi", action="confirm")
        or _bypass_object_scope(user)
    ):
        raise EvidenceAccessDenied("Tidak berizin melihat bukti pengiriman.")

    # Assignment driver bila field otoritatif ada; legacy tanpa driver → role+branch saja.
    if not _bypass_object_scope(user):
        driver = normalize_email(
            str(
                note.get("driver_email")
                or note.get("assigned_driver_email")
                or ""
            )
        )
        is_coord = can_act(
            db, user, task_type="pengiriman-gagal-koordinasi", action="view"
        ) or can_act(
            db, user, task_type="pengiriman-gagal-koordinasi", action="confirm"
        )
        if driver and not is_coord:
            actor = _actor_email(user)
            if not actor or actor != driver:
                raise EvidenceAccessDenied(
                    "Bukti pengiriman di luar assignment driver Anda."
                )

    assert_branch_scope(user, scope_object=note)
