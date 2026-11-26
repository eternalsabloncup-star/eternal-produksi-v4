"""M6 Accurate paint inbound — dark adapter (disabled by default).

Does NOT call Accurate purchase/goods-receipt APIs (not verified live).
Does NOT credit stock. Mapping + duplicate guards only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.repositories import paint_repository as paint
from app.services import paint_service as svc


@dataclass(frozen=True)
class AccuratePaintReadiness:
    status: str  # NOT_READY | READY_DARK | ENABLED (ENABLED never without Owner)
    detail: str
    adapter_enabled: bool


def readiness() -> AccuratePaintReadiness:
    settings = get_settings()
    enabled = bool(getattr(settings, "paint_accurate_inbound_enabled", False))
    if enabled:
        # Fail-closed: even if misconfigured ON, we refuse auto credit without
        # a verified goods-receipt client (does not exist yet).
        return AccuratePaintReadiness(
            status="NOT_READY",
            detail=(
                "Flag paint Accurate ON tetapi goods-receipt Accurate belum "
                "terverifikasi — stok otomatis tetap ditolak."
            ),
            adapter_enabled=False,
        )
    return AccuratePaintReadiness(
        status="READY_DARK",
        detail=(
            "Adapter mapping siap; integrasi purchasing/goods-receipt Accurate "
            "belum aktif. Gunakan penerimaan manual terkontrol."
        ),
        adapter_enabled=False,
    )


def list_mappings(db: Database) -> list[dict[str, Any]]:
    paint.require_paint_indexes(db)
    return list(
        paint.coll(db, paint.ACCURATE_MAPPINGS).find({}, {"_id": 0}).sort("accurate_item_code", 1)
    )


def upsert_mapping(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_item_code: str,
    paint_id: str,
) -> dict[str, Any]:
    svc.require_paint_permission(user, svc.PERM_PAINT_ACCURATE_MAP)
    paint.require_paint_indexes(db)
    code = str(accurate_item_code or "").strip()
    pid = str(paint_id or "").strip()
    if not code or not pid:
        raise svc.PaintValidationError("accurate_item_code dan paint_id wajib.")
    svc.get_paint(db, pid)
    doc = {
        "accurate_item_code": code,
        "paint_id": pid,
        "updated_at": paint.now_utc(),
        "updated_by": svc.actor_email(user),
    }
    paint.coll(db, paint.ACCURATE_MAPPINGS).update_one(
        {"accurate_item_code": code},
        {"$set": doc, "$setOnInsert": {"created_at": paint.now_utc()}},
        upsert=True,
    )
    return doc


def reject_unverified_goods_receipt(
    *,
    external_document_id: str,
    physical_confirmed: bool,
) -> dict[str, Any]:
    """Contract helper for tests — never credits stock."""
    ready = readiness()
    if not physical_confirmed:
        return {
            "accepted": False,
            "reason": "physical_confirmation_required",
            "external_document_id": external_document_id,
            "readiness": ready.status,
        }
    if ready.status != "ENABLED":
        return {
            "accepted": False,
            "reason": "accurate_goods_receipt_not_ready",
            "external_document_id": external_document_id,
            "readiness": ready.status,
        }
    return {
        "accepted": False,
        "reason": "accurate_auto_credit_disabled",
        "external_document_id": external_document_id,
        "readiness": ready.status,
    }
