"""Orkestrasi tugas operasional: izin Master + audit + ledger Qty."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.auth.permissions import (
    PERM_BYPASS_PAYMENT,
    PERM_OPERATIONS_FULL,
    has_permission,
)
from app.auth.roles import is_super_admin
from app.domain.operational import normalize_operational_role
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event


class TaskPermissionError(PermissionError):
    """Aktor tidak memiliki izin aksi pada jenis pekerjaan ini."""


def _assert_task_object_scope(
    db: Database, user: dict[str, Any], task: dict[str, Any]
) -> None:
    """Cabang sebelum konfirmasi generik — assignment metadata bukan gate."""
    from app.services.object_auth import (
        assert_branch_access,
        resolve_scope_object_for_task,
    )

    scope = resolve_scope_object_for_task(db, task)
    assert_branch_access(user, scope_object=scope)


def actor_operational_role(user: dict[str, Any]) -> str | None:
    return normalize_operational_role(user.get("operational_role"))


def can_approve_payment_bypass(db: Database, user: dict[str, Any]) -> bool:
    """Canonical Payment Bypass policy (Owner):

    Allow when effective ``operations.full`` OR ``payment.bypass`` module
    permission OR Master ``payment-bypass``/approve.

    Super Admin follows this policy via effective permissions (SA has
    ``operations.full`` through ``has_permission``) — no separate can_act
    shortcut for this sensitive action.
    """
    if has_permission(user, PERM_OPERATIONS_FULL):
        return True
    if has_permission(user, PERM_BYPASS_PAYMENT):
        return True
    role = actor_operational_role(user)
    if role is None:
        return False
    return master.role_can(
        db, role=role, task_type="payment-bypass", action="approve"
    )


def can_act(
    db: Database,
    user: dict[str, Any],
    *,
    task_type: str,
    action: str,
) -> bool:
    """Izin diperiksa backend, bukan sekadar tombol tersembunyi di UI."""
    if task_type == "payment-bypass":
        return action == "approve" and can_approve_payment_bypass(db, user)

    if is_super_admin(user.get("role")):
        return True
    # Owner: operations.full = full operational task authority.
    if has_permission(user, PERM_OPERATIONS_FULL):
        return True
    role = actor_operational_role(user)
    if role is None:
        return False
    # Read-only: Master wajib di-bootstrap eksplisit (bukan seed di request path).
    return master.role_can(db, role=role, task_type=task_type, action=action)


def require_action(
    db: Database,
    user: dict[str, Any],
    *,
    task_type: str,
    action: str,
) -> None:
    if not can_act(db, user, task_type=task_type, action=action):
        raise TaskPermissionError(
            f"Akses ditolak untuk aksi '{action}' pada pekerjaan '{task_type}'."
        )


def confirm_task(
    db: Database,
    user: dict[str, Any],
    *,
    task_key: str,
    qty: float | int,
    idempotency_key: str,
    note: str | None = None,
    extra: dict[str, Any] | None = None,
    session: Any | None = None,
    extra_reservation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = tasks.get_task(db, task_key, session=session)
    if task is None:
        raise LookupError(f"Tugas tidak ditemukan: {task_key}")

    task_type = str(task.get("task_type") or "")
    # Preliminary Loading: informatif saja — konfirmasi Qty dilarang tanpa SJ.
    if task_type == "loading-menunggu-sj":
        raise TaskPermissionError(
            "Tugas Loading menunggu Surat Jalan bersifat informatif — "
            "konfirmasi Loading hanya lewat Surat Jalan Accurate."
        )
    require_action(db, user, task_type=task_type, action="confirm")
    _assert_task_object_scope(db, user, task)

    result = tasks.confirm_task_qty(
        db,
        task_key=task_key,
        qty=qty,
        actor_email=str(user.get("email_normalized") or ""),
        actor_role=actor_operational_role(user) or str(user.get("role") or ""),
        idempotency_key=idempotency_key,
        note=note,
        extra=extra,
        session=session,
        extra_reservation=extra_reservation,
    )

    if not result.get("duplicate"):
        append_audit_event(
            db,
            action=f"task.confirm.{task_type}",
            actor_email=str(user.get("email_normalized") or ""),
            target_email=None,
            detail={
                "task_key": task_key,
                "qty": qty,
                "idempotency_key": idempotency_key,
                "ref": task.get("ref") or {},
            },
        )
    return result


class MasterRoleConflictError(ValueError):
    """assigned_role baru belum punya grant aksi yang kompatibel."""

    def __init__(self, message: str, *, conflicts: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.conflicts = conflicts or {}


def set_task_type_role(
    db: Database,
    user: dict[str, Any],
    *,
    task_type: str,
    role: str,
) -> dict[str, Any]:
    """Owner memindahkan jenis pekerjaan ke role lain lewat Master.

    Tidak otomatis menyalin/mencabut ``role_actions``. Menolak bila role baru
    belum punya minimal satu aksi relevan — Owner harus set grant terlebih dulu.
    """
    spec = master.get_task_type(db, task_type)
    if spec is None:
        raise LookupError(f"Jenis pekerjaan tidak ditemukan: {task_type}")
    relevant = list(spec.get("actions") or [])
    actionable = [a for a in relevant if a != "view"]
    check_actions = actionable or relevant
    granted = master.role_actions_collection(db).find_one(
        {"role": role, "task_type": task_type}, {"_id": 0, "actions": 1}
    )
    have = set((granted or {}).get("actions") or [])
    if check_actions and not any(a in have for a in check_actions):
        raise MasterRoleConflictError(
            f"Role '{role}' belum memiliki grant aksi untuk '{task_type}'. "
            "Set Master role-actions terlebih dahulu sebelum memindahkan assigned_role "
            "(grant tidak disalin otomatis).",
            conflicts={
                "task_type": task_type,
                "assigned_role": role,
                "required_any_of": check_actions,
                "current_actions": sorted(have),
            },
        )

    previous = spec.get("assigned_role")
    doc = master.set_task_type_role(db, task_type=task_type, role=role)
    append_audit_event(
        db,
        action="master.task_type.assign_role",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={
            "task_type": task_type,
            "assigned_role": role,
            "previous_assigned_role": previous,
            "role_actions_unchanged": True,
            "new_role_actions": sorted(have),
        },
    )
    return doc


def set_role_actions(
    db: Database,
    user: dict[str, Any],
    *,
    role: str,
    task_type: str,
    actions: list[str],
) -> dict[str, Any]:
    doc = master.set_role_actions(
        db, role=role, task_type=task_type, actions=actions
    )
    append_audit_event(
        db,
        action="master.role_actions.set",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={"role": role, "task_type": task_type, "actions": sorted(set(actions))},
    )
    return doc
