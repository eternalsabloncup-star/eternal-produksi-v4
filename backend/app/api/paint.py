"""API Manajemen Cat — masters, stock, withdrawals, returns, conversion, waste,
stocktake, reports, Accurate dark diagnostics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.db import get_db
from app.repositories import paint_repository as paint
from app.services import paint_accurate_adapter as accurate_paint
from app.services import paint_service as svc
from app.services.object_auth import ObjectAuthError

router = APIRouter(prefix="/api/paint", tags=["paint"])


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.PaintPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ObjectAuthError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, svc.PaintNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(
        exc,
        (
            svc.PaintValidationError,
            paint.PaintQuantityError,
            paint.PaintConflictError,
            ValueError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Kesalahan server.")


class PaintUpsertBody(BaseModel):
    brand: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=120)
    color: str = Field(min_length=1, max_length=120)
    is_black: bool = False
    is_active: bool = True
    paint_id: str | None = None


class PurposeUpsertBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    requires_po: bool = False
    is_active: bool = True
    sort_order: int = 100
    purpose_id: str | None = None


class InboundBody(BaseModel):
    branch_id: str
    paint_id: str
    qty_g: float = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=200)
    supplier: str | None = None
    accurate_ref: str | None = None
    received_by: str | None = None
    note: str | None = None
    physical_confirmed: bool = False


class WithdrawItem(BaseModel):
    paint_id: str
    qty_g: float = Field(gt=0)


class WithdrawBody(BaseModel):
    branch_id: str
    used_by_email: str
    purpose_id: str
    items: list[WithdrawItem] = Field(min_length=1)
    idempotency_key: str = Field(min_length=8, max_length=200)
    accurate_po_id: str | None = None
    note: str | None = None


class ReturnBody(BaseModel):
    withdrawal_id: str
    withdrawal_line_id: str
    qty_g: float = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=200)
    condition: str = "fit"
    note: str | None = None


class ConversionSource(BaseModel):
    withdrawal_id: str
    withdrawal_line_id: str
    qty_g: float = Field(gt=0)


class ConversionBody(BaseModel):
    branch_id: str
    destination_paint_id: str
    source_lines: list[ConversionSource] = Field(min_length=1)
    verified_return_g: float = Field(gt=0)
    waste_g: float = 0
    idempotency_key: str = Field(min_length=8, max_length=200)
    note: str | None = None


class WasteBody(BaseModel):
    withdrawal_id: str
    withdrawal_line_id: str
    qty_g: float = Field(gt=0)
    reason: str = Field(min_length=2, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=200)


class SoftDeleteBody(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=200)


class StocktakeStartBody(BaseModel):
    branch_id: str
    counts: list[dict[str, Any]] = Field(default_factory=list)


class StocktakeFinalizeBody(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    counts: list[dict[str, Any]] = Field(min_length=1)


class AccurateMapBody(BaseModel):
    accurate_item_code: str
    paint_id: str


@router.get("/ready")
def paint_ready(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    ready = accurate_paint.readiness()
    return {
        "module": "paint",
        "accurate": {
            "status": ready.status,
            "detail": ready.detail,
            "adapter_enabled": ready.adapter_enabled,
            "ui_banner": "Integrasi Accurate Belum Aktif"
            if ready.status != "ENABLED"
            else None,
        },
        "permissions": sorted(
            p for p in svc.PAINT_PERMISSIONS if svc.has_paint_permission(auth.user, p)
        ),
    }


@router.get("/catalog")
def get_catalog(
    active_only: bool = False,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        svc.require_paint_permission(auth.user, svc.PERM_PAINT_VIEW)
        items = svc.list_catalog(get_db(), active_only=active_only)
        return {"items": items, "total": len(items)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/catalog")
def post_catalog(
    body: PaintUpsertBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        doc = svc.upsert_paint(get_db(), auth.user, **body.model_dump())
        return {"item": doc}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/purposes")
def get_purposes(
    active_only: bool = False,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        svc.require_paint_permission(auth.user, svc.PERM_PAINT_VIEW)
        items = svc.list_purposes(get_db(), active_only=active_only)
        return {"items": items, "total": len(items)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/purposes")
def post_purposes(
    body: PurposeUpsertBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        doc = svc.upsert_purpose(get_db(), auth.user, **body.model_dump())
        return {"item": doc}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/stock")
def get_stock(
    branch_id: str | None = None,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        items = svc.list_stock(get_db(), auth.user, branch_id=branch_id)
        return {"items": items, "total": len(items)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/ledger")
def get_ledger(
    branch_id: str,
    paint_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        items = svc.list_ledger(
            get_db(), auth.user, branch_id=branch_id, paint_id=paint_id, limit=limit
        )
        return {"items": items, "total": len(items)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/inbound")
def post_inbound(
    body: InboundBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return svc.confirm_inbound(get_db(), auth.user, **body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/withdrawals")
def post_withdrawal(
    body: WithdrawBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        payload = body.model_dump()
        payload["items"] = [i for i in payload["items"]]
        return svc.create_withdrawal(get_db(), auth.user, **payload)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/returns")
def post_return(
    body: ReturnBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return svc.confirm_return(get_db(), auth.user, **body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/conversions")
def post_conversion(
    body: ConversionBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return svc.confirm_conversion_to_black(get_db(), auth.user, **body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/waste")
def post_waste(
    body: WasteBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return svc.record_waste(get_db(), auth.user, **body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/documents/{document_id}/soft-delete")
def post_soft_delete(
    document_id: str,
    body: SoftDeleteBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return svc.soft_delete_document(
            get_db(),
            auth.user,
            document_id=document_id,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/documents/{document_id}/correct")
def post_correct(
    document_id: str,
    body: SoftDeleteBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return svc.correct_document(
            get_db(),
            auth.user,
            document_id=document_id,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/documents")
def get_documents(
    branch_id: str | None = None,
    doc_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = None,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        return svc.list_documents(
            get_db(),
            auth.user,
            branch_id=branch_id,
            doc_type=doc_type,
            limit=limit,
            offset=offset,
            q=q,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/documents/{document_id}/outstanding")
def get_withdrawal_outstanding(
    document_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        return svc.withdrawal_outstanding(
            get_db(), auth.user, withdrawal_id=document_id
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/summary")
def get_paint_summary(
    branch_id: str | None = None,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        return svc.paint_kpi_summary(get_db(), auth.user, branch_id=branch_id)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/users")
def get_paint_users(
    branch_id: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        return svc.list_paint_users(
            get_db(), auth.user, branch_id=branch_id, q=q, limit=limit
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/reports/{kind}")
def get_report(
    kind: str,
    branch_id: str | None = None,
    used_by: str | None = None,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    try:
        return svc.paint_report(
            get_db(),
            auth.user,
            kind=kind,
            branch_id=branch_id,
            used_by=used_by,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/stocktakes/preview")
def post_stocktake_preview(
    body: StocktakeStartBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Read-only dry run — no stocktake/audit/ledger writes."""
    try:
        return {
            "stocktake": svc.preview_stocktake(
                get_db(), auth.user, branch_id=body.branch_id, counts=body.counts
            )
        }
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/stocktakes")
def post_stocktake_session(
    body: StocktakeStartBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Explicit session create — persists authorized snapshot for finalize."""
    try:
        return {
            "stocktake": svc.create_stocktake_session(
                get_db(), auth.user, branch_id=body.branch_id, counts=body.counts
            )
        }
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/stocktakes/{stocktake_id}/finalize")
def post_stocktake_finalize(
    stocktake_id: str,
    body: StocktakeFinalizeBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        return svc.finalize_stocktake(
            get_db(),
            auth.user,
            stocktake_id=stocktake_id,
            idempotency_key=body.idempotency_key,
            counts=body.counts,
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/accurate/status")
def get_accurate_status(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    ready = accurate_paint.readiness()
    return {
        "status": ready.status,
        "detail": ready.detail,
        "adapter_enabled": ready.adapter_enabled,
        "banner": "Integrasi Accurate Belum Aktif",
    }


@router.get("/accurate/mappings")
def get_accurate_mappings(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    try:
        svc.require_paint_permission(auth.user, svc.PERM_PAINT_ACCURATE_MAP)
        items = accurate_paint.list_mappings(get_db())
        return {"items": items, "total": len(items)}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/accurate/mappings")
def post_accurate_mapping(
    body: AccurateMapBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        item = accurate_paint.upsert_mapping(get_db(), auth.user, **body.model_dump())
        return {"item": item}
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc
