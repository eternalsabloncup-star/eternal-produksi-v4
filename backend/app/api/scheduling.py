"""API Penjadwalan (Checkpoint C) — alokasi operator per item line Cetak."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.db import get_db
from app.repositories import schedule_repository as schedules
from app.repositories.po_repository import get_by_accurate_id
from app.repositories import master_repository as master
from app.services import scheduling_service
from app.services.index_preflight import MissingRequiredIndexError
from app.services.task_engine import TaskPermissionError

router = APIRouter(prefix="/api/scheduling", tags=["scheduling"])


class AllocationIn(BaseModel):
    operator_email: str = Field(min_length=3, max_length=200)
    qty: float = Field(gt=0)
    # Opsional agar Jadwal Produksi lama tidak regres; form Tugas Tim mengirim keduanya.
    print_color: str | None = Field(default=None, max_length=100)
    screen_kind: str | None = Field(default=None, max_length=20)


class CreateAllocationsRequest(BaseModel):
    allocations: list[AllocationIn] = Field(min_length=1)
    idempotency_key: str = Field(min_length=8, max_length=200)
    schedule_date: str | None = None


class ReviseAllocationRequest(BaseModel):
    qty: float = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)


class RescheduleScanRequest(BaseModel):
    as_of: str | None = None


def _po_or_404(accurate_po_id: str) -> dict[str, Any]:
    doc = get_by_accurate_id(get_db(), accurate_po_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO Accurate tidak ditemukan")
    return doc


@router.get("/operators")
def get_scheduling_operators(
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Operator aktif Master Pengguna untuk dropdown penjadwalan.

    Identifier tetap email (kontrak alokasi); label UI memakai display_name.
    """
    from app.repositories import settings_user_repository as users
    from app.services.task_engine import can_act

    db = get_db()
    allowed = (
        can_act(db, auth.user, task_type="penjadwalan", action="confirm")
        or can_act(db, auth.user, task_type="penjadwalan", action="input")
        or can_act(db, auth.user, task_type="penjadwalan", action="edit")
        or can_act(db, auth.user, task_type="penjadwalan-ulang", action="edit")
        or can_act(db, auth.user, task_type="penjadwalan-ulang", action="input")
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Daftar operator hanya untuk peran penjadwalan.",
        )
    return {"items": users.list_active_operators(db)}


@router.get("/{accurate_po_id}/lines/{accurate_line_id}")
def get_line_schedule(
    accurate_po_id: str,
    accurate_line_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    po_doc = _po_or_404(accurate_po_id)
    try:
        return scheduling_service.line_schedule_state(
            get_db(), po_doc, accurate_line_id
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{accurate_po_id}")
def get_po_schedule(
    accurate_po_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    po_doc = _po_or_404(accurate_po_id)
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    db = get_db()
    lines = [
        scheduling_service.line_schedule_state(
            db, po_doc, str(item.get("accurate_line_id"))
        )
        for item in (header.get("items") or [])
        if isinstance(item, dict) and item.get("process_kind") == "Cetak"
    ]
    return {"accurate_po_id": accurate_po_id, "lines": lines, "source": "mongodb"}


@router.post("/{accurate_po_id}/lines/{accurate_line_id}/allocations")
def post_allocations(
    accurate_po_id: str,
    accurate_line_id: str,
    body: CreateAllocationsRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    po_doc = _po_or_404(accurate_po_id)
    try:
        return scheduling_service.create_allocations(
            get_db(),
            auth.user,
            po_doc=po_doc,
            accurate_line_id=accurate_line_id,
            allocations=[a.model_dump() for a in body.allocations],
            idempotency_key=body.idempotency_key,
            schedule_date=body.schedule_date,
        )
    except TaskPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except master.MissingMasterConfigError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": exc.code, "detail": str(exc)},
        ) from exc
    except MissingRequiredIndexError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": exc.code,
                "detail": str(exc),
                "missing": exc.missing,
                "conflict": exc.conflict,
            },
        ) from exc
    except scheduling_service.ScheduleGateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except scheduling_service.ScheduleQuantityError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.put("/allocations/{allocation_id}/qty")
def put_allocation_qty(
    allocation_id: str,
    body: ReviseAllocationRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    db = get_db()
    alloc = schedules.get_allocation(db, allocation_id)
    if alloc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alokasi tidak ditemukan")
    po_doc = _po_or_404(str(alloc["accurate_po_id"]))
    try:
        return scheduling_service.revise_allocation_qty(
            db,
            auth.user,
            po_doc=po_doc,
            allocation_id=allocation_id,
            qty=body.qty,
            reason=body.reason,
        )
    except TaskPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except scheduling_service.AllocationBelowConfirmedPrintError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": scheduling_service.AllocationBelowConfirmedPrintError.code,
                "detail": str(exc),
            },
        ) from exc
    except scheduling_service.AllocationDownstreamConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except scheduling_service.AllocationConcurrencyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except scheduling_service.AllocationLockedError as exc:
        # Legacy path — tetap 409 bila masih terpanggil.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except scheduling_service.ScheduleQuantityError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/allocations/{allocation_id}/history")
def get_allocation_history(
    allocation_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    if schedules.get_allocation(db, allocation_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alokasi tidak ditemukan")
    return {
        "allocation_id": allocation_id,
        "items": schedules.allocation_history(db, allocation_id),
    }


@router.post("/{accurate_po_id}/reschedule-scan")
def post_reschedule_scan(
    accurate_po_id: str,
    body: RescheduleScanRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    po_doc = _po_or_404(accurate_po_id)
    try:
        return scheduling_service.scan_reschedule(
            get_db(), auth.user, po_doc=po_doc, as_of=body.as_of
        )
    except TaskPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except master.MissingMasterConfigError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": exc.code, "detail": str(exc)},
        ) from exc
    except MissingRequiredIndexError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": exc.code,
                "detail": str(exc),
                "missing": exc.missing,
                "conflict": exc.conflict,
            },
        ) from exc
