"""API PO / Sales Order — baca Mongo V4 + sync payload uji (bukan write Accurate)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.config import get_settings
from app.db import get_db
from app.repositories.po_repository import (
    get_by_accurate_id,
    get_by_po_no,
    list_sales_orders,
)
from app.services.po_sync import sync_sales_order_payload

router = APIRouter(prefix="/api/po", tags=["po"])


class SyncPayloadRequest(BaseModel):
    """Body sync: objek Sales Order Accurate mentah."""

    sales_order: dict[str, Any] = Field(default_factory=dict)


def _serialize_po_doc(row: dict[str, Any]) -> dict[str, Any]:
    """Dokumen Mongo → JSON Inbox (header+items lengkap, tanpa _id)."""
    doc = dict(row)
    doc.pop("_id", None)
    header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    items = header.get("items") if isinstance(header.get("items"), list) else []
    return {
        "accurate_id": doc.get("accurate_id"),
        "source_po_id": doc.get("source_po_id"),
        "po_no": doc.get("po_no"),
        "origin": doc.get("origin"),
        "customer": header.get("customer"),
        "item_count": len(items),
        "updated_at": doc.get("updated_at"),
        "created_at": doc.get("created_at"),
        "header": header,
        "line_ids": doc.get("line_ids") or [],
        "operational": doc.get("operational")
        if isinstance(doc.get("operational"), dict)
        else {},
        "last_line_diff": doc.get("last_line_diff"),
    }


@router.get("")
def list_po(
    auth: AuthContext = Depends(require_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500, alias="pageSize"),
    q: str | None = None,
    urgent: bool | None = None,
    sort_by: Literal[
        "updated_at", "po_no", "po_date", "customer", "item_count"
    ] = Query("updated_at", alias="sortBy"),
    sort_dir: Literal["asc", "desc"] = Query("desc", alias="sortDir"),
) -> dict[str, Any]:
    """List PO Accurate dari Mongo V4 — tidak memanggil Accurate Online."""
    del auth
    db = get_db()
    direction = -1 if sort_dir == "desc" else 1
    skip = (page - 1) * page_size
    rows, total = list_sales_orders(
        db,
        origin="accurate",
        limit=page_size,
        skip=skip,
        q=q,
        urgent=urgent,
        sort_by=sort_by,
        sort_dir=direction,
    )
    items = [_serialize_po_doc(row) for row in rows]
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "source": "mongodb",
        "origin": "accurate",
    }


@router.get("/{po_key:path}")
def get_po(
    po_key: str,
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """Detail PO dari Mongo by accurate_id atau po_no.

    ``:path`` agar nomor PO ber-slash (mis. PO/18/11/2026/000010) tetap
    tertangkap — bukan request Accurate live.
    """
    del auth
    db = get_db()
    key = po_key.strip().strip("/")
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO tidak ditemukan")
    doc = get_by_accurate_id(db, key)
    if not doc:
        doc = get_by_po_no(db, key)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO tidak ditemukan")
    return _serialize_po_doc(doc)


@router.post("/sync-payload")
def sync_payload(
    body: SyncPayloadRequest,
    auth: AuthContext = Depends(require_session),
    _: None = Depends(require_csrf),
) -> dict[str, Any]:
    """Upsert dari payload JSON (uji/integrasi). Tidak memanggil Accurate write.

    Diblokir di production kecuali APP_ENV != production — cegah tulis tak terkendali.
    """
    del auth
    settings = get_settings()
    if settings.app_env == "production":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "sync-payload dinonaktifkan di production tanpa keputusan Owner.",
        )
    if settings.mongodb_db == "eternal_v4" and settings.app_env != "test":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Penulisan ke database eternal_v4 diblokir tanpa persetujuan Owner. "
            "Gunakan DB_NAME test terisolasi.",
        )
    if not body.sales_order:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "sales_order wajib.")

    result = sync_sales_order_payload(get_db(), body.sales_order)
    po = result["po"]
    return {
        "accurate_id": result["accurate_id"],
        "po_no": result["po_no"],
        "line_count": result["line_count"],
        "was_insert": result["was_insert"],
        "duplicate_prevented": result["duplicate_prevented"],
        "count_after": result["count_after"],
        "process_kinds": [i.process_kind for i in po.items],
        "colors": [i.color for i in po.items],
    }
