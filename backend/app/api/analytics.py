"""API Checkpoint F — Buffer Stock, Alur Produksi, Dashboard, Laporan."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.auth.permissions import PERM_VIEW_REPORTS, has_permission
from app.db import get_db
from app.repositories.po_repository import get_by_accurate_id, po_collection
from app.services import read_models
from app.services.po_acceptance import is_po_accepted

router = APIRouter(prefix="/api", tags=["analytics"])


def _branch_scope(auth: AuthContext) -> tuple[list[str] | None, bool]:
    from app.services.object_auth import bypasses_branch, user_branch_ids

    elevated = bypasses_branch(auth.user)
    scope_ids = None if elevated else user_branch_ids(auth.user)
    return scope_ids, not elevated


@router.get("/buffer-stock")
def get_buffer_stock(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    """Monitoring Buffer Stock — scoped cabang; tidak mengubah stok fisik."""
    branch_ids, branch_scope = _branch_scope(auth)
    return read_models.buffer_stock_rows(
        get_db(),
        branch_ids=branch_ids,
        branch_scope=branch_scope,
    )


@router.get("/production-flow")
def get_production_flow(
    accurate_po_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Alur Produksi — hanya PO accepted. By-id unaccepted → 404."""
    db = get_db()
    branch_ids, branch_scope = _branch_scope(auth)

    if accurate_po_id:
        po_doc = get_by_accurate_id(db, accurate_po_id)
        if po_doc is None or not is_po_accepted(po_doc):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "PO Accurate tidak ditemukan",
            )
        if branch_scope:
            from app.services.object_auth import ObjectAuthError, assert_branch_access

            try:
                assert_branch_access(auth.user, scope_object=po_doc)
            except ObjectAuthError as exc:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    "PO Accurate tidak ditemukan",
                ) from exc
        return {"items": [read_models.production_flow_state(db, po_doc)]}

    query = read_models.accepted_po_query(
        branch_ids=branch_ids,
        branch_scope=branch_scope,
        q=q,
    )
    docs = list(
        po_collection(db)
        .find(query, {"_id": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )
    return {
        "items": [read_models.production_flow_state(db, doc) for doc in docs],
        "nodes": read_models.production_flow_stage_nodes(),
        "source": "mongodb",
        "accepted_only": True,
    }


@router.get("/production-flow/stage-counts")
def get_production_flow_stage_counts(
    q: str | None = Query(default=None),
    urgent: bool | None = Query(default=None),
    accurate_po_id: str | None = Query(default=None),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Badge Alur Produksi — PO unik per tahap aktif (satu scan accepted)."""
    db = get_db()
    branch_ids, branch_scope = _branch_scope(auth)
    return read_models.list_production_flow_stage_counts(
        db,
        q=q,
        urgent=urgent,
        accurate_po_id=accurate_po_id,
        branch_ids=branch_ids,
        branch_scope=branch_scope,
    )


@router.get("/production-flow/by-stage")
def get_production_flow_by_stage(
    node_id: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100, alias="pageSize"),
    q: str | None = Query(default=None),
    urgent: bool | None = Query(default=None),
    accurate_po_id: str | None = Query(default=None),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Monitoring PO per tahap aktif — accepted only, pagination server-side.

    ``node_id=flow-all`` = seluruh PO accepted unik (bukan membership tahap).
    """
    from app.api.po import _serialize_po_doc

    db = get_db()
    branch_ids, branch_scope = _branch_scope(auth)
    try:
        return read_models.list_production_flow_by_stage(
            db,
            node_id=node_id,
            page=page,
            page_size=page_size,
            q=q,
            urgent=urgent,
            accurate_po_id=accurate_po_id,
            branch_ids=branch_ids,
            branch_scope=branch_scope,
            serialize_po=_serialize_po_doc,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/dashboard")
def get_dashboard(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    return read_models.dashboard_metrics(get_db())


@router.get("/reports/{kind}")
def get_report(
    kind: str,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    accurate_po_id: str | None = Query(default=None),
    report_status: str | None = Query(default=None, alias="status"),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    if not has_permission(auth.user, PERM_VIEW_REPORTS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses laporan ditolak")
    try:
        return read_models.report(
            get_db(),
            kind,
            date_from=date_from,
            date_to=date_to,
            actor=actor,
            accurate_po_id=accurate_po_id,
            status=report_status,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/reports")
def list_report_kinds(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    return {"kinds": list(read_models.REPORT_KINDS)}


def _require_finance_reports(auth: AuthContext) -> None:
    """Finance invoice classification — reports.view only; no silent grants."""
    if not has_permission(auth.user, PERM_VIEW_REPORTS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Akses laporan ditolak")


@router.get("/finance/invoices")
def list_finance_invoices(
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    finance_class: str | None = Query(default=None),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """M9.3C — derived finance classification read-model (branch ACL).

    Additive RO view over Accurate invoice snapshots. Does not resolve reviews,
    open Payment Gate, or invent SO/PO relationships.
    """
    _require_finance_reports(auth)
    from app.repositories import invoice_repository as invoices
    from app.services.invoice_finance_classification import finance_invoice_view
    from app.services.object_auth import ObjectAuthError, assert_branch_access

    branch_ids, branch_scope = _branch_scope(auth)
    db = get_db()
    # Fetch a bounded window then classify; branch_scope filters missing branch_id.
    rows = invoices.list_invoices(
        db,
        branch_ids=branch_ids,
        branch_scope=branch_scope,
        limit=limit,
        skip=skip,
    )
    items: list[dict[str, Any]] = []
    for doc in rows:
        view = finance_invoice_view(doc, db=db)
        if finance_class and view.get("finance_class") != finance_class:
            continue
        if branch_scope:
            # Defense in depth: re-assert object branch for each row.
            try:
                assert_branch_access(auth.user, scope_object=doc)
            except ObjectAuthError:
                continue
        items.append(view)
    return {
        "items": items,
        "count": len(items),
        "source": "accurate_sales_invoices",
        "derived": True,
        "payment_gate_unchanged": True,
        "payment_allocation": False,
    }


@router.get("/finance/invoices/{accurate_invoice_id}")
def get_finance_invoice(
    accurate_invoice_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Single-invoice derived finance classification — fail-closed on branch."""
    _require_finance_reports(auth)
    from app.repositories import invoice_repository as invoices
    from app.services.invoice_finance_classification import finance_invoice_view
    from app.services.object_auth import ObjectAuthError, assert_branch_access

    db = get_db()
    doc = invoices.get_invoice(db, accurate_invoice_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktur tidak ditemukan")

    _branch_ids, branch_scope = _branch_scope(auth)
    if branch_scope:
        # Missing branch identity → deny cross-branch exposure.
        if not doc.get("branch_id"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktur tidak ditemukan")
        try:
            assert_branch_access(auth.user, scope_object=doc)
        except ObjectAuthError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Faktur tidak ditemukan"
            ) from exc

    return finance_invoice_view(doc, db=db)


class AdminInvoiceReviewRequest(BaseModel):
    outcome: str = Field(min_length=3, max_length=64)
    reason: str = Field(min_length=0, max_length=2000)
    evidence_ref: str | None = Field(default=None, max_length=500)
    expected_source_fingerprint: str = Field(min_length=16, max_length=128)
    expected_admin_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.get("/finance/invoice-reviews")
def list_finance_invoice_reviews(
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    source_completeness: str | None = Query(default=None),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """M9.3E — administrative review queue (branch ACL, reports.view)."""
    _require_finance_reports(auth)
    from app.services.invoice_admin_review_service import list_admin_review_queue

    branch_ids, branch_scope = _branch_scope(auth)
    return list_admin_review_queue(
        get_db(),
        auth.user,
        branch_ids=branch_ids,
        branch_scope=branch_scope,
        limit=limit,
        skip=skip,
        source_completeness=source_completeness,
    )


@router.get("/finance/invoice-reviews/{accurate_invoice_id}")
def get_finance_invoice_review(
    accurate_invoice_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Administrative review detail + append-only history."""
    _require_finance_reports(auth)
    from app.services.invoice_admin_review_service import (
        AdminReviewPermissionError,
        get_admin_review_detail,
    )

    try:
        return get_admin_review_detail(get_db(), auth.user, accurate_invoice_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AdminReviewPermissionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktur tidak ditemukan") from exc


@router.get("/finance/invoice-reviews/{accurate_invoice_id}/history")
def get_finance_invoice_review_history(
    accurate_invoice_id: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Append-only administrative events for one invoice."""
    _require_finance_reports(auth)
    from app.repositories import invoice_admin_review_repository as admin_reviews
    from app.services.invoice_admin_review_service import (
        AdminReviewPermissionError,
        get_admin_review_detail,
    )

    try:
        detail = get_admin_review_detail(get_db(), auth.user, accurate_invoice_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AdminReviewPermissionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktur tidak ditemukan") from exc
    events = admin_reviews.list_events(get_db(), accurate_invoice_id)
    return {
        "accurate_invoice_id": accurate_invoice_id,
        "events": events,
        "count": len(events),
        "admin_review_status": detail.get("admin_review_status"),
        "admin_decision_stale": detail.get("admin_decision_stale"),
    }


@router.post("/finance/invoice-reviews/{accurate_invoice_id}")
def post_finance_invoice_review(
    accurate_invoice_id: str,
    body: AdminInvoiceReviewRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Submit administrative decision — never opens Payment Gate."""
    _require_finance_reports(auth)
    from app.services.invoice_admin_review_service import (
        AdminReviewConflictError,
        AdminReviewPermissionError,
        AdminReviewValidationError,
        submit_admin_review,
    )

    try:
        return submit_admin_review(
            get_db(),
            auth.user,
            accurate_invoice_id=accurate_invoice_id,
            outcome=body.outcome,
            reason=body.reason,
            evidence_ref=body.evidence_ref,
            expected_source_fingerprint=body.expected_source_fingerprint,
            expected_admin_version=body.expected_admin_version,
            idempotency_key=body.idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AdminReviewPermissionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Faktur tidak ditemukan") from exc
    except AdminReviewValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except AdminReviewConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
