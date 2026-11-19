"""Settings V4 orchestration — users, roles, permissions, org masters, audit."""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from pymongo.database import Database

from app.auth.permissions import (
    ALL_PERMISSIONS,
    PERM_MANAGE_ROLES,
    PERM_MANAGE_SETTINGS,
    PERM_MANAGE_USERS,
    PERM_VIEW_AUDIT_LOG,
    SENSITIVE_MODULE_PERMISSIONS,
    has_permission,
    permissions_for_role,
)
from app.auth.roles import ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_USER, is_super_admin
from app.auth import session as sessions
from app.domain.operational import OPERATIONAL_ROLES, TASK_ACTIONS
from app.repositories import master_repository as master
from app.repositories import org_master_repository as org
from app.repositories import settings_role_repository as roles
from app.repositories import settings_user_repository as users
from app.services.audit_log import append_audit_event, list_audit_events
from app.services.task_engine import TaskPermissionError


class SettingsConflictError(RuntimeError):
    """Version / concurrent conflict."""


class SettingsDependencyError(RuntimeError):
    """Deactivate blocked by dependencies."""


def _require(user: dict[str, Any], perm: str) -> None:
    if not has_permission(user, perm):
        raise TaskPermissionError("Akses ditolak")


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("email_normalized") or "")


def _resolve_module_perms_for_role(db: Database, role_key: str) -> list[str]:
    if is_super_admin(role_key):
        return sorted(ALL_PERMISSIONS)
    if role_key in {ROLE_ADMIN, ROLE_USER}:
        # Prefer catalog if present; else hard-coded baseline.
        cat = roles.get_role(db, role_key)
        if cat is not None:
            return list(cat["module_permissions"])
        return sorted(permissions_for_role(role_key))
    cat = roles.get_role(db, role_key)
    if cat is None or not cat.get("is_active"):
        raise ValueError(f"Role tidak tersedia: {role_key}")
    return list(cat["module_permissions"])


# ----- Users -----


def list_users(db: Database, actor: dict[str, Any]) -> list[dict[str, Any]]:
    _require(actor, PERM_MANAGE_USERS)
    return users.list_users(db)


def create_user(db: Database, actor: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_USERS)
    role = str(body.get("role") or ROLE_USER)
    perms = _resolve_module_perms_for_role(db, role)
    created = users.create_user(
        db,
        email=str(body["email"]),
        display_name=str(body["display_name"]),
        password=str(body["password"]),
        role=role,
        operational_role=body.get("operational_role"),
        branch_ids=body.get("branch_ids"),
        effective_module_permissions=perms,
    )
    append_audit_event(
        db,
        action="settings.user.create",
        actor_email=_actor(actor),
        target_email=created["email"],
        detail={"after": {k: created[k] for k in created if k != "id"}, "user_id": created["id"]},
    )
    return created


def update_user(db: Database, actor: dict[str, Any], user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_USERS)
    before = users.get_user(db, user_id)
    if before is None:
        raise LookupError("Pengguna tidak ditemukan.")
    role = body.get("role")
    perms = None
    if role is not None:
        perms = _resolve_module_perms_for_role(db, str(role))
    updated = users.update_user(
        db,
        user_id,
        display_name=body.get("display_name"),
        role=str(role) if role is not None else None,
        operational_role=body["operational_role"] if "operational_role" in body else ...,
        branch_ids=body.get("branch_ids"),
        effective_module_permissions=perms,
    )
    append_audit_event(
        db,
        action="settings.user.update",
        actor_email=_actor(actor),
        target_email=updated["email"],
        detail={"before": before, "after": updated, "reason": body.get("reason")},
    )
    return updated


