"""Object-level authorization — satu kontrak untuk mutasi & evidence.

Owner contract (PR #18):
- Master Permission (task action) is the authority for actions.
- ``operations.full`` = full operational access across all branches;
  bypasses operational assignment restrictions; does NOT grant user/role/
  settings admin. Payment bypass: YES (operations.full OR explicit grant).
- ``access.all_branches`` = cross-branch only; still needs action grants.
- Assignment metadata (operator_email / assigned_driver_email) is NOT an
  authorization gate — audit records the actual actor.
- Empty ``branch_ids`` = DENIED for branch-scoped resources unless elevated.
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


class ObjectAuthError(PermissionError):
    """Aktor tidak berizin pada cakupan objek (cabang)."""

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
        text = str(branch).strip()
        if len(text) <= 16 and " " not in text:
            return text.upper()
    return None


def has_operations_full(user: dict[str, Any]) -> bool:
    """Effective ``operations.full`` from session user — never trust client body."""
    return has_permission(user, PERM_OPERATIONS_FULL)


def bypasses_assignment(user: dict[str, Any]) -> bool:
    """Assignment metadata is not an auth gate; kept for elevated evidence paths."""
    if is_super_admin(user.get("role")):
        return True
    return has_operations_full(user)


def bypasses_branch(user: dict[str, Any]) -> bool:
    """Super Admin, operations.full, or access.all_branches."""
    if is_super_admin(user.get("role")):
        return True
    if has_operations_full(user):
        return True
    return has_permission(user, PERM_ACCESS_ALL_BRANCHES)


def assert_branch_access(
    user: dict[str, Any],
    *,
    scope_object: dict[str, Any] | None,
) -> None:
    """Fail-closed for limited accounts; elevated ops skip branch enumeration."""
    if bypasses_branch(user):
        return
    branches = user_branch_ids(user)
    obj_branch = object_branch_code(scope_object)
    if not branches:
        raise ObjectAuthError(
            "Akun tanpa scope cabang yang valid — akses ditolak."
        )
    if obj_branch is None:
        raise ObjectAuthError(
            "Scope cabang objek tidak dapat divalidasi — akses ditolak."
        )
    if obj_branch not in branches:
        raise ObjectAuthError(
            "Objek di luar cabang yang diizinkan untuk akun ini."
        )


def authoritative_driver_email(note: dict[str, Any] | None) -> str:
    """Historical metadata only — not an authorization gate."""
    if not isinstance(note, dict):
        return ""
    return normalize_email(
        str(note.get("assigned_driver_email") or note.get("driver_email") or "")
    )


def assert_operator_assignment(
    db: Database,
    user: dict[str, Any],
    *,
    allocation: dict[str, Any],
    allow_approver: bool = False,
) -> None:
    """No-op: Owner contract — task action + branch authorize; not operator_email."""
    _ = (db, user, allocation, allow_approver)
    return


def assert_driver_assignment(
    db: Database,
    user: dict[str, Any],
    *,
    note: dict[str, Any],
    allow_coordinator: bool = False,
) -> None:
    """No-op: Owner contract — no mandatory/exclusive courier assignment."""
    _ = (db, user, note, allow_coordinator)
    return


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
    """Filter by branch only — assignment metadata does not hide permitted tasks."""
    if is_super_admin(user.get("role")) or has_operations_full(user):
        return items
    from app.repositories import delivery_repository as deliveries
    from app.repositories.po_repository import get_by_accurate_id

    out: list[dict[str, Any]] = []
    all_branches = bypasses_branch(user)

    for task in items:
        if not all_branches:
            ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
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
        if po_doc and object_branch_code(po_doc):
            return po_doc
        if alloc and object_branch_code(alloc):
            return alloc
        return po_doc or alloc
    return po_doc
