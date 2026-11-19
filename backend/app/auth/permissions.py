"""Permission checks — backend enforcement (not UI-only).

Super Admin bypasses ordinary permission and branch filters but still
requires authentication, CSRF (where applicable), data validation, and
audit logging. Super Admin must not erase audit history or skip
integrity checks.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import Depends, HTTPException, status

from app.auth.deps import AuthContext, require_session
from app.auth.roles import ROLE_ADMIN, ROLE_SUPER_ADMIN, is_super_admin, normalize_role

# Named permissions used by V4 modules. Super Admin always grants all.
PERM_ACCESS_ALL_MODULES: Final = "access.all_modules"
PERM_ACCESS_ALL_BRANCHES: Final = "access.all_branches"
PERM_MANAGE_USERS: Final = "users.manage"
PERM_MANAGE_ROLES: Final = "roles.manage"
PERM_MANAGE_SETTINGS: Final = "settings.manage"
PERM_VIEW_REPORTS: Final = "reports.view"
PERM_VIEW_AUDIT_LOG: Final = "audit.view"
PERM_OPERATIONS_FULL: Final = "operations.full"
# Bypass Payment tidak terikat role tertentu — diberikan lewat Master.
PERM_BYPASS_PAYMENT: Final = "payment.bypass"

ALL_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        PERM_ACCESS_ALL_MODULES,
        PERM_ACCESS_ALL_BRANCHES,
        PERM_MANAGE_USERS,
        PERM_MANAGE_ROLES,
        PERM_MANAGE_SETTINGS,
        PERM_VIEW_REPORTS,
        PERM_VIEW_AUDIT_LOG,
        PERM_OPERATIONS_FULL,
        PERM_BYPASS_PAYMENT,
    }
)

# Baseline grants for non-super roles (extensible). Admin gets ops + reports;
# user gets none of the privileged set by default.
_ROLE_GRANTS: dict[str, frozenset[str]] = {
    ROLE_SUPER_ADMIN: ALL_PERMISSIONS,
    ROLE_ADMIN: frozenset(
        {
            PERM_OPERATIONS_FULL,
            PERM_VIEW_REPORTS,
            PERM_ACCESS_ALL_BRANCHES,
        }
    ),
}


def permissions_for_role(role: str | None) -> frozenset[str]:
    normalized = normalize_role(role)
    if is_super_admin(normalized):
        return ALL_PERMISSIONS
    return _ROLE_GRANTS.get(normalized, frozenset())


def has_permission(user_or_role: dict[str, Any] | str | None, permission: str) -> bool:
    if isinstance(user_or_role, dict):
        role = user_or_role.get("role")
    else:
        role = user_or_role
    if is_super_admin(role):
        return True
    return permission in permissions_for_role(role)


def branch_filter_applies(user: dict[str, Any]) -> bool:
    """Super Admin is never limited by branch filter."""
    return not is_super_admin(user.get("role"))


def require_permission(permission: str):
    """FastAPI dependency factory — 403 if actor lacks permission."""

    def _dep(auth: AuthContext = Depends(require_session)) -> AuthContext:
        if not has_permission(auth.user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Akses ditolak",
            )
        return auth

    return _dep