def set_user_active(
    db: Database, actor: dict[str, Any], user_id: str, *, is_active: bool, reason: str | None
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_USERS)
    before = users.get_user(db, user_id)
    if before is None:
        raise LookupError("Pengguna tidak ditemukan.")
    if not is_active:
        # Dependency: open tasks assigned conceptually by operational role — warn via count.
        open_tasks = db["operational_tasks"].count_documents(
            {
                "status": {"$nin": ["done", "cancelled"]},
                "assigned_role": before.get("operational_role"),
            }
        ) if before.get("operational_role") else 0
        if open_tasks > 0 and not (reason or "").strip():
            raise SettingsDependencyError(
                f"Pengguna terkait {open_tasks} tugas terbuka pada role operasional — "
                "sertakan reason untuk konfirmasi nonaktif."
            )
    updated = users.set_active(db, user_id, is_active=is_active)
    if not is_active:
        sessions.revoke_all_sessions_for_user(db, ObjectId(user_id))
    append_audit_event(
        db,
        action="settings.user.activate" if is_active else "settings.user.deactivate",
        actor_email=_actor(actor),
        target_email=updated["email"],
        detail={"before": before, "after": updated, "reason": reason},
    )
    return updated


def reset_user_password(
    db: Database, actor: dict[str, Any], user_id: str, *, new_password: str, reason: str | None
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_USERS)
    target = users.get_user(db, user_id)
    if target is None:
        raise LookupError("Pengguna tidak ditemukan.")
    users.reset_password(db, user_id, new_password=new_password)
    sessions.revoke_all_sessions_for_user(db, ObjectId(user_id))
    append_audit_event(
        db,
        action="settings.user.reset_password",
        actor_email=_actor(actor),
        target_email=target["email"],
        detail={"user_id": user_id, "reason": reason, "sessions_revoked": True},
    )
    return {"ok": True, "user_id": user_id}


def list_user_sessions(db: Database, actor: dict[str, Any], user_id: str) -> list[dict[str, Any]]:
    _require(actor, PERM_MANAGE_USERS)
    if users.get_user(db, user_id) is None:
        raise LookupError("Pengguna tidak ditemukan.")
    return sessions.list_active_sessions_for_user(db, ObjectId(user_id))


def revoke_user_session(
    db: Database, actor: dict[str, Any], user_id: str, session_id: str
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_USERS)
    from bson.errors import InvalidId

    try:
        sid = ObjectId(session_id)
        uid = ObjectId(user_id)
    except InvalidId as exc:
        raise ValueError("id tidak valid") from exc
    target = db["sessions"].find_one({"_id": sid, "user_id": uid})
    if target is None:
        raise LookupError("Sesi tidak ditemukan.")
    sessions.revoke_session(db, sid)
    append_audit_event(
        db,
        action="settings.session.revoke",
        actor_email=_actor(actor),
        target_email=(users.get_user(db, user_id) or {}).get("email"),
        detail={"session_id": session_id, "user_id": user_id},
    )
    return {"ok": True}


# ----- Roles & module permissions -----


def list_roles(db: Database, actor: dict[str, Any]) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_ROLES)
    return {
        "items": roles.list_roles(db),
        "system_roles_bootstrap": roles.system_roles_bootstrap_status(db),
    }


def create_role(db: Database, actor: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_ROLES)
    # Default deny — ignore body permissions unless empty list explicitly.
    created = roles.create_custom_role(
        db,
        role_key=str(body["role_key"]),
        label=str(body["label"]),
        module_permissions=[],
    )
    append_audit_event(
        db,
        action="settings.role.create",
        actor_email=_actor(actor),
        target_email=None,
        detail={"after": created},
    )
    return created


def duplicate_role(
    db: Database, actor: dict[str, Any], source_key: str, *, new_key: str, label: str
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_ROLES)
    src = roles.get_role(db, source_key)
    if src is None:
        raise LookupError("Role sumber tidak ditemukan.")
    # Duplikasi menyalin label/struktur tetapi TIDAK mengaktifkan grant sensitif.
    safe_perms = [
        p for p in src["module_permissions"] if p not in SENSITIVE_MODULE_PERMISSIONS
    ]
    created = roles.create_custom_role(
        db,
        role_key=new_key,
        label=label,
        module_permissions=safe_perms,
        duplicated_from=source_key,
    )
    append_audit_event(
        db,
        action="settings.role.duplicate",
        actor_email=_actor(actor),
        target_email=None,
        detail={
            "source": source_key,
            "after": created,
            "sensitive_stripped": sorted(
                set(src["module_permissions"]) - set(safe_perms)
            ),
        },
    )
    return created


def update_role(db: Database, actor: dict[str, Any], role_key: str, body: dict[str, Any]) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_ROLES)
    before = roles.get_role(db, role_key)
    if before is None:
        raise LookupError("Role tidak ditemukan.")
    updated = roles.update_role_label(db, role_key, label=str(body["label"]))
    append_audit_event(
        db,
        action="settings.role.update",
        actor_email=_actor(actor),
        target_email=None,
        detail={"before": before, "after": updated},
    )
    return updated


def deactivate_role(db: Database, actor: dict[str, Any], role_key: str, *, reason: str | None) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_ROLES)
    active_users = users.count_users_with_role(db, role_key, active_only=True)
    if active_users > 0:
        raise SettingsDependencyError(
            f"Role masih dipakai {active_users} pengguna aktif — pindahkan pengguna dulu."
        )
    before = roles.get_role(db, role_key)
    updated = roles.deactivate_role(db, role_key)
    append_audit_event(
        db,
        action="settings.role.deactivate",
        actor_email=_actor(actor),
        target_email=None,
        detail={"before": before, "after": updated, "reason": reason},
    )
    return updated


def save_module_permissions(
    db: Database,
    actor: dict[str, Any],
    role_key: str,
    *,
    module_permissions: list[str],
    expected_version: int,
    reason: str | None,
    confirm_sensitive: bool,
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_ROLES)
    before = roles.get_role(db, role_key)
    if before is None:
        raise LookupError("Role tidak ditemukan.")
    added = set(module_permissions) - set(before["module_permissions"])
    sensitive_added = added & SENSITIVE_MODULE_PERMISSIONS
    if sensitive_added and not confirm_sensitive:
        raise ValueError(
            "Perubahan grant sensitif memerlukan confirm_sensitive=true dan reason."
        )
    if sensitive_added and not (reason or "").strip():
        raise ValueError("Reason wajib untuk grant sensitif.")
    try:
        updated = roles.set_module_permissions(
            db,
            role_key,
            module_permissions=module_permissions,
            expected_version=expected_version,
        )
    except RuntimeError as exc:
        if str(exc) == "version_conflict":
            raise SettingsConflictError(
                "Konflik versi permission — muat ulang lalu simpan lagi."
            ) from exc
        raise
    users.refresh_effective_permissions_for_role(
        db, role_key, updated["module_permissions"]
    )
    append_audit_event(
        db,
        action="settings.role.permissions.save",
        actor_email=_actor(actor),
        target_email=None,
        detail={
            "before": before,
            "after": updated,
            "reason": reason,
            "sensitive_added": sorted(sensitive_added),
        },
    )
    return updated


# ----- Task types (custom manual) -----


def create_manual_task_type(
    db: Database, actor: dict[str, Any], body: dict[str, Any]
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_SETTINGS)
    master.require_master_indexes(db)
    task_type = str(body.get("task_type") or "").strip().lower().replace(" ", "-")
    label = str(body.get("label") or "").strip()
    scope = str(body.get("scope") or "po")
    assigned_role = str(body.get("assigned_role") or "")
    if not task_type or not label:
        raise ValueError("task_type dan label wajib.")
    if assigned_role not in OPERATIONAL_ROLES:
        raise ValueError("assigned_role harus role operasional terdaftar.")
    if scope not in {"po", "po_line", "po_line_allocation", "delivery_note"}:
        raise ValueError("scope tidak valid.")
    if master.get_task_type(db, task_type) is not None:
        raise RuntimeError("Jenis tugas sudah ada.")
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    doc = {
        "task_type": task_type,
        "label": label,
        "scope": scope,
        "assigned_role": assigned_role,
        "actions": ["view", "input", "confirm"],
        "coordination": False,
        "partial_qty": bool(body.get("partial_qty") or False),
        "kind": "manual",
        "has_workflow_handler": False,
        "is_active": True,
        "is_default_mapping": False,
        "created_at": now,
        "updated_at": now,
    }
    master.task_types_collection(db).insert_one(doc)
    public = {k: v for k, v in doc.items() if k != "_id"}
    append_audit_event(
        db,
        action="settings.task_type.create",
        actor_email=_actor(actor),
        target_email=None,
        detail={"after": public},
    )
    return public


