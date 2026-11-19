"""API Checkpoint F — Buffer Stock, Alur Produksi, Dashboard, Laporan."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.deps import AuthContext, require_session
from app.auth.permissions import PERM_VIEW_REPORTS, has_permission
from app.db import get_db
from app.repositories.po_repository import get_by_accurate_id, po_collection
from app.services import read_models

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/buffer-stock")
def get_buffer_stock(auth: AuthContext = Depends(require_session)) -> dict[str, Any]:
    return read_models.buffer_stock_rows(get_db())


@router.get("/production-flow")
def get_production_flow(
    accurate_po_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    db = get_db()
    if accurate_po_id:
        po_doc = get_by_accurate_id(db, accurate_po_id)
        if po_doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "PO Accurate tidak ditemukan")
        return {"items": [read_models.production_flow_state(db, po_doc)]}

    docs = list(po_collection(db).find({"origin": "accurate"}, {"_id": 0}).limit(limit))
    return {
        "items": [read_models.production_flow_state(db, doc) for doc in docs],
        "nodes": [
            {"id": node_id, "label": label}
            for node_id, label in read_models.PRODUCTION_FLOW_NODES
        ],
        "source": "mongodb",
    }


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
