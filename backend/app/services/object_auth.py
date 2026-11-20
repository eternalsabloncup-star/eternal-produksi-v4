"""Object-level authorization — satu kontrak untuk mutasi & evidence.

Elevated access (eksplisit):
- Super Admin: bypass branch + assignment (tetap ikut gate/bisnis).
- access.all_branches: bypass branch saja — bukan action/assignment.
- operations.full: modul operasi (accept PO, sync, dll.) — BUKAN bypass
  assignment/cabang pada objek tugas.

Akun terbatas: fail-closed bila cabang objek tidak terbukti atau assignment
otoritatif kosong/tidak cocok.
"""

from __future__ import annotations

from typing import Any, Final

from pymongo.database import Database

from app.auth.permissions import PERM_ACCESS_ALL_BRANCHES, has_permission
from app.auth.roles import is_super_admin
from app.models.user import normalize_email
from app.services.task_engine import can_act


class ObjectAuthError(PermissionError):
    """Aktor tidak berizin pada cakupan objek (cabang/assignment)."""

    def __init__(self, message: str = "Akses objek ditolak.") -> None:
        super().__init__(message)


# Alias agar evidence_access dapat memakai tipe yang sama.
ObjectAccessDenied = ObjectAuthError


def actor_email(user: dict[str, Any]) -> str:
    return normalize_email(str(user.get("email_normalized") or user.get("email") or ""))


def user_branch_ids(user: dict[str, Any]) -> set[str]:
    raw = user.get("branch_ids") or []
    out: set[str] = set()
    for item in raw:
        code = str(item or "").strip().upper()
        if code:
            out.add(code)
    return out


def object_branch_code(obj: dict[str, Any] | None) -> str | None:
    """Cabang otoritatif; branch_name saja tidak cukup."""
    if not isinstance(obj, dict):
        return None
    for key in ("branch_code", "branch_id"):
        val = obj.get(key)
        if val is not None and str(val).strip():
            return str(val).strip().upper()
    branch = obj.get("branch")
    if isinstance(branch, dict):
        code = branch.get("code") or branch.get("id") or branch.get("no")
        if code is not None and str(code).strip():
            return str(code).strip().upper()
    elif branch is not None and str(branch).strip() and not str(branch).strip().startswith("{"):
        # String kode cabang — bukan nama bebas.
        text = str(branch).strip()
        if len(text) <= 16 and " " not in text:
            return text.upper()
    return None


def bypasses_assignment(user: dict[str, Any]) -> bool:
    """Hanya Super Admin yang melewati pembatasan assignment objek."""
    return is_super_admin(user.get("role"))


def bypasses_branch(user: dict[str, Any]) -> bool:
    """Super Admin atau grant access.all_branches — bukan operations.full."""
    if is_super_admin(user.get("role")):
        return True
    return has_permission(user, PERM_ACCESS_ALL_BRANCHES)


def assert_branch_access(
    user: dict[str, Any],
    *,
    scope_object: dict[str, Any] | None,
) -> None:
    if bypasses_branch(user):
        return
    branches = user_branch_ids(user)
    obj_branch = object_branch_code(scope_object)
    if not branches:
        # Tanpa daftar cabang yang valid: tolak objek ber-cabang; legacy
        # pasangan tanpa cabang diizinkan hanya jika assignment lolos di pemanggil.
        if obj_branch is not None:
            raise ObjectAuthError(
                "Objek di luar cabang yang diizinkan untuk akun ini."
            )
        return
    if obj_branch is None:
        raise ObjectAuthError(
            "Scope cabang objek tidak dapat divalidasi — akses ditolak."
        )
    if obj_branch not in branches:
        raise ObjectAuthError(
            "Objek di luar cabang yang diizinkan untuk akun ini."
        )


def assert_operator_assignment(
    db: Database,
    user: dict[str, Any],
    *,
    allocation: dict[str, Any],
    allow_approver: bool = False,
) -> None:
    """Operator hanya alokasi miliknya; approver KP boleh melihat/approve."""
    if bypasses_assignment(user):
        return
    if allow_approver and (
        can_act(db, user, task_type="approval-sampel", action="view")
        or can_act(db, user, task_type="approval-sampel", action="approve")
    ):
        return
    # production-head dengan penjadwalan boleh mengelola alokasi (bukan cetak/sample).
    assigned = normalize_email(str(allocation.get("operator_email") or ""))
    actor = actor_email(user)
    if not assigned:
        raise ObjectAuthError(
            "Alokasi tanpa assignment operator otoritatif — akses ditolak."
        )
    if not actor or actor != assigned:
        raise ObjectAuthError(
            "Alokasi di luar assignment operator Anda."
        )


