"""API Master/Pengaturan + tugas operasional (Checkpoint A).

Semua aksi tulis: terautentikasi, CSRF, divalidasi, berizin, idempoten, teraudit.
Tidak ada seed Master otomatis / createIndex pada request path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.auth.permissions import PERM_MANAGE_SETTINGS, has_permission
from app.db import get_db
from app.domain.operational import OPERATIONAL_ROLES, TASK_ACTIONS
from app.repositories import master_repository as master
from app.repositories import product_master_repository as products
from app.repositories import task_repository as tasks
from app.services import task_engine
from app.services.audit_log import append_audit_event
from app.services.index_preflight import MissingRequiredIndexError
from app.services.object_auth import ObjectAuthError

router = APIRouter(prefix="/api", tags=["operational"])


class TaskTypeRoleRequest(BaseModel):
    role: str = Field(min_length=1)


class RoleActionsRequest(BaseModel):
    role: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    actions: list[str] = Field(default_factory=list)


class ProductScreenRequest(BaseModel):
    item_code: str = Field(min_length=1, max_length=120)
    screen_size: str = Field(default="", max_length=120)


class ConfirmTaskRequest(BaseModel):
    qty: float = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=200)
    note: str | None = Field(default=None, max_length=500)


def _require_settings_permission(auth: AuthContext) -> None:
    if not has_permission(auth.user, PERM_MANAGE_SETTINGS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses ditolak")


def _map_master_errors(exc: Exception) -> HTTPException | None:
    if isinstance(exc, master.MissingMasterConfigError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": getattr(exc, "code", "MISSING_MASTER_CONFIG"),
                "detail": str(exc),
            },
        )
    if isinstance(exc, MissingRequiredIndexError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": getattr(exc, "code", "MISSING_REQUIRED_INDEX"),
                "detail": str(exc),
                "missing": getattr(exc, "missing", []),
                "conflict": getattr(exc, "conflict", []),
            },
        )
    return None


@router.get("/master/task-types")
def get_task_types(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    db = get_db()
    try:
        items = master.list_task_types(db)
    except (master.MissingMasterConfigError, MissingRequiredIndexError) as exc:
        mapped = _map_master_errors(exc)
        assert mapped is not None
        raise mapped from exc
    return {
        "items": items,
        "roles": list(OPERATIONAL_ROLES),
        "actions": list(TASK_ACTIONS),
    }


@router.put("/master/task-types/{task_type}/role")
def put_task_type_role(
    task_type: str,
    body: TaskTypeRoleRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_settings_permission(auth)
    db = get_db()
    try:
        doc = task_engine.set_task_type_role(
            db, auth.user, task_type=task_type, role=body.role
        )
    except (master.MissingMasterConfigError, MissingRequiredIndexError) as exc:
        mapped = _map_master_errors(exc)
        assert mapped is not None
        raise mapped from exc
    except task_engine.MasterRoleConflictError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"detail": str(exc), "conflicts": getattr(exc, "conflicts", {})},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return doc


@router.get("/master/role-actions")
def get_role_actions(
    role: str | None = Query(default=None),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    try:
        items = master.list_role_actions(db, role=role)
    except (master.MissingMasterConfigError, MissingRequiredIndexError) as exc:
        mapped = _map_master_errors(exc)
        assert mapped is not None
        raise mapped from exc
    return {"items": items}


@router.put("/master/role-actions")
def put_role_actions(
    body: RoleActionsRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_settings_permission(auth)
    db = get_db()
    try:
        return task_engine.set_role_actions(
            db,
            auth.user,
            role=body.role,
            task_type=body.task_type,
            actions=body.actions,
        )
    except (master.MissingMasterConfigError, MissingRequiredIndexError) as exc:
        mapped = _map_master_errors(exc)
        assert mapped is not None
        raise mapped from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/master/products")
def get_master_products(
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Daftar Master Produk (ukuran screen). Tanpa seed ukuran."""
    db = get_db()
    try:
        items = products.list_products(db)
    except MissingRequiredIndexError as exc:
        mapped = _map_master_errors(exc)
        assert mapped is not None
        raise mapped from exc
    return {
        "items": items,
        "screen_unset_label": products.SCREEN_UNSET,
    }


@router.put("/master/products/screen-size")
def put_product_screen_size(
    body: ProductScreenRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Atur ukuran screen per item_code — permission settings.manage."""
    _require_settings_permission(auth)
    db = get_db()
    try:
        doc = products.upsert_product_screen_size(
            db,
            item_code=body.item_code,
            screen_size=body.screen_size,
            actor_email=str(auth.user.get("email_normalized") or ""),
        )
    except MissingRequiredIndexError as exc:
        mapped = _map_master_errors(exc)
        assert mapped is not None
        raise mapped from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    append_audit_event(
        db,
        action="master.product.screen_size.set",
        actor_email=str(auth.user.get("email_normalized") or ""),
        target_email=None,
        detail={
            "item_code": doc.get("item_code"),
            "screen_size": doc.get("screen_size") or None,
        },
    )
    return {
        "item": doc,
        "display_screen_size": products.screen_size_for_item(
            db, str(doc.get("item_code") or "")
        ),
    }


@router.get("/tasks")
def list_operational_tasks(
    role: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    done: bool | None = Query(default=None),
    accurate_po_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    from app.services.object_auth import filter_tasks_for_actor

    db = get_db()
    # Ambil lebih longgar lalu saring object-scope agar limit tetap bermakna.
    fetch_limit = min(max(limit * 3, limit), 1000)
    items = tasks.list_tasks(
        db,
        assigned_role=role,
        task_type=task_type,
        done=done,
        accurate_po_id=accurate_po_id,
        limit=fetch_limit,
    )
    filtered = filter_tasks_for_actor(db, auth.user, items)[:limit]
    return {"items": filtered, "total": len(filtered), "source": "mongodb"}


@router.get("/tasks/{task_key:path}/confirmations")
def get_task_confirmations(
    task_key: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    task = tasks.get_task(db, task_key)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tugas tidak ditemukan")
    return {
        "task_key": task_key,
        "items": tasks.list_confirmations(db, task_key),
    }


@router.post("/tasks/{task_key:path}/confirm")
def post_task_confirm(
    task_key: str,
    body: ConfirmTaskRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    db = get_db()
    try:
        result = task_engine.confirm_task(
            db,
            auth.user,
            task_key=task_key,
            qty=body.qty,
            idempotency_key=body.idempotency_key,
            note=body.note,
        )
    except (master.MissingMasterConfigError, MissingRequiredIndexError) as exc:
        mapped = _map_master_errors(exc)
        assert mapped is not None
        raise mapped from exc
    except task_engine.TaskPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except tasks.TaskQuantityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return result
