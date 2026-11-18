"""One-shot Super Admin promotion — never auto-run on deploy.

Target account is identified by email. Password hash is never modified.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from pymongo import ReturnDocument
from pymongo.database import Database

from app.auth.roles import ROLE_SUPER_ADMIN
from app.db import users_collection
from app.models.user import normalize_email
from app.services.audit_log import append_audit_event

# Owner-approved identity for Eternal V4 first Super Admin.
CANONICAL_SUPER_ADMIN_EMAIL = "prasnda21@gmail.com"
CANONICAL_SUPER_ADMIN_DISPLAY_NAME = "Dimas"

ALLOWED_PROMOTE_HOSTS = frozenset({"127.0.0.1", "localhost"})
ALLOWED_PROMOTE_PORT = 27018
ALLOWED_PROMOTE_DB = "eternal_v4"


class PromoteError(RuntimeError):
    pass


def assert_promote_target_env(*, mongodb_uri: str, mongodb_db: str) -> None:
    """Refuse wrong host/port/db — no accidental ops/prod writes."""
    db_name = (mongodb_db or "").strip()
    if db_name != ALLOWED_PROMOTE_DB:
        raise PromoteError(
            f"DB_NAME harus '{ALLOWED_PROMOTE_DB}' (diterima: {db_name!r})."
        )

    parsed = urlparse(mongodb_uri)
    host = (parsed.hostname or "").strip().lower()
    port = parsed.port
    if host not in ALLOWED_PROMOTE_HOSTS:
        raise PromoteError(
            f"Host Mongo harus 127.0.0.1/localhost (diterima: {host!r})."
        )
    if port != ALLOWED_PROMOTE_PORT:
        raise PromoteError(
            f"Port Mongo harus {ALLOWED_PROMOTE_PORT} (diterima: {port!r})."
        )


def find_exact_user(db: Database, email: str) -> dict[str, Any]:
    email_normalized = normalize_email(email)
    matches = list(
        users_collection(db).find({"email_normalized": email_normalized})
    )
    if len(matches) == 0:
        raise PromoteError(
            f"Akun {email_normalized} tidak ditemukan. Tidak membuat user baru."
        )
    if len(matches) != 1:
        raise PromoteError(
            f"Ditemukan {len(matches)} akun untuk {email_normalized}. Ditolak."
        )
    return matches[0]


def promote_to_super_admin(
    db: Database,
    *,
    email: str = CANONICAL_SUPER_ADMIN_EMAIL,
    display_name: str = CANONICAL_SUPER_ADMIN_DISPLAY_NAME,
    dry_run: bool = True,
    actor_email: str = "owner_manual_script",
) -> dict[str, Any]:
    """Promote existing user → super_admin.

    - Does not change password_hash
    - Does not create or delete users
    - Idempotent if already super_admin + matching display_name + active
    """
    user = find_exact_user(db, email)
    email_normalized = user["email_normalized"]
    before = {
        "role": user.get("role"),
        "display_name": user.get("display_name"),
        "is_active": bool(user.get("is_active", False)),
        "password_hash_fingerprint": _hash_fingerprint(user.get("password_hash")),
    }

    already = (
        user.get("role") == ROLE_SUPER_ADMIN
        and user.get("display_name") == display_name
        and bool(user.get("is_active", False))
    )

    plan = {
        "email": email_normalized,
        "dry_run": dry_run,
        "already_compliant": already,
        "before": before,
        "after": {
            "role": ROLE_SUPER_ADMIN,
            "display_name": display_name,
            "is_active": True,
            "password_hash_unchanged": True,
        },
    }

    if dry_run:
        plan["executed"] = False
        plan["message"] = "Dry-run saja — tidak menulis database."
        return plan

    password_hash_before = user.get("password_hash")
    now = datetime.now(timezone.utc)
    result = users_collection(db).find_one_and_update(
        {
            "_id": user["_id"],
            "email_normalized": email_normalized,
            # Refuse if password hash changed mid-flight (integrity).
            "password_hash": password_hash_before,
        },
        {
            "$set": {
                "role": ROLE_SUPER_ADMIN,
                "display_name": display_name,
                "is_active": True,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise PromoteError(
            "Update ditolak — akun berubah atau password_hash tidak cocok."
        )

    if result.get("password_hash") != password_hash_before:
        raise PromoteError(
            "INTEGRITAS GAGAL: password_hash berubah — tidak diizinkan."
        )

    append_audit_event(
        db,
        action="user.promote_super_admin",
        actor_email=actor_email,
        target_email=email_normalized,
        detail={
            "before_role": before["role"],
            "after_role": ROLE_SUPER_ADMIN,
            "display_name": display_name,
            "idempotent": already,
            "password_hash_unchanged": True,
        },
    )

    plan["executed"] = True
    plan["message"] = (
        "Sudah super_admin (idempotent)."
        if already
        else "Promosi super_admin berhasil."
    )
    plan["after"]["password_hash_fingerprint"] = _hash_fingerprint(
        result.get("password_hash")
    )
    return plan


def _hash_fingerprint(password_hash: str | None) -> str:
    """Non-reversible short marker for test assertions — not the hash itself."""
    if not password_hash:
        return "missing"
    return f"len:{len(password_hash)}:prefix:{password_hash[:6]}"