def deactivate_task_type(
    db: Database, actor: dict[str, Any], task_type: str, *, reason: str | None
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_SETTINGS)
    master.require_master_indexes(db)
    existing = master.get_task_type(db, task_type)
    if existing is None:
        raise LookupError("Jenis tugas tidak ditemukan.")
    if existing.get("kind") != "manual" and existing.get("is_default_mapping"):
        # Registered workflow types: soft-deactivate only if Owner insists — block by default.
        raise ValueError(
            "Jenis tugas workflow terdaftar tidak dapat dinonaktifkan dari Settings "
            "tanpa keputusan Owner (handler produksi bergantung padanya)."
        )
    open_count = db["operational_tasks"].count_documents(
        {"task_type": task_type, "status": {"$nin": ["done", "cancelled"]}}
    )
    if open_count > 0:
        raise SettingsDependencyError(
            f"Masih ada {open_count} tugas terbuka untuk jenis ini."
        )
    from datetime import datetime, timezone

    from pymongo import ReturnDocument

    doc = master.task_types_collection(db).find_one_and_update(
        {"task_type": task_type},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    append_audit_event(
        db,
        action="settings.task_type.deactivate",
        actor_email=_actor(actor),
        target_email=None,
        detail={"before": existing, "after": doc, "reason": reason},
    )
    return doc


# ----- Org masters -----


def list_branches(db: Database, actor: dict[str, Any]) -> list[dict[str, Any]]:
    _require(actor, PERM_MANAGE_SETTINGS)
    return org.list_docs(db, org.BRANCHES)


def save_branch(db: Database, actor: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_SETTINGS)
    doc = org.upsert_branch(
        db,
        code=str(body["code"]),
        name=str(body["name"]),
        is_active=bool(body.get("is_active", True)),
    )
    append_audit_event(
        db,
        action="settings.branch.upsert",
        actor_email=_actor(actor),
        target_email=None,
        detail={"after": doc},
    )
    return doc


def set_branch_active(
    db: Database, actor: dict[str, Any], code: str, *, is_active: bool, reason: str | None
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_SETTINGS)
    if not is_active:
        n = org.count_active_users_on_branch(db, code)
        if n > 0 and not (reason or "").strip():
            raise SettingsDependencyError(
                f"Cabang masih dipakai {n} pengguna aktif — sertakan reason."
            )
    doc = org.set_branch_active(db, code, is_active=is_active)
    append_audit_event(
        db,
        action="settings.branch.activate" if is_active else "settings.branch.deactivate",
        actor_email=_actor(actor),
        target_email=None,
        detail={"after": doc, "reason": reason},
    )
    return doc


def list_machines(db: Database, actor: dict[str, Any]) -> list[dict[str, Any]]:
    _require(actor, PERM_MANAGE_SETTINGS)
    return org.list_docs(db, org.MACHINES)


def save_machine(db: Database, actor: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_SETTINGS)
    doc = org.upsert_machine(
        db,
        code=str(body["code"]),
        label=str(body["label"]),
        branch_code=body.get("branch_code"),
        is_active=bool(body.get("is_active", True)),
    )
    append_audit_event(
        db,
        action="settings.machine.upsert",
        actor_email=_actor(actor),
        target_email=None,
        detail={"after": doc},
    )
    return doc


def list_shifts(db: Database, actor: dict[str, Any]) -> list[dict[str, Any]]:
    _require(actor, PERM_MANAGE_SETTINGS)
    return org.list_docs(db, org.SHIFTS)


def save_shift(db: Database, actor: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_SETTINGS)
    doc = org.upsert_shift(
        db,
        code=str(body["code"]),
        label=str(body["label"]),
        start_time=str(body["start_time"]),
        end_time=str(body["end_time"]),
        is_active=bool(body.get("is_active", True)),
    )
    append_audit_event(
        db,
        action="settings.shift.upsert",
        actor_email=_actor(actor),
        target_email=None,
        detail={"after": doc},
    )
    return doc




# ----- Master Produk (adapter → product_master_repository) -----


def list_products_master(db: Database, actor: dict[str, Any]) -> dict[str, Any]:
    """Satu sumber data: master_products.screen_size (bukan Master kedua)."""
    _require(actor, PERM_MANAGE_SETTINGS)
    from app.repositories import product_master_repository as products

    return {
        "items": products.list_products(db),
        "screen_unset_label": products.SCREEN_UNSET,
        "source": "product_master_repository",
    }


def set_product_screen_size(
    db: Database, actor: dict[str, Any], *, item_code: str, screen_size: str
) -> dict[str, Any]:
    _require(actor, PERM_MANAGE_SETTINGS)
    from app.repositories import product_master_repository as products

    before = products.get_product(db, item_code) or {}
    doc = products.upsert_product_screen_size(
        db,
        item_code=item_code,
        screen_size=screen_size,
        actor_email=_actor(actor),
    )
    append_audit_event(
        db,
        action="settings.product.screen_size.set",
        actor_email=_actor(actor),
        target_email=None,
        detail={
            "item_code": doc.get("item_code"),
            "before": before.get("screen_size") or None,
            "after": doc.get("screen_size") or None,
        },
    )
    return {
        "item": doc,
        "display_screen_size": products.screen_size_for_item(
            db, str(doc.get("item_code") or "")
        ),
    }

# ----- Accurate status (non-secret) + Audit -----


def accurate_status(db: Database, actor: dict[str, Any]) -> dict[str, Any]:
    del db
    _require(actor, PERM_MANAGE_SETTINGS)
    from app.config import get_settings

    s = get_settings()
    summary = s.safe_summary()
    return {
        "configured": {
            "base_url": bool(summary.get("accurate_base_url_configured")),
            "api_token": bool(summary.get("accurate_api_token_configured")),
            "signature": bool(summary.get("accurate_signature_configured")),
            "db_id": bool(summary.get("accurate_db_id_configured")),
        },
        "sj_live_sync_enabled": bool(summary.get("sj_sync_enabled")),
        "auth_mode": summary.get("accurate_auth_mode"),
        "safe_summary": summary,
        "note": "Secret Accurate hanya dari environment — tidak disimpan/ditampilkan di Settings.",
    }


def read_audit(db: Database, actor: dict[str, Any], **filters: Any) -> dict[str, Any]:
    _require(actor, PERM_VIEW_AUDIT_LOG)
    return list_audit_events(db, **filters)


def permission_catalog(db: Database | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "module_permissions": sorted(ALL_PERMISSIONS),
        "sensitive_module_permissions": sorted(SENSITIVE_MODULE_PERMISSIONS),
        "operational_roles": list(OPERATIONAL_ROLES),
        "task_actions": list(TASK_ACTIONS),
        "system_roles": [ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_USER],
    }
    if db is not None:
        payload["system_roles_bootstrap"] = roles.system_roles_bootstrap_status(db)
    return payload


def workflow_policy_status() -> dict[str, Any]:
    """Read-only: kebijakan yang sudah didukung vs belum tersedia."""
    return {
        "items": [
            {
                "id": "sample_approval",
                "label": "Approval Sampel oleh Kepala Produksi",
                "status": "available",
                "note": "Diatur lewat Master role-actions approval-sampel.",
            },
            {
                "id": "sample_reject_to_operator",
                "label": "Sampel ditolak → Operator sampel ulang",
                "status": "available",
                "note": "Perilaku production_service; bukan editor status bebas.",
            },
            {
                "id": "payment_bypass",
                "label": "Payment Bypass via Master approve",
                "status": "available",
                "note": "Grant aksi approve pada payment-bypass / PERM_BYPASS_PAYMENT.",
            },
            {
                "id": "loading_sj_discrepancy",
                "label": "Selisih Loading & SJ → koordinasi KP",
                "status": "available",
                "note": "Task type qty-loading-sj-berbeda.",
            },
            {
                "id": "delivery_failed_coordination",
                "label": "Pengiriman gagal → koordinasi",
                "status": "available",
                "note": "Task type pengiriman-gagal-koordinasi.",
            },
            {
                "id": "partial_schedule_backlog",
                "label": "Penjadwalan parsial menyisakan backlog",
                "status": "available",
                "note": "Scheduling service; tidak auto-reschedule.",
            },
            {
                "id": "freeform_transition_editor",
                "label": "Editor bebas status/transisi workflow",
                "status": "unavailable",
                "note": "Belum didukung — ditampilkan disabled di UI.",
            },
            {
                "id": "master_products_screen_size",
                "label": "Master Produk screen_size",
                "status": "available",
                "note": "Adapter Settings → product_master_repository (satu sumber master_products).",
            },
        ]
    }