def assert_driver_assignment(
    db: Database,
    user: dict[str, Any],
    *,
    note: dict[str, Any],
    allow_coordinator: bool = False,
) -> None:
    if bypasses_assignment(user):
        return
    if allow_coordinator and (
        can_act(db, user, task_type="pengiriman-gagal-koordinasi", action="view")
        or can_act(db, user, task_type="pengiriman-gagal-koordinasi", action="confirm")
        or can_act(db, user, task_type="qty-loading-sj-berbeda", action="view")
        or can_act(db, user, task_type="qty-loading-sj-berbeda", action="confirm")
    ):
        return
    driver = normalize_email(
        str(
            note.get("assigned_driver_email")
            or note.get("driver_email")
            or ""
        )
    )
    actor = actor_email(user)
    if not driver:
        raise ObjectAuthError(
            "Surat Jalan tanpa assignment kurir otoritatif — akses ditolak."
        )
    if not actor or actor != driver:
        raise ObjectAuthError(
            "Surat Jalan di luar assignment kurir Anda."
        )


ALLOCATION_SCOPED_TASKS: Final[frozenset[str]] = frozenset(
    {
        "screen",
        "konfirmasi-sample",
        "approval-sampel",
        "cetak",
    }
)
DELIVERY_SCOPED_TASKS: Final[frozenset[str]] = frozenset(
    {
        "pengiriman",
        "qty-loading-sj-berbeda",
        "pengiriman-gagal-koordinasi",
    }
)


def filter_tasks_for_actor(
    db: Database,
    user: dict[str, Any],
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Saring daftar tugas sesuai assignment/cabang — Super Admin melihat semua."""
    if is_super_admin(user.get("role")):
        return items
    from app.repositories import schedule_repository as schedules
    from app.repositories.po_repository import get_by_accurate_id
    from app.repositories import delivery_repository as deliveries

    out: list[dict[str, Any]] = []
    actor = actor_email(user)
    all_branches = bypasses_branch(user)
    user_branches = user_branch_ids(user)

    for task in items:
        tt = str(task.get("task_type") or "")
        ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
        ctx = task.get("context") if isinstance(task.get("context"), dict) else {}

        # Branch filter via PO / SJ when codes exist.
        if not all_branches:
            scope_obj: dict[str, Any] | None = None
            sj_id = ref.get("accurate_delivery_note_id")
            po_id = ref.get("accurate_po_id")
            if sj_id:
                scope_obj = deliveries.get_delivery_note(db, str(sj_id))
            elif po_id:
                scope_obj = get_by_accurate_id(db, str(po_id))
            try:
                assert_branch_access(user, scope_object=scope_obj)
            except ObjectAuthError:
                continue

        if tt in ALLOCATION_SCOPED_TASKS and not bypasses_assignment(user):
            # Approver melihat approval-sampel semua cabang yang diizinkan.
            if tt == "approval-sampel" and (
                can_act(db, user, task_type="approval-sampel", action="view")
                or can_act(db, user, task_type="approval-sampel", action="approve")
            ):
                out.append(task)
                continue
            alloc_id = ref.get("allocation_id")
            op = normalize_email(str(ctx.get("operator_email") or ""))
            if alloc_id and not op:
                alloc = schedules.get_allocation(db, str(alloc_id)) or {}
                op = normalize_email(str(alloc.get("operator_email") or ""))
            if not op or op != actor:
                continue

        if tt == "pengiriman" and not bypasses_assignment(user):
            drv = normalize_email(
                str(ctx.get("assigned_driver_email") or ctx.get("driver_email") or "")
            )
            if not drv:
                sj_id = ref.get("accurate_delivery_note_id")
                note = (
                    deliveries.get_delivery_note(db, str(sj_id)) if sj_id else None
                ) or {}
                drv = normalize_email(
                    str(
                        note.get("assigned_driver_email")
                        or note.get("driver_email")
                        or ""
                    )
                )
            if not drv or drv != actor:
                continue

        out.append(task)
    return out


def resolve_scope_object_for_task(
    db: Database, task: dict[str, Any]
) -> dict[str, Any] | None:
    """Objek otoritatif untuk cek cabang — utamakan PO/SJ ber-branch_code."""
    from app.repositories import delivery_repository as deliveries
    from app.repositories import schedule_repository as schedules
    from app.repositories.po_repository import get_by_accurate_id

    ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
    tt = str(task.get("task_type") or "")
    if tt in DELIVERY_SCOPED_TASKS:
        sj_id = ref.get("accurate_delivery_note_id")
        return deliveries.get_delivery_note(db, str(sj_id)) if sj_id else None
    po_id = ref.get("accurate_po_id")
    po_doc = get_by_accurate_id(db, str(po_id)) if po_id else None
    if tt in ALLOCATION_SCOPED_TASKS:
        alloc_id = ref.get("allocation_id")
        alloc = schedules.get_allocation(db, str(alloc_id)) if alloc_id else None
        # Preferensi: PO (sumber cabang) → alokasi bertema branch_code → alloc mentah.
        if po_doc and object_branch_code(po_doc):
            return po_doc
        if alloc and object_branch_code(alloc):
            return alloc
        return po_doc or alloc
    return po_doc
