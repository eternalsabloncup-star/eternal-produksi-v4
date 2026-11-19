"""Settings V4 HTTP API — users, roles, permissions, masters, audit, Accurate status."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.db import get_db
from app.services import settings_service as svc
from app.services.settings_service import SettingsConflictError, SettingsDependencyError
from app.services.task_engine import TaskPermissionError

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, TaskPermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, SettingsConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, SettingsDependencyError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, (ValueError, RuntimeError)):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    raise exc


class CreateUserBody(BaseModel):
    email: str
    display_name: str
    password: str = Field(min_length=12)
    role: str = "user"
    operational_role: str | None = None
    branch_ids: list[str] = Field(default_factory=list)


class UpdateUserBody(BaseModel):
    display_name: str | None = None
    role: str | None = None
    operational_role: str | None = None
    branch_ids: list[str] | None = None
    reason: str | None = None


class ActiveBody(BaseModel):
    is_active: bool
    reason: str | None = None


class ResetPasswordBody(BaseModel):
    new_password: str = Field(min_length=12)
    reason: str | None = None


class CreateRoleBody(BaseModel):
    role_key: str
    label: str


class DuplicateRoleBody(BaseModel):
    new_key: str
    label: str


class UpdateRoleBody(BaseModel):
    label: str


class SavePermissionsBody(BaseModel):
    module_permissions: list[str]
    expected_version: int
    reason: str | None = None
    confirm_sensitive: bool = False


class ManualTaskTypeBody(BaseModel):
    task_type: str
    label: str
    scope: str = "po"
    assigned_role: str
    partial_qty: bool = False


class BranchBody(BaseModel):
    code: str
    name: str
    is_active: bool = True


class MachineBody(BaseModel):
    code: str
    label: str
    branch_code: str | None = None
    is_active: bool = True


class ShiftBody(BaseModel):
    code: str
    label: str
    start_time: str
    end_time: str
    is_active: bool = True


@router.get("/catalog")
def get_catalog(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    del auth
    return {
        **svc.permission_catalog(),
        "workflow_policies": svc.workflow_policy_status(),
    }


# ---- Users ----


@router.get("/users")
def get_users(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    try:
        return {"items": svc.list_users(get_db(), auth.user)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/users")
def post_user(
    body: CreateUserBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.create_user(get_db(), auth.user, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.patch("/users/{user_id}")
def patch_user(
    user_id: str, body: UpdateUserBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.update_user(
            get_db(), auth.user, user_id, body.model_dump(exclude_unset=True)
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/users/{user_id}/active")
def post_user_active(
    user_id: str, body: ActiveBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.set_user_active(
            get_db(),
            auth.user,
            user_id,
            is_active=body.is_active,
            reason=body.reason,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/users/{user_id}/reset-password")
def post_reset_password(
    user_id: str, body: ResetPasswordBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.reset_user_password(
            get_db(),
            auth.user,
            user_id,
            new_password=body.new_password,
            reason=body.reason,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/users/{user_id}/sessions")
def get_sessions(
    user_id: str, auth: AuthContext = Depends(require_session)
) -> dict[str, Any]:
    try:
        return {"items": svc.list_user_sessions(get_db(), auth.user, user_id)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.delete("/users/{user_id}/sessions/{session_id}")
def delete_session(
    user_id: str, session_id: str, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.revoke_user_session(get_db(), auth.user, user_id, session_id)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


# ---- Roles ----


@router.get("/roles")
def get_roles(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    try:
        return {"items": svc.list_roles(get_db(), auth.user)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/roles")
def post_role(
    body: CreateRoleBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.create_role(get_db(), auth.user, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/roles/{role_key}/duplicate")
def post_duplicate_role(
    role_key: str, body: DuplicateRoleBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.duplicate_role(
            get_db(), auth.user, role_key, new_key=body.new_key, label=body.label
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.patch("/roles/{role_key}")
def patch_role(
    role_key: str, body: UpdateRoleBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.update_role(get_db(), auth.user, role_key, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/roles/{role_key}/deactivate")
def post_deactivate_role(
    role_key: str, body: ActiveBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.deactivate_role(
            get_db(), auth.user, role_key, reason=body.reason
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.put("/roles/{role_key}/permissions")
def put_role_permissions(
    role_key: str,
    body: SavePermissionsBody,
    auth: AuthContext = Depends(require_csrf),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict[str, Any]:
    expected = body.expected_version
    if if_match and if_match.strip().isdigit():
        expected = int(if_match.strip())
    try:
        return svc.save_module_permissions(
            get_db(),
            auth.user,
            role_key,
            module_permissions=body.module_permissions,
            expected_version=expected,
            reason=body.reason,
            confirm_sensitive=body.confirm_sensitive,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


# ---- Manual task types ----


@router.post("/task-types/manual")
def post_manual_task_type(
    body: ManualTaskTypeBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.create_manual_task_type(get_db(), auth.user, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/task-types/{task_type}/deactivate")
def post_deactivate_task_type(
    task_type: str, body: ActiveBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.deactivate_task_type(
            get_db(), auth.user, task_type, reason=body.reason
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


# ---- Org masters ----


@router.get("/branches")
def get_branches(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    try:
        return {"items": svc.list_branches(get_db(), auth.user)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.put("/branches")
def put_branch(
    body: BranchBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.save_branch(get_db(), auth.user, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/branches/{code}/active")
def post_branch_active(
    code: str, body: ActiveBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.set_branch_active(
            get_db(), auth.user, code, is_active=body.is_active, reason=body.reason
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/machines")
def get_machines(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    try:
        return {"items": svc.list_machines(get_db(), auth.user)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.put("/machines")
def put_machine(
    body: MachineBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.save_machine(get_db(), auth.user, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/shifts")
def get_shifts(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    try:
        return {"items": svc.list_shifts(get_db(), auth.user)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.put("/shifts")
def put_shift(
    body: ShiftBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    try:
        return svc.save_shift(get_db(), auth.user, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/products")
def get_products_blocked(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    """Master Produk bergantung PR #9 — belum di baseline."""
    del auth
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Master Produk screen_size bergantung PR #9 yang belum di-merge ke main. "
        "Jangan cherry-pick diam-diam.",
    )


@router.get("/accurate")
def get_accurate(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    try:
        return svc.accurate_status(get_db(), auth.user)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/audit")
def get_audit(
    auth: AuthContext = Depends(require_session),
    action: str | None = None,
    actor_email: str | None = None,
    module: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return svc.read_audit(
            get_db(),
            auth.user,
            action=action,
            actor_email=actor_email,
            module=module,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/workflow-policies")
def get_workflow_policies(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    del auth
    return svc.workflow_policy_status()
