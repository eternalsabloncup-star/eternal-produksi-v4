"""API PO / Sales Order — baca Mongo V4 + sync payload uji (bukan write Accurate)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, require_csrf, require_session
from app.config import get_settings
from app.db import get_db
from app.repositories.po_repository import (
    aggregate_inbox_metrics,
    get_by_accurate_id,
    get_by_po_no,
    list_sales_orders,
)
from app.services.po_sync import sync_sales_order_payload

router = APIRouter(prefix="/api/po", tags=["po"])


class SyncPayloadRequest(BaseModel):
    """Body sync: objek Sales Order Accurate mentah."""

    sales_order: dict[str, Any] = Field(default_factory=dict)


def _label_for_api(value: Any) -> Any:
    """Pastikan field tampilan bukan dict mentah / str(dict)."""
    from app.integrations.accurate.normalize import _business_label

    if value is None:
        return None
    labeled = _business_label(value)
    if isinstance(value, (dict, list, tuple)):
        return labeled or None
    if isinstance(value, str) and value.strip().startswith("{"):
        return labeled or None
    return labeled if labeled != "" else value


def _sanitize_header_for_api(header: dict[str, Any]) -> dict[str, Any]:
    out = dict(header)
    for key in (
        "customer",
        "payment_terms",
        "expedition",
        "seller",
        "ship_zone",
        "recipient",
        "address",
        "user_name",
        "buffer_label",
        "notes",
        "po_date",
        "eta_ship",
        "address_pic_name",
        "print_user_name",
        "created_by_id",
        "priority_label",
    ):
        if key in out:
            out[key] = _label_for_api(out.get(key))
    items = out.get("items")
    if isinstance(items, list):
        cleaned_items = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            for ikey in ("item_code", "item_name", "unit", "color", "process_raw"):
                if ikey in item and isinstance(item.get(ikey), (dict, list)):
                    item[ikey] = _label_for_api(item.get(ikey))
            cleaned_items.append(item)
        out["items"] = cleaned_items
    return out


def _payment_gate_slice(gate_doc: dict[str, Any] | None) -> dict[str, Any]:
    payment = (gate_doc or {}).get("payment") if isinstance(gate_doc, dict) else None
    if not isinstance(payment, dict):
        payment = {}
    detail = payment.get("detail") if isinstance(payment.get("detail"), dict) else {}
    return {
        "state": payment.get("state") or "closed",
        "bypass": bool(detail.get("bypass")),
        "reason": payment.get("reason"),
    }


def _serialize_po_doc(
    row: dict[str, Any],
    db=None,
    *,
    payment_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dokumen Mongo → JSON Inbox (header+items lengkap, tanpa _id)."""
    from app.repositories import org_master_repository as org
    from app.services.branch_identity import branch_display_name
    from app.services.object_auth import object_branch_code

    doc = dict(row)
    doc.pop("_id", None)
    header_raw = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    header = _sanitize_header_for_api(header_raw)
    items = header.get("items") if isinstance(header.get("items"), list) else []
    customer = _label_for_api(header.get("customer"))
    branch_id = object_branch_code(doc)
    master = org.get_branch(db, branch_id) if db is not None and branch_id else None
    branch_info = branch_display_name(master, branch_id=branch_id)
    gate_slice = payment_gate
    if gate_slice is None and db is not None and doc.get("accurate_id"):
        from app.repositories import gate_repository as gates

        gate_slice = _payment_gate_slice(
            gates.get_gates(db, str(doc.get("accurate_id")))
        )
    return {
        "accurate_id": doc.get("accurate_id"),
        "source_po_id": doc.get("source_po_id"),
        "po_no": doc.get("po_no"),
        "origin": doc.get("origin"),
        "customer": customer,
        "branch_id": branch_info.get("branch_id"),
        "branch_name": branch_info.get("branch_name"),
        "branch_name_verified": bool(branch_info.get("branch_name_verified")),
        "item_count": len(items),
        "updated_at": doc.get("updated_at"),
        "created_at": doc.get("created_at"),
        "header": header,
        "line_ids": doc.get("line_ids") or [],
        "operational": doc.get("operational")
        if isinstance(doc.get("operational"), dict)
        else {},
        "acceptance": _acceptance_for_api(doc),
        "payment_gate": gate_slice
        if gate_slice is not None
        else {"state": "closed", "bypass": False, "reason": None},
        "last_line_diff": doc.get("last_line_diff"),
    }


def _acceptance_for_api(doc: dict[str, Any]) -> dict[str, Any]:
    from app.services.po_acceptance import acceptance_block

    block = acceptance_block(doc)
    accepted_at = block.get("accepted_at")
    if hasattr(accepted_at, "isoformat"):
        block = {**block, "accepted_at": accepted_at.isoformat()}
    return block


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
    from app.services.object_auth import bypasses_branch, user_branch_ids

    db = get_db()
    direction = -1 if sort_dir == "desc" else 1
    skip = (page - 1) * page_size
    elevated = bypasses_branch(auth.user)
    scope_ids = None if elevated else user_branch_ids(auth.user)
    rows, total = list_sales_orders(
        db,
        origin="accurate",
        limit=page_size,
        skip=skip,
        q=q,
        urgent=urgent,
        sort_by=sort_by,
        sort_dir=direction,
        branch_ids=scope_ids,
        branch_scope=not elevated,
    )
    from app.repositories import gate_repository as gates

    gate_map = gates.get_gates_many(
        db,
        [str(r.get("accurate_id") or "") for r in rows if r.get("accurate_id")],
    )
    items = [
        _serialize_po_doc(
            row,
            db,
            payment_gate=_payment_gate_slice(
                gate_map.get(str(row.get("accurate_id") or ""))
            ),
        )
        for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "source": "mongodb",
        "origin": "accurate",
        "supportedFilters": ["q", "urgent"],
    }


@router.get("/metrics")
def po_metrics(
    auth: AuthContext = Depends(require_session),
) -> dict[str, Any]:
    """KPI Inbox Accurate — agregasi scoped; akun terbatas tidak lihat angka global."""
    from app.services.object_auth import bypasses_branch, user_branch_ids

    elevated = bypasses_branch(auth.user)
    scope_ids = None if elevated else user_branch_ids(auth.user)
    metrics = aggregate_inbox_metrics(
        get_db(),
        origin="accurate",
        branch_ids=scope_ids,
        branch_scope=not elevated,
    )
    return {
        **metrics,
        "source": "mongodb",
        "origin": "accurate",
    }


@router.post("/{accurate_po_id}/accept")
def accept_po(
    accurate_po_id: str,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Kepala Produksi menerima PO → aktifkan workflow Eternal (idempotent)."""
    from app.services import po_acceptance
    from app.services.object_auth import ObjectAuthError
    from app.services.task_engine import TaskPermissionError

    db = get_db()
    try:
        result = po_acceptance.accept_po(
            db, auth.user, accurate_po_id=accurate_po_id
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (TaskPermissionError, ObjectAuthError) as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except po_acceptance.PoAcceptanceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    sync_info = result.get("design_gate_sync") or {
        "applicable": False,
        "synced": True,
        "status": "n_a",
        "display": None,
        "error": None,
    }
    return {
        "accurate_po_id": result["accurate_po_id"],
        "duplicate": result["duplicate"],
        "acceptance": result["acceptance"],
        "workflow": result["workflow"],
        "design_gate_sync": sync_info,
        "po": _serialize_po_doc(result["po"], db),
    }


@router.post("/{accurate_po_id}/design-gate/sync")
def sync_design_gate(
    accurate_po_id: str,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Retry idempotent Gate Design Non-Cetak — tidak accept ulang / tidak buka payment."""
    from app.services import po_acceptance
    from app.services.object_auth import ObjectAuthError
    from app.services.task_engine import TaskPermissionError

    db = get_db()
    try:
        result = po_acceptance.retry_design_gate_sync(
            db, auth.user, accurate_po_id=accurate_po_id
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (TaskPermissionError, ObjectAuthError) as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except po_acceptance.PoAcceptanceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except po_acceptance.DesignGateSyncError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return {
        "accurate_po_id": result["accurate_po_id"],
        "design_gate_sync": result["design_gate_sync"],
        "acceptance": result["acceptance"],
        "gates": result["gates"],
        "workflow": result["workflow"],
        "po": _serialize_po_doc(result["po"], db),
    }


class PoBranchBody(BaseModel):
    branch_id: str
    reason: str


@router.patch("/{accurate_po_id}/branch")
def patch_po_branch(
    accurate_po_id: str,
    body: PoBranchBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Ubah identitas cabang PO (Accurate branchId). Tidak menulis Accurate."""
    from app.services import po_branch_service as pbs
    from app.services.object_auth import ObjectAuthError
    from app.services.task_engine import TaskPermissionError

    db = get_db()
    try:
        result = pbs.update_po_branch(
            db,
            auth.user,
            accurate_po_id=accurate_po_id,
            branch_id=body.branch_id,
            reason=body.reason,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (TaskPermissionError, ObjectAuthError) as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except pbs.PoBranchConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {
        "accurate_po_id": accurate_po_id,
        "before": result["before"],
        "after": result["after"],
        "unchanged": result["unchanged"],
        "po": _serialize_po_doc(result["po"], db),
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
    from app.services.object_auth import ObjectAuthError, assert_branch_access

    db = get_db()
    key = po_key.strip().strip("/")
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO tidak ditemukan")
    doc = get_by_accurate_id(db, key)
    if not doc:
        doc = get_by_po_no(db, key)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PO tidak ditemukan")
    try:
        assert_branch_access(auth.user, scope_object=doc)
    except ObjectAuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return _serialize_po_doc(doc, db)


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
    po = result.get("po")
    if result.get("writes") == 0 and result.get("outcome") in {"excluded", "failed"}:
        return {
            "accurate_id": result.get("accurate_id"),
            "po_no": result.get("po_no"),
            "line_count": 0,
            "was_insert": False,
            "duplicate_prevented": True,
            "count_after": result.get("count_after", 0),
            "process_kinds": [],
            "colors": [],
            "outcome": result.get("outcome"),
            "reason": result.get("reason"),
            "writes": 0,
            "ok": False,
        }
    if po is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Sinkronisasi ditolak tanpa dokumen PO.",
        )
    return {
        "accurate_id": result["accurate_id"],
        "po_no": result["po_no"],
        "line_count": result["line_count"],
        "was_insert": result["was_insert"],
        "duplicate_prevented": result["duplicate_prevented"],
        "count_after": result["count_after"],
        "process_kinds": [i.process_kind for i in po.items],
        "colors": [i.color for i in po.items],
        "outcome": result.get("outcome") or ("created" if result["was_insert"] else "updated"),
        "writes": result.get("writes", 1),
        "ok": True,
    }
