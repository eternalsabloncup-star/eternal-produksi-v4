"""System roles — Eternal V4.

``super_admin`` is the highest privilege. Legacy ``admin`` remains valid
for compatibility with existing operators.
"""

from __future__ import annotations

from typing import Final

ROLE_SUPER_ADMIN: Final = "super_admin"
ROLE_ADMIN: Final = "admin"
ROLE_USER: Final = "user"
# Canonical Master role_key for Holding (custom catalog) — bukan label/email.
ROLE_HOLDING: Final = "holding"

# Roles that count as "bootstrap operator already exists".
BOOTSTRAP_OPERATOR_ROLES: Final[frozenset[str]] = frozenset(
    {ROLE_SUPER_ADMIN, ROLE_ADMIN}
)

ALL_ROLES: Final[frozenset[str]] = frozenset(
    {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER}
)


def normalize_role(role: str | None) -> str:
    raw = (role or "").strip().lower()
    if raw in ALL_ROLES:
        return raw
    return ROLE_USER


def role_key(role: str | None) -> str:
    """Raw role_key dari session/Master — jangan collapse custom (mis. holding)."""
    return (role or "").strip().lower()


def is_super_admin(role: str | None) -> bool:
    return normalize_role(role) == ROLE_SUPER_ADMIN


def is_holding_role(role: str | None) -> bool:
    """Holding resmi = role_key Master ``holding`` pada akun terautentikasi."""
    return role_key(role) == ROLE_HOLDING


def is_bootstrap_operator(role: str | None) -> bool:
    return normalize_role(role) in BOOTSTRAP_OPERATOR_ROLES
