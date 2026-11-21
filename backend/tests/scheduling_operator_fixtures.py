"""Seed/upsert Operator Master untuk tes alokasi — bukan production data."""

from __future__ import annotations

from pymongo.database import Database

from app.auth.passwords import hash_password
from app.models.user import normalize_email

# Email yang dipakai suite lama sebagai identifier alokasi bebas.
DEFAULT_SCHEDULING_OPERATOR_EMAILS: frozenset[str] = frozenset(
    {
        "op1@eternal.local",
        "op2@eternal.local",
        "op@eternal.local",
        "op-a@eternal.local",
        "op-b@eternal.local",
        "opa@eternal.local",
        "opb@eternal.local",
        "operator@eternal.local",
        "op.warna@eternal.local",
    }
)

_PASSWORD = "password-kuat-123"


def upsert_test_user(
    db: Database,
    email: str,
    *,
    operational_role: str | None,
    display_name: str | None = None,
    is_active: bool = True,
    role: str = "user",
    password: str = _PASSWORD,
    effective_module_permissions: list[str] | None = None,
    branch_ids: list[str] | None = None,
) -> None:
    """Idempotent user upsert untuk fixture tes."""
    email_n = normalize_email(email)
    if not email_n:
        raise ValueError("email uji tidak valid")
    perms = effective_module_permissions or ["access.all_branches"]
    if display_name is None:
        stored_name = email_n.split("@")[0]
    else:
        stored_name = str(display_name).strip()
    db["users"].update_one(
        {"email_normalized": email_n},
        {
            "$set": {
                "display_name": stored_name,
                "password_hash": hash_password(password),
                "role": role,
                "operational_role": operational_role,
                "is_active": is_active,
                "effective_module_permissions": list(perms),
                "branch_ids": list(branch_ids or []),
            }
        },
        upsert=True,
    )


def ensure_active_operator(
    db: Database,
    email: str,
    *,
    display_name: str | None = None,
) -> None:
    upsert_test_user(
        db,
        email,
        operational_role="operator",
        display_name=display_name,
        is_active=True,
    )


def ensure_default_scheduling_operators(db: Database) -> None:
    for email in sorted(DEFAULT_SCHEDULING_OPERATOR_EMAILS):
        ensure_active_operator(db, email)


def ensure_operators_for_allocations(
    db: Database, allocations: list[dict[str, object]]
) -> None:
    """Seed Operator hanya untuk email suite default yang belum ada di Master.

    Tidak menimpa user yang sudah ada (helper/nonaktif tetap gagal kontrak).
    Tidak membuat email asing (ghost) — create harus REJECT.
    """
    from app.repositories import settings_user_repository as users

    for entry in allocations:
        email = str(entry.get("operator_email") or "").strip()
        if not email:
            continue
        email_n = normalize_email(email)
        if email_n not in DEFAULT_SCHEDULING_OPERATOR_EMAILS:
            continue
        if users.find_user_by_email(db, email_n) is not None:
            continue
        ensure_active_operator(db, email_n)
