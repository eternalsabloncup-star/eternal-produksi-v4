"""User management for Settings V4 — no password/hash leakage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.auth.passwords import hash_password
from app.auth.roles import ROLE_SUPER_ADMIN, is_super_admin
from app.db import users_collection
from app.domain.operational import OPERATIONAL_ROLES, normalize_operational_role
from app.models.user import normalize_email


def _oid(user_id: str) -> ObjectId:
    try:
        return ObjectId(user_id)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("user_id tidak valid") from exc


def public_user_admin(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "email": doc.get("email_normalized", ""),
        "display_name": doc.get("display_name", ""),
        "role": doc.get("role", "user"),
        "operational_role": doc.get("operational_role"),
        "branch_ids": list(doc.get("branch_ids") or []),
        "is_active": bool(doc.get("is_active", False)),
        "effective_module_permissions": list(
            doc.get("effective_module_permissions") or []
        ),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def list_users(db: Database, *, include_inactive: bool = True) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if not include_inactive:
        query["is_active"] = True
    cursor = users_collection(db).find(query).sort("email_normalized", ASCENDING)
    return [public_user_admin(d) for d in cursor]


def _is_operator_operational_role(value: Any) -> bool:
    """Role operasional Operator — slug resmi via normalize (bukan role modul Settings)."""
    return normalize_operational_role(
        str(value) if value is not None else None
    ) == "operator"


def operator_display_label(display_name: Any, email: str) -> str:
    """Nama manusiawi; fallback email agar kandidat valid tidak hilang tanpa alasan.

    Dipakai saat identitas teknis masih perlu terbaca (admin/debug).
    Untuk label operasional UI, pakai ``operational_display_name``.
    """
    name = str(display_name or "").strip()
    if name:
        return name
    return str(email or "").strip()


USER_NAME_UNAVAILABLE = "Nama belum tersedia"


def operational_display_name(display_name: Any) -> str:
    """Label aktor operasional — tanpa fallback email."""
    name = str(display_name or "").strip()
    return name if name else USER_NAME_UNAVAILABLE


def display_names_by_emails(
    db: Database, emails: list[str] | set[str]
) -> dict[str, str]:
    """Batch lookup Master display_name by email_normalized (aktif/non-aktif).

    Return map email_normalized → display_name mentah (boleh kosong).
    Tidak mengekspos daftar pengguna penuh — hanya email yang diminta.
    """
    needles: list[str] = []
    seen: set[str] = set()
    for raw in emails:
        email_n = normalize_email(str(raw or ""))
        if not email_n or email_n in seen:
            continue
        seen.add(email_n)
        needles.append(email_n)
    if not needles:
        return {}
    out: dict[str, str] = {}
    cursor = users_collection(db).find(
        {"email_normalized": {"$in": needles}},
        {"email_normalized": 1, "display_name": 1},
    )
    for doc in cursor:
        email_n = str(doc.get("email_normalized") or "").strip()
        if not email_n:
            continue
        out[email_n] = str(doc.get("display_name") or "").strip()
    return out


def list_active_operators(db: Database) -> list[dict[str, Any]]:
    """Operator aktif Master Pengguna — identifier email + label tampilan.

    Kontrak: is_active + operational_role Operator (bukan Settings role_key).
    Tidak memfilter cabang di sini — scope cabang mengikuti aturan alokasi existing.
    """
    cursor = users_collection(db).find({"is_active": True})
    items: list[dict[str, Any]] = []
    for doc in cursor:
        if not _is_operator_operational_role(doc.get("operational_role")):
            continue
        email = str(doc.get("email_normalized") or "").strip()
        if not email:
            continue
        items.append(
            {
                "id": str(doc["_id"]),
                "email": email,
                "display_name": operator_display_label(doc.get("display_name"), email),
            }
        )
    items.sort(key=lambda row: str(row["display_name"]).lower())
    return items


def find_user_by_email(db: Database, email: str) -> dict[str, Any] | None:
    """Lookup Master Pengguna by email (aktif atau tidak)."""
    email_n = normalize_email(email)
    if not email_n:
        return None
    return users_collection(db).find_one({"email_normalized": email_n})


def find_active_operator_by_email(db: Database, email: str) -> dict[str, Any] | None:
    doc = find_user_by_email(db, email)
    if doc is None:
        return None
    if not bool(doc.get("is_active", False)):
        return None
    if not _is_operator_operational_role(doc.get("operational_role")):
        return None
    email_n = str(doc.get("email_normalized") or "").strip()
    return {
        "id": str(doc["_id"]),
        "email": email_n,
        "display_name": operator_display_label(doc.get("display_name"), email_n),
    }


def get_user(db: Database, user_id: str) -> dict[str, Any] | None:
    doc = users_collection(db).find_one({"_id": _oid(user_id)})
    return public_user_admin(doc) if doc else None


def get_user_raw(db: Database, user_id: str) -> dict[str, Any] | None:
    return users_collection(db).find_one({"_id": _oid(user_id)})


def count_users_with_role(db: Database, role_key: str, *, active_only: bool = True) -> int:
    query: dict[str, Any] = {"role": role_key}
    if active_only:
        query["is_active"] = True
    return users_collection(db).count_documents(query)


def create_user(
    db: Database,
    *,
    email: str,
    display_name: str,
    password: str,
    role: str,
    operational_role: str | None,
    branch_ids: list[str] | None,
    effective_module_permissions: list[str],
) -> dict[str, Any]:
    if not email.strip() or not display_name.strip():
        raise ValueError("Email dan nama tampilan wajib.")
    if len(password) < 12:
        raise ValueError("Kata sandi minimal 12 karakter.")
    if is_super_admin(role):
        raise ValueError("Tidak boleh membuat Super Admin lewat Settings API.")
    op = None
    if operational_role:
        op = normalize_operational_role(operational_role)
        if op is None:
            raise ValueError(f"operational_role tidak dikenal: {operational_role}")
        if op not in OPERATIONAL_ROLES:
            raise ValueError(f"operational_role tidak dikenal: {operational_role}")
    now = datetime.now(timezone.utc)
    doc = {
        "email_normalized": normalize_email(email),
        "display_name": display_name.strip(),
        "password_hash": hash_password(password),
        "role": role,
        "operational_role": op,
        "branch_ids": list(branch_ids or []),
        "effective_module_permissions": sorted(set(effective_module_permissions)),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = users_collection(db).insert_one(doc)
    except DuplicateKeyError as exc:
        raise RuntimeError("Email sudah terpakai.") from exc
    doc["_id"] = result.inserted_id
    return public_user_admin(doc)


def update_user(
    db: Database,
    user_id: str,
    *,
    display_name: str | None = None,
    role: str | None = None,
    operational_role: str | None | object = ...,
    branch_ids: list[str] | None = None,
    effective_module_permissions: list[str] | None = None,
) -> dict[str, Any]:
    raw = get_user_raw(db, user_id)
    if raw is None:
        raise LookupError("Pengguna tidak ditemukan.")
    updates: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    if display_name is not None:
        if not display_name.strip():
            raise ValueError("Nama tampilan wajib.")
        updates["display_name"] = display_name.strip()
    if role is not None:
        if is_super_admin(role):
            raise ValueError("Tidak boleh menetapkan Super Admin lewat Settings API.")
        if is_super_admin(raw.get("role")):
            raise ValueError("Tidak boleh mengubah Super Admin lewat Settings API.")
        updates["role"] = role
    if operational_role is not ...:
        if operational_role is None or operational_role == "":
            updates["operational_role"] = None
        else:
            op = normalize_operational_role(str(operational_role))
            if op is None or op not in OPERATIONAL_ROLES:
                raise ValueError(f"operational_role tidak dikenal: {operational_role}")
            updates["operational_role"] = op
    if branch_ids is not None:
        updates["branch_ids"] = list(branch_ids)
    if effective_module_permissions is not None:
        updates["effective_module_permissions"] = sorted(
            set(effective_module_permissions)
        )
    doc = users_collection(db).find_one_and_update(
        {"_id": raw["_id"]},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    assert doc is not None
    return public_user_admin(doc)


def set_active(db: Database, user_id: str, *, is_active: bool) -> dict[str, Any]:
    raw = get_user_raw(db, user_id)
    if raw is None:
        raise LookupError("Pengguna tidak ditemukan.")
    if is_super_admin(raw.get("role")) and not is_active:
        remaining = users_collection(db).count_documents(
            {"role": ROLE_SUPER_ADMIN, "is_active": True}
        )
        if remaining <= 1:
            raise ValueError(
                "Tidak boleh menonaktifkan Super Admin terakhir — mencegah penguncian akses."
            )
    doc = users_collection(db).find_one_and_update(
        {"_id": raw["_id"]},
        {
            "$set": {
                "is_active": is_active,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    assert doc is not None
    return public_user_admin(doc)


def reset_password(db: Database, user_id: str, *, new_password: str) -> None:
    if len(new_password) < 12:
        raise ValueError("Kata sandi minimal 12 karakter.")
    raw = get_user_raw(db, user_id)
    if raw is None:
        raise LookupError("Pengguna tidak ditemukan.")
    users_collection(db).update_one(
        {"_id": raw["_id"]},
        {
            "$set": {
                "password_hash": hash_password(new_password),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


def refresh_effective_permissions_for_role(
    db: Database, role_key: str, permissions: list[str]
) -> int:
    """Denormalize module grants onto users holding this role."""
    res = users_collection(db).update_many(
        {"role": role_key},
        {
            "$set": {
                "effective_module_permissions": sorted(set(permissions)),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return int(res.modified_count)
