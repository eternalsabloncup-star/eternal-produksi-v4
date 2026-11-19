"""API Checkpoint D — produksi setelah penjadwalan."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.db import get_db
from app.repositories import sample_repository as samples
from app.repositories import schedule_repository as schedules
from app.repositories.po_repository import get_by_accurate_id
from app.repositories.task_repository import TaskQuantityError
from app.repositories import master_repository as master
from app.services import production_service
from app.services.index_preflight import MissingRequiredIndexError
from app.services.task_engine import TaskPermissionError

router = APIRouter(prefix="/api/production", tags=["production"])

MAX_SAMPLE_BYTES = 10 * 1024 * 1024
ALLOWED_SAMPLE_TYPES = {"image/png", "image/jpeg", "image/webp", "application/pdf"}


class QtyConfirmRequest(BaseModel):
    qty: float = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class SampleDecisionRequest(BaseModel):
    version: int = Field(ge=1)
    approved: bool
    reason: str | None = Field(default=None, max_length=500)


def _po_or_404(accurate_po_id: str) -> dict[str, Any]:
    doc = get_by_accurate_id(get_db(), accurate_po_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO Accurate tidak ditemukan")
    return doc


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, TaskPermissionError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, master.MissingMasterConfigError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": exc.code, "detail": str(exc)},
        )
    if isinstance(exc, MissingRequiredIndexError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": exc.code,
                "detail": str(exc),
                "missing": exc.missing,
                "conflict": exc.conflict,
            },
        )
    if isinstance(exc, production_service.ProductionGateError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, (production_service.ProductionQuantityError, TaskQuantityError)):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    raise exc


@router.get("/{accurate_po_id}/lines/{accurate_line_id}")
def get_line_production(
    accurate_po_id: str,
    accurate_line_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    po_doc = _po_or_404(accurate_po_id)
    return production_service.line_production_state(
        get_db(), po_doc, accurate_line_id
    )


@router.post("/{accurate_po_id}/lines/{accurate_line_id}/preparation")
def post_preparation(
    accurate_po_id: str,
    accurate_line_id: str,
    body: QtyConfirmRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return production_service.confirm_preparation(
            get_db(),
            auth.user,
            accurate_po_id=accurate_po_id,
            accurate_line_id=accurate_line_id,
            qty=body.qty,
            idempotency_key=body.idempotency_key,
            note=body.note,
        )
    except Exception as exc:  # noqa: BLE001 - dipetakan ke HTTP yang tepat
        raise _handle(exc) from exc


@router.post("/allocations/{allocation_id}/screen")
def post_screen(
    allocation_id: str,
    body: QtyConfirmRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return production_service.confirm_screen(
            get_db(),
            auth.user,
            allocation_id=allocation_id,
            qty=body.qty,
            idempotency_key=body.idempotency_key,
            note=body.note,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/allocations/{allocation_id}/samples")
def get_samples(
    allocation_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    if schedules.get_allocation(db, allocation_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alokasi tidak ditemukan")
    return {
        "allocation_id": allocation_id,
        "items": samples.list_samples(db, allocation_id),
    }


@router.post("/allocations/{allocation_id}/samples")
async def post_sample(
    allocation_id: str,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_SAMPLE_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Tipe file sampel tidak didukung: {content_type}",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File sampel kosong")
    if len(content) > MAX_SAMPLE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File sampel terlalu besar"
        )
    try:
        return production_service.submit_sample(
            get_db(),
            auth.user,
            allocation_id=allocation_id,
            filename=file.filename or "sample.bin",
            content_type=content_type,
            content=content,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/allocations/{allocation_id}/samples/decision")
def post_sample_decision(
    allocation_id: str,
    body: SampleDecisionRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return production_service.decide_sample(
            get_db(),
            auth.user,
            allocation_id=allocation_id,
            version=body.version,
            approved=body.approved,
            reason=body.reason,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/allocations/{allocation_id}/print")
def post_print(
    allocation_id: str,
    body: QtyConfirmRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return production_service.confirm_print(
            get_db(),
            auth.user,
            allocation_id=allocation_id,
            qty=body.qty,
            idempotency_key=body.idempotency_key,
            note=body.note,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/{accurate_po_id}/lines/{accurate_line_id}/qc-packing")
def post_qc_packing(
    accurate_po_id: str,
    accurate_line_id: str,
    body: QtyConfirmRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return production_service.confirm_qc_packing(
            get_db(),
            auth.user,
            accurate_po_id=accurate_po_id,
            accurate_line_id=accurate_line_id,
            qty=body.qty,
            idempotency_key=body.idempotency_key,
            note=body.note,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc
