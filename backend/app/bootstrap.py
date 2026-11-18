"""Manual MongoDB bootstrap helpers (indexes + first Super Admin).

Never invoked by API startup or deploy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.auth.passwords import hash_password
from app.auth.roles import (
    BOOTSTRAP_OPERATOR_ROLES,
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    normalize_role,
)
from app.db import ensure_indexes, users_collection
from app.models.user import normalize_email
from app.services.audit_log import append_audit_event


def init_indexes(db: Database) -> dict[str, list[str]]:
    return ensure_indexes(db)


def admin_exists(db: Database) -> bool:
    """True if a bootstrap operator (super_admin or legacy admin) exists."""
    return (
        users_collection(db).find_one(
            {"role": {"$in": list(BOOTSTRAP_OPERATOR_ROLES)}}
        )
        is not None
    )


def create_admin_user(
    db: Database,
    *,
    email: str,
    display_name: str,
    password: str,
    allow_if_admin_exists: bool = False,
    role: str | None = None,
) -> dict[str, Any]:
    """Create bootstrap user. Default role for first account: ``super_admin``.

    Existing callers that seed test ``admin`` users should pass ``role="admin"``.
    """
    if not email or not email.strip():
        raise ValueError("Email wajib diisi")
    if not display_name or not display_name.strip():
        raise ValueError("Nama tampilan wajib diisi")
    if not password or len(password) < 12:
        raise ValueError("Kata sandi minimal 12 karakter")

    if admin_exists(db) and not allow_if_admin_exists:
        raise RuntimeError(
            "Operator bootstrap (super_admin/admin) sudah ada. "
            "Penolakan otomatis untuk mencegah overwrite. "
            "Gunakan prosedur khusus Owner jika memang diperlukan."
        )

    desired = normalize_role(role) if role else ROLE_SUPER_ADMIN
    # First account without explicit role must be Super Admin.
    if role is None:
        desired = ROLE_SUPER_ADMIN
    elif desired not in {ROLE_SUPER_ADMIN, ROLE_ADMIN}:
        raise ValueError(f"Role bootstrap tidak didukung: {desired}")

    now = datetime.now(timezone.utc)
    email_normalized = normalize_email(email)
    existing = users_collection(db).find_one({"email_normalized": email_normalized})
    if existing:
        raise RuntimeError(
            f"User dengan email {email_normalized} sudah ada. Tidak menimpa."
        )

    doc = {
        "email_normalized": email_normalized,
        "display_name": display_name.strip(),
        "password_hash": hash_password(password),
        "role": desired,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = users_collection(db).insert_one(doc)
    except DuplicateKeyError as exc:
        raise RuntimeError(
            f"User dengan email {email_normalized} sudah ada. Tidak menimpa."
        ) from exc

    append_audit_event(
        db,
        action="user.bootstrap_create",
        actor_email="bootstrap",
        target_email=email_normalized,
        detail={"role": desired, "display_name": doc["display_name"]},
    )

    return {
        "id": str(result.inserted_id),
        "email_normalized": email_normalized,
        "display_name": doc["display_name"],
        "role": desired,
    }
