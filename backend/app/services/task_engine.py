"""Orkestrasi tugas operasional: izin Master + audit + ledger Qty."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.auth.roles import is_super_admin
from app.domain.operational import normalize_operational_role
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event


class TaskPermissionError(PermissionError):
    """Aktor tidak memiliki izin aksi pada jenis pekerjaan ini."""


def actor_operational_role(user: dict[str, Any]) -> str | None:
    return normalize_operational_role(user.get("operational_role"))


def can_act(
    db: Database,
    user: dict[str, Any],
    *,
    task_type: str,
    action: str,
) -> bool:
    """Izin diperiksa backend, bukan sekadar tombol tersembunyi di UI."""
    if is_super_admin(user.get("role")):
        return True
    role = actor_operational_role(user)
    if role is None:
        return False
    # Default terkunci Owner harus tersedia sebelum izin dievaluasi.
    master.seed_master_defaults(db)
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
) -> dict[str, Any]:
    task = tasks.get_task(db, task_key)
    if task is None:
        raise LookupError(f"Tugas tidak ditemukan: {task_key}")

    task_type = str(task.get("task_type") or "")
    require_action(db, user, task_type=task_type, action="confirm")

    result = tasks.confirm_task_qty(
        db,
        task_key=task_key,
        qty=qty,
        actor_email=str(user.get("email_normalized") or ""),
        actor_role=actor_operational_role(user) or str(user.get("role") or ""),
        idempotency_key=idempotency_key,
        note=note,
        extra=extra,
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


def set_task_type_role(
    db: Database,
    user: dict[str, Any],
    *,
    task_type: str,
    role: str,
) -> dict[str, Any]:
    """Owner memindahkan jenis pekerjaan ke role lain lewat Master."""
    doc = master.set_task_type_role(db, task_type=task_type, role=role)
    append_audit_event(
        db,
        action="master.task_type.assign_role",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={"task_type": task_type, "assigned_role": role},
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
