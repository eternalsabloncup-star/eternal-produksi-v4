"""Custom & system role catalog for Settings V4.

System roles are immutable keys. Custom roles default-deny module permissions.
Request path: require indexes (no createIndex). Bootstrap via ensure_indexes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

from app.auth.permissions import ALL_PERMISSIONS
from app.auth.roles import ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_USER

ROLES_COLLECTION = "settings_roles"

SYSTEM_ROLE_DOCS: tuple[dict[str, Any], ...] = (
    {
        "role_key": ROLE_SUPER_ADMIN,
        "label": "Super Admin",
        "kind": "system",
        "module_permissions": sorted(ALL_PERMISSIONS),
        "is_active": True,
    },
    {
        "role_key": ROLE_ADMIN,
        "label": "Admin",
        "kind": "system",
        "module_permissions": sorted(
            {
                "operations.full",
                "reports.view",
                "access.all_branches",
            }
        ),
        "is_active": True,
    },
    {
        "role_key": ROLE_USER,
        "label": "User",
        "kind": "system",
        "module_permissions": [],
        "is_active": True,
    },
)

REQUIRED_ROLE_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": ROLES_COLLECTION,
        "keys": [("role_key", ASCENDING)],
        "name": "uniq_settings_role_key",
        "unique": True,
    }
]


def roles_collection(db: Database) -> Collection:
    return db[ROLES_COLLECTION]


def ensure_settings_role_indexes(db: Database) -> list[str]:
    created: list[str] = []
    for spec in REQUIRED_ROLE_INDEX_SPECS:
        created.append(
            roles_collection(db).create_index(
                spec["keys"], name=spec["name"], unique=bool(spec.get("unique"))
            )
        )
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified([s["name"] for s in REQUIRED_ROLE_INDEX_SPECS], db=db)
    return created


def require_settings_role_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_ROLE_INDEX_SPECS)


def ensure_system_roles(db: Database) -> int:
    """Idempotent catalog rows for system roles — $setOnInsert only."""
    require_settings_role_indexes(db)
    now = datetime.now(timezone.utc)
    inserted = 0
    for base in SYSTEM_ROLE_DOCS:
        res = roles_collection(db).update_one(
            {"role_key": base["role_key"]},
            {
                "$setOnInsert": {
                    **base,
                    "version": 1,
                    "duplicated_from": None,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        if res.upserted_id is not None:
            inserted += 1
    return inserted


def _public(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "role_key": doc["role_key"],
        "label": doc.get("label") or doc["role_key"],
        "kind": doc.get("kind") or "custom",
        "module_permissions": list(doc.get("module_permissions") or []),
        "is_active": bool(doc.get("is_active", True)),
        "version": int(doc.get("version") or 1),
        "duplicated_from": doc.get("duplicated_from"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def list_roles(db: Database, *, include_inactive: bool = True) -> list[dict[str, Any]]:
    require_settings_role_indexes(db)
    ensure_system_roles(db)
    query: dict[str, Any] = {}
    if not include_inactive:
        query["is_active"] = True
    return [_public(d) for d in roles_collection(db).find(query).sort("role_key", ASCENDING)]


def get_role(db: Database, role_key: str) -> dict[str, Any] | None:
    require_settings_role_indexes(db)
    ensure_system_roles(db)
    doc = roles_collection(db).find_one({"role_key": role_key})
    return _public(doc) if doc else None


def create_custom_role(
    db: Database,
    *,
    role_key: str,
    label: str,
    module_permissions: list[str] | None = None,
    duplicated_from: str | None = None,
) -> dict[str, Any]:
    require_settings_role_indexes(db)
    ensure_system_roles(db)
    key = role_key.strip().lower().replace(" ", "-")
    if not key or key in {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER}:
        raise ValueError("role_key custom tidak valid atau bentrok dengan role sistem.")
    if not label.strip():
        raise ValueError("label wajib diisi.")
    # Default deny — ignore accidental grants unless caller passes explicitly after confirm.
    perms = sorted(set(module_permissions or []))
    unknown = [p for p in perms if p not in ALL_PERMISSIONS]
    if unknown:
        raise ValueError(f"Permission tidak dikenal: {', '.join(unknown)}")
    now = datetime.now(timezone.utc)
    doc = {
        "role_key": key,
        "label": label.strip(),
        "kind": "custom",
        "module_permissions": perms,
        "is_active": True,
        "version": 1,
        "duplicated_from": duplicated_from,
        "created_at": now,
        "updated_at": now,
    }
    roles_collection(db).insert_one(doc)
    return _public(doc)


def update_role_label(db: Database, role_key: str, *, label: str) -> dict[str, Any]:
    require_settings_role_indexes(db)
    if not label.strip():
        raise ValueError("label wajib diisi.")
    doc = roles_collection(db).find_one_and_update(
        {"role_key": role_key},
        {"$set": {"label": label.strip(), "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise LookupError(f"Role tidak ditemukan: {role_key}")
    return _public(doc)


def set_module_permissions(
    db: Database,
    role_key: str,
    *,
    module_permissions: list[str],
    expected_version: int,
) -> dict[str, Any]:
    require_settings_role_indexes(db)
    ensure_system_roles(db)
    role = roles_collection(db).find_one({"role_key": role_key})
    if role is None:
        raise LookupError(f"Role tidak ditemukan: {role_key}")
    if role.get("kind") == "system" and role_key == ROLE_SUPER_ADMIN:
        raise ValueError("Permission Super Admin tidak dapat diubah lewat Settings.")
    perms = sorted(set(module_permissions))
    unknown = [p for p in perms if p not in ALL_PERMISSIONS]
    if unknown:
        raise ValueError(f"Permission tidak dikenal: {', '.join(unknown)}")
    doc = roles_collection(db).find_one_and_update(
        {"role_key": role_key, "version": int(expected_version)},
        {
            "$set": {
                "module_permissions": perms,
                "updated_at": datetime.now(timezone.utc),
            },
            "$inc": {"version": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        current = roles_collection(db).find_one({"role_key": role_key})
        if current is None:
            raise LookupError(f"Role tidak ditemukan: {role_key}")
        raise RuntimeError("version_conflict")
    return _public(doc)


def deactivate_role(db: Database, role_key: str) -> dict[str, Any]:
    require_settings_role_indexes(db)
    role = roles_collection(db).find_one({"role_key": role_key})
    if role is None:
        raise LookupError(f"Role tidak ditemukan: {role_key}")
    if role.get("kind") == "system":
        raise ValueError("Role sistem tidak dapat dinonaktifkan.")
    doc = roles_collection(db).find_one_and_update(
        {"role_key": role_key},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    assert doc is not None
    return _public(doc)


def activate_role(db: Database, role_key: str) -> dict[str, Any]:
    require_settings_role_indexes(db)
    doc = roles_collection(db).find_one_and_update(
        {"role_key": role_key, "kind": "custom"},
        {"$set": {"is_active": True, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise LookupError(f"Custom role tidak ditemukan: {role_key}")
    return _public(doc)
