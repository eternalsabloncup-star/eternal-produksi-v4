"""Guards for user/role mutations — prevent privilege escalation & lockout."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.auth.roles import (
    ROLE_SUPER_ADMIN,
    is_bootstrap_operator,
    is_super_admin,
    normalize_role,
)
from app.db import users_collection


class UserGuardError(ValueError):
    """Raised when a role/user mutation is refused."""


def count_active_super_admins(db: Database) -> int:
    return users_collection(db).count_documents(
        {"role": ROLE_SUPER_ADMIN, "is_active": True}
    )


def assert_actor_can_change_role(
    actor: dict[str, Any],
    target: dict[str, Any],
    new_role: str,
) -> None:
    """Ordinary roles cannot promote themselves or alter Super Admin."""
    actor_role = normalize_role(actor.get("role"))
    target_role = normalize_role(target.get("role"))
    desired = normalize_role(new_role)

    if not is_super_admin(actor_role):
        if desired == ROLE_SUPER_ADMIN:
            raise UserGuardError(
                "Role biasa tidak boleh mempromosikan akun menjadi super_admin."
            )
        if is_super_admin(target_role):
            raise UserGuardError(
                "Role biasa tidak boleh mengubah, menonaktifkan, atau menghapus Super Admin."
            )
        # Non-super cannot change roles of other bootstrap operators either.
        if is_bootstrap_operator(target_role) and actor.get("_id") != target.get("_id"):
            raise UserGuardError(
                "Role biasa tidak boleh mengubah role operator bootstrap."
            )


def assert_actor_can_deactivate(
    actor: dict[str, Any],
    target: dict[str, Any],
    *,
    db: Database,
) -> None:
    actor_role = normalize_role(actor.get("role"))
    target_role = normalize_role(target.get("role"))

    if not is_super_admin(actor_role) and is_super_admin(target_role):
        raise UserGuardError(
            "Role biasa tidak boleh menonaktifkan Super Admin."
        )

    if is_super_admin(target_role) and target.get("is_active", False):
        # Demoting/deactivating last active Super Admin is forbidden.
        remaining = count_active_super_admins(db)
        if remaining <= 1:
            raise UserGuardError(
                "Tidak boleh menonaktifkan Super Admin terakhir — "
                "mencegah penguncian akses sistem."
            )


def assert_can_demote_super_admin(
    db: Database,
    target: dict[str, Any],
    new_role: str,
) -> None:
    if not is_super_admin(target.get("role")):
        return
    if normalize_role(new_role) == ROLE_SUPER_ADMIN:
        return
    if count_active_super_admins(db) <= 1 and target.get("is_active", False):
        raise UserGuardError(
            "Tidak boleh menurunkan Super Admin terakhir — "
            "mencegah penguncian akses sistem."
        )
