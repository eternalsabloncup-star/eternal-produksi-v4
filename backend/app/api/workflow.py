"""API Checkpoint B: Design, Payment/Gate, dan derivasi tugas PO."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.auth.permissions import PERM_OPERATIONS_FULL, has_permission
from app.db import get_db
from app.repositories import design_repository as design
from app.repositories import evidence_repository as evidence
from app.repositories import gate_repository as gates
from app.repositories import payment_repository as payments
from app.repositories.po_repository import get_by_accurate_id
from app.services import design_service, payment_service, po_tasks
from app.services.evidence_access import (
    EvidenceAccessDenied,
    assert_design_evidence_access,
)
from app.services.object_auth import ObjectAuthError, require_po_branch_access
from app.services.task_engine import TaskPermissionError

router = APIRouter(prefix="/api", tags=["workflow"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class ConfirmDesignRequest(BaseModel):
    version: int = Field(ge=1)


class ApproveDesignRequest(BaseModel):
    signer_name: str = Field(min_length=1, max_length=120)
    signature_png_base64: str = Field(min_length=16)


class ReceiptIn(BaseModel):
    id: str = Field(min_length=1)
    invoice_no: str | None = None
    amount: float
    receipt_date: str | None = None


class IngestReceiptsRequest(BaseModel):
    receipts: list[ReceiptIn] = Field(default_factory=list)


class BypassPaymentRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _po_or_404(accurate_po_id: str) -> dict[str, Any]:
    db = get_db()
    doc = get_by_accurate_id(db, accurate_po_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO Accurate tidak ditemukan")
    return doc


def _require_operations(auth: AuthContext) -> None:
    if not has_permission(auth.user, PERM_OPERATIONS_FULL):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses ditolak")


def _require_po_accepted(po_doc: dict[str, Any]) -> None:
    """Gate Terima PO — workflow desain/pembayaran hanya setelah accepted."""
    from app.services.po_acceptance import PoAcceptanceError, require_po_accepted

    try:
        require_po_accepted(po_doc)
    except PoAcceptanceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


# Prefix terpisah dari /api/po agar tidak tertelan route katalog PO.
@router.post("/workflow/{accurate_po_id}/derive-tasks")
def post_derive_tasks(
    accurate_po_id: str,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_operations(auth)
    db = get_db()
    po_doc = _po_or_404(accurate_po_id)
    from app.services.po_acceptance import PoAcceptanceError

    try:
        return po_tasks.derive_tasks_for_po(db, po_doc, require_accepted=True)
    except PoAcceptanceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/workflow/{accurate_po_id}/gates")
def get_po_gates(
    accurate_po_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    try:
        require_po_branch_access(db, auth.user, accurate_po_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return {
        "gates": gates.get_gates(db, accurate_po_id),
        "history": gates.gate_history(db, accurate_po_id),
        "scheduling_unlocked": gates.both_gates_open(db, accurate_po_id),
    }


@router.get("/design/{accurate_po_id}")
def get_design_state(
    accurate_po_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    try:
        require_po_branch_access(db, auth.user, accurate_po_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    latest = design.latest_submission(db, accurate_po_id)
    approval = None
    if latest:
        approval = design.get_approval(
            db, accurate_po_id=accurate_po_id, design_version=int(latest["version"])
        )
    return {
        "accurate_po_id": accurate_po_id,
        "submissions": design.list_submissions(db, accurate_po_id),
        "latest": latest,
        "approval": approval,
        "gates": gates.get_gates(db, accurate_po_id),
    }


@router.get("/design/{accurate_po_id}/evidence/{evidence_id}/content")
def get_design_evidence_content(
    accurate_po_id: str,
    evidence_id: str,
    auth: AuthContext = Depends(require_session),
) -> StreamingResponse:
    """Read-only stream bukti desain PO — sesi wajib; tidak membuka Gate/approval."""
    db = get_db()
    po_doc = get_by_accurate_id(db, accurate_po_id)
    if po_doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO Accurate tidak ditemukan")
    try:
        assert_design_evidence_access(db, auth.user, po_doc=po_doc)
    except EvidenceAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    submissions = design.list_submissions(db, accurate_po_id)
    allowed_ids = {
        str(s.get("evidence_id"))
        for s in submissions
        if isinstance(s, dict) and s.get("evidence_id")
    }
    if evidence_id not in allowed_ids:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Bukti desain tidak terkait PO ini.",
        )
    meta = evidence.get_evidence(db, evidence_id)
    if meta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bukti tidak ditemukan")
    if meta.get("deleted_at") is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bukti sudah dihapus")
    if str(meta.get("scope")) != design_service.DESIGN_SCOPE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bukti bukan file desain.")
    if str(meta.get("scope_id")) != str(accurate_po_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Bukti di luar scope PO yang diminta.",
        )
    try:
        _doc, stream = evidence.open_evidence_stream(db, evidence_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    filename = str(meta.get("filename") or "design.bin")
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


@router.post("/design/{accurate_po_id}/submission")
async def post_design_submission(
    accurate_po_id: str,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    po_doc = _po_or_404(accurate_po_id)
    _require_po_accepted(po_doc)
    if not po_tasks.has_print_items(po_doc):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "PO tanpa item Cetak tidak memerlukan desain.",
        )
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File terlalu besar")
    db = get_db()
    try:
        return design_service.submit_design(
            db,
            auth.user,
            accurate_po_id=accurate_po_id,
            po_no=str(po_doc.get("po_no") or ""),
            filename=file.filename or "design.bin",
            content_type=file.content_type or "application/octet-stream",
            content=content,
        )
    except TaskPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/design/{accurate_po_id}/confirm")
def post_design_confirm(
    accurate_po_id: str,
    body: ConfirmDesignRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_po_accepted(_po_or_404(accurate_po_id))
    db = get_db()
    try:
        return design_service.confirm_design(
            db, auth.user, accurate_po_id=accurate_po_id, version=body.version
        )
    except TaskPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/design/{accurate_po_id}/approval-link")
def post_design_approval_link(
    accurate_po_id: str,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_po_accepted(_po_or_404(accurate_po_id))
    db = get_db()
    try:
        return design_service.create_customer_link(
            db, auth.user, accurate_po_id=accurate_po_id
        )
    except TaskPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except design_service.DesignStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/public/design-approval/{token}")
def get_public_design_approval(token: str) -> dict[str, Any]:
    """Halaman customer — scope satu PO, tanpa sesi internal V4."""
    db = get_db()
    link = design.find_valid_link(db, token)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link tidak valid")
    po_doc = get_by_accurate_id(db, str(link["accurate_po_id"]))
    try:
        return design_service.public_approval_context(db, token, po_doc)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/public/design-approval/{token}/approve")
def post_public_design_approval(
    token: str, body: ApproveDesignRequest
) -> dict[str, Any]:
    db = get_db()
    try:
        signature = base64.b64decode(body.signature_png_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Tanda tangan tidak valid"
        ) from exc
    if len(signature) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Tanda tangan terlalu besar")
    try:
        return design_service.approve_design(
            db,
            raw_token=token,
            signer_name=body.signer_name,
            signature_png=signature,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/payment/{accurate_po_id}")
def get_payment_state(
    accurate_po_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    try:
        require_po_branch_access(db, auth.user, accurate_po_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return {
        "accurate_po_id": accurate_po_id,
        "receipts": payments.list_receipts(db, accurate_po_id),
        "reconciliation": payments.reconcile_po_payment(db, accurate_po_id),
        "gates": gates.get_gates(db, accurate_po_id),
    }


@router.post("/payment/{accurate_po_id}/receipts")
def post_payment_receipts(
    accurate_po_id: str,
    body: IngestReceiptsRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    _require_operations(auth)
    po_doc = _po_or_404(accurate_po_id)
    _require_po_accepted(po_doc)
    db = get_db()
    return payment_service.ingest_receipts(
        db,
        auth.user,
        accurate_po_id=accurate_po_id,
        po_no=str(po_doc.get("po_no") or ""),
        receipts=[r.model_dump() for r in body.receipts],
    )


@router.post("/payment/{accurate_po_id}/bypass")
def post_payment_bypass(
    accurate_po_id: str,
    body: BypassPaymentRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    db = get_db()
    try:
        po_doc = require_po_branch_access(db, auth.user, accurate_po_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    _require_po_accepted(po_doc)
    try:
        return payment_service.bypass_payment_gate(
            db, auth.user, accurate_po_id=accurate_po_id, reason=body.reason
        )
    except payment_service.PaymentPermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except payment_service.PaymentGateConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/evidence/{scope}/{scope_id}")
def get_evidence_list(
    scope: str,
    scope_id: str,
    include_deleted: bool = False,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    from app.services.object_auth import (
        bypasses_branch,
        require_allocation_branch_access,
        require_sj_branch_access,
    )

    db = get_db()
    try:
        scope_l = str(scope or "").lower()
        if scope_l in {"design", "payment"} or scope_l.startswith("design"):
            require_po_branch_access(db, auth.user, scope_id)
        elif scope_l in {"sample", "konfirmasi-sample"} or "sample" in scope_l:
            require_allocation_branch_access(db, auth.user, scope_id)
        elif (
            scope_l in {"delivery", "delivery-photo", "delivery-signature"}
            or "delivery" in scope_l
        ):
            require_sj_branch_access(db, auth.user, scope_id)
        elif not bypasses_branch(auth.user):
            raise ObjectAuthError(
                "Scope evidence tidak dapat divalidasi — akses ditolak."
            )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return {
        "items": evidence.list_evidence(
            db, scope=scope, scope_id=scope_id, include_deleted=include_deleted
        )
    }
