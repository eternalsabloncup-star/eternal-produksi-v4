"""API Checkpoint E — Surat Jalan, Loading, Driver, bukti pengiriman."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.auth.permissions import PERM_OPERATIONS_FULL, has_permission
from app.db import get_db
from app.db_guard import UnsafeMongoTargetError
from app.repositories import delivery_repository as deliveries
from app.repositories import evidence_repository as evidence
from app.repositories.task_repository import TaskQuantityError
from app.services import delivery_service
from app.services import delivery_sync
from app.services.evidence_access import (
    EvidenceAccessDenied,
    assert_delivery_evidence_access,
)
from app.services.object_auth import ObjectAuthError, require_sj_branch_access
from app.services.task_engine import TaskPermissionError

router = APIRouter(prefix="/api/delivery", tags=["delivery"])

MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


class IngestDeliveryNotesRequest(BaseModel):
    delivery_notes: list[dict[str, Any]] = Field(min_length=1)


class AccurateDeliverySyncRequest(BaseModel):
    """Sinkronisasi manual SJ Accurate — hanya DB test terisolasi."""

    page_size: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=50, ge=1, le=200)
    max_rows: int = Field(default=500, ge=1, le=2000)
    timeout_seconds: float = Field(default=120.0, ge=5, le=600)


class LoadingConfirmRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    # Opsional: penugasan kurir eksplisit saat Loading (tidak menimpa assignment valid).
    assigned_driver_email: str | None = Field(default=None, max_length=200)


class CoordinationConfirmRequest(BaseModel):
    task_key: str = Field(min_length=8)
    idempotency_key: str = Field(min_length=8, max_length=200)


class DeliveryFailureRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class LoadingCancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class EvidenceDeleteRequest(BaseModel):
    evidence_id: str = Field(min_length=5)
    reason: str = Field(min_length=3, max_length=500)


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, (TaskPermissionError, EvidenceAccessDenied, ObjectAuthError)):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, delivery_sync.DeliverySyncInProgressError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, delivery_sync.DeliverySyncDisabledError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, UnsafeMongoTargetError):
        return HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Sinkronisasi SJ ditolak: target Mongo tidak aman untuk mutasi.",
        )
    if isinstance(
        exc, (delivery_service.LoadingValidationError, delivery_service.LoadingCancelError)
    ):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, TaskQuantityError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(
        exc,
        (delivery_service.DeliveryEvidenceError, deliveries.DeliveryNoteIdentityError),
    ):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    raise exc


@router.get("/schedule")
def get_schedule(
    ship_date_from: str | None = None,
    ship_date_to: str | None = None,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    return delivery_service.delivery_schedule(
        get_db(),
        auth.user,
        ship_date_from=ship_date_from,
        ship_date_to=ship_date_to,
    )


@router.post("/sync/accurate")
def post_accurate_delivery_sync(
    body: AccurateDeliverySyncRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Sinkronisasi manual SJ Accurate → Mongo V4 test (operasi penuh saja).

    Memerlukan V4_SJ_SYNC_ENABLED=1 (kill switch) + operations.full.
    Selalu memakai mongo_guard=test. Production ditolak fail-closed.
    Tidak menulis Accurate. Tidak menghapus SJ yang absen dari listing.
    """
    if not has_permission(auth.user, PERM_OPERATIONS_FULL):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses ditolak")
    try:
        return delivery_sync.sync_delivery_orders_from_accurate(
            get_db(),
            auth.user,
            page_size=body.page_size,
            max_pages=body.max_pages,
            max_rows=body.max_rows,
            timeout_seconds=body.timeout_seconds,
            mongo_guard="test",
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/notes")
def post_delivery_notes(
    body: IngestDeliveryNotesRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Snapshot SJ Accurate (idempoten) dari payload yang disediakan caller."""
    if not has_permission(auth.user, PERM_OPERATIONS_FULL):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses ditolak")
    db = get_db()
    results = []
    try:
        for payload in body.delivery_notes:
            results.append(delivery_service.ingest_delivery_note(db, auth.user, payload))
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc
    return {"items": results, "count": len(results)}


@router.get("/{accurate_sj_id}")
def get_delivery_note(
    accurate_sj_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    try:
        note = require_sj_branch_access(db, auth.user, accurate_sj_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return {
        "delivery_note": note,
        "loading": deliveries.get_loading_event(db, accurate_sj_id),
        "loadings": deliveries.list_loading_events(db, accurate_sj_id),
        "revisions": deliveries.list_note_revisions(db, accurate_sj_id),
        "events": deliveries.list_delivery_events(db, accurate_sj_id),
        "evidence": delivery_service.active_delivery_evidence(db, accurate_sj_id),
    }


@router.get("/{accurate_sj_id}/evidence/{evidence_id}/content")
def get_delivery_evidence_content(
    accurate_sj_id: str,
    evidence_id: str,
    auth: AuthContext = Depends(require_session),
) -> StreamingResponse:
    """Read-only stream foto/tanda tangan pengiriman — scope SJ; soft-delete → 404."""
    db = get_db()
    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Surat Jalan tidak ditemukan")
    try:
        assert_delivery_evidence_access(db, auth.user, note=note)
    except EvidenceAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    active = delivery_service.active_delivery_evidence(db, accurate_sj_id)
    allowed = {
        str(item.get("evidence_id"))
        for item in (active.get("photos") or []) + (active.get("signatures") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    # Juga izinkan evidence yang tertaut ke event (meski daftar active berbeda bentuk).
    for ev in deliveries.list_delivery_events(db, accurate_sj_id):
        for eid in ev.get("photo_evidence_ids") or []:
            allowed.add(str(eid))
        if ev.get("signature_evidence_id"):
            allowed.add(str(ev["signature_evidence_id"]))
    if evidence_id not in allowed:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Bukti tidak terkait Surat Jalan ini.",
        )
    meta = evidence.get_evidence(db, evidence_id)
    if meta is None or meta.get("deleted_at") is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bukti tidak ditemukan")
    scope = str(meta.get("scope") or "")
    if scope not in (
        delivery_service.DELIVERY_PHOTO_SCOPE,
        delivery_service.DELIVERY_SIGNATURE_SCOPE,
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bukti bukan file pengiriman.")
    if str(meta.get("scope_id")) != str(accurate_sj_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Bukti di luar scope Surat Jalan yang diminta.",
        )
    try:
        _doc, stream = evidence.open_evidence_stream(db, evidence_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    filename = str(meta.get("filename") or "delivery.bin")
    content_type = str(meta.get("content_type") or "application/octet-stream")
    return StreamingResponse(
        stream,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{accurate_sj_id}/loading-validation")
def get_loading_validation(
    accurate_sj_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        return delivery_service.validate_loading(
            get_db(), accurate_sj_id, user=auth.user
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/{accurate_sj_id}/loading")
def post_loading(
    accurate_sj_id: str,
    body: LoadingConfirmRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return delivery_service.confirm_loading(
            get_db(),
            auth.user,
            accurate_sj_id=accurate_sj_id,
            idempotency_key=body.idempotency_key,
            assigned_driver_email=body.assigned_driver_email,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/{accurate_sj_id}/loading/cancel")
def post_cancel_loading(
    accurate_sj_id: str,
    body: LoadingCancelRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return delivery_service.cancel_loading(
            get_db(), auth.user, accurate_sj_id=accurate_sj_id, reason=body.reason
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/{accurate_sj_id}/revisions")
def get_revisions(
    accurate_sj_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    try:
        require_sj_branch_access(db, auth.user, accurate_sj_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return {
        "accurate_sj_id": accurate_sj_id,
        "revisions": deliveries.list_note_revisions(db, accurate_sj_id),
        "loadings": deliveries.list_loading_events(db, accurate_sj_id),
    }


@router.post("/{accurate_sj_id}/discrepancy")
def post_discrepancy(
    accurate_sj_id: str,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return delivery_service.raise_quantity_discrepancy(
            get_db(), auth.user, accurate_sj_id=accurate_sj_id
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/coordination/confirm")
def post_coordination(
    body: CoordinationConfirmRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return delivery_service.confirm_coordination(
            get_db(),
            auth.user,
            task_key=body.task_key,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/{accurate_sj_id}/delivered")
async def post_delivered(
    accurate_sj_id: str,
    recipient_name: str = Form(...),
    photos: list[UploadFile] = File(...),
    signature: UploadFile = File(...),
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    photo_payloads: list[tuple[str, str, bytes]] = []
    for photo in photos:
        ctype = photo.content_type or "application/octet-stream"
        if ctype not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Tipe foto tidak didukung: {ctype}",
            )
        content = await photo.read()
        if not content:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Foto kosong")
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Foto terlalu besar"
            )
        photo_payloads.append((photo.filename or "photo.png", ctype, content))

    sig_type = signature.content_type or "application/octet-stream"
    if sig_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Tipe tanda tangan tidak didukung: {sig_type}",
        )
    sig_content = await signature.read()
    if len(sig_content) > MAX_FILE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Tanda tangan terlalu besar"
        )

    try:
        return delivery_service.confirm_delivery_success(
            get_db(),
            auth.user,
            accurate_sj_id=accurate_sj_id,
            recipient_name=recipient_name,
            photos=photo_payloads,
            signature=(signature.filename or "signature.png", sig_type, sig_content),
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/{accurate_sj_id}/failed")
def post_failed(
    accurate_sj_id: str,
    body: DeliveryFailureRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return delivery_service.confirm_delivery_failure(
            get_db(), auth.user, accurate_sj_id=accurate_sj_id, reason=body.reason
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/evidence/soft-delete")
def post_evidence_soft_delete(
    body: EvidenceDeleteRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return delivery_service.soft_delete_delivery_evidence(
            get_db(), auth.user, evidence_id=body.evidence_id, reason=body.reason
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc
