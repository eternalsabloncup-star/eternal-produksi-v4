"""Guard bersama ingest PO Accurate — date floor + cabang kanonis.

Dipakai auto-sync (``po_job``) dan jalur ``po_sync`` agar kontrak tidak diverge.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.domain.po import PoHeaderModel
from app.services.accurate_auto_sync.date_floor import detail_scope_outcome
from app.services.branch_identity import normalize_branch_identity
from app.services.object_auth import object_branch_code


def source_branch_id_from_po(po: PoHeaderModel) -> str | None:
    """Cabang dari payload Accurate yang sudah dinormalisasi (bukan tebakan)."""
    meta = po.raw_sync_meta if isinstance(po.raw_sync_meta, dict) else {}
    return normalize_branch_identity(meta.get("accurate_branch_id"))


def source_branch_id_from_payload(payload: dict[str, Any]) -> str | None:
    return normalize_branch_identity(payload.get("branchId"))


def require_active_master_branch(db: Database, branch_id: str) -> str:
    """Fail-closed: cabang harus ada di Master dan aktif."""
    from app.repositories.org_master_repository import (
        get_branch,
        validate_branch_code_format,
    )

    code = validate_branch_code_format(branch_id)
    doc = get_branch(db, code)
    if doc is None:
        raise ValueError(f"Cabang tidak dikenal di Master: {code}")
    if not bool(doc.get("is_active", False)):
        raise ValueError(f"Cabang tidak aktif: {code}")
    return code


def resolve_branch_for_ingest(
    db: Database,
    *,
    po: PoHeaderModel,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Tentukan aksi cabang sebelum write PO.

    Returns dict dengan ``ok`` + ``action``:
    - ``set`` — isi ``branch_id`` kanonis (insert / kosong)
    - ``preserve`` — pertahankan existing
    - ``conflict`` — source beda dari existing; jangan geser scope
    - gagal: ``ok=False`` + ``outcome`` failed (zero write untuk insert baru)
    """
    source = source_branch_id_from_po(po)
    if not source:
        return {
            "ok": False,
            "outcome": "failed",
            "reason": "missing_branch_id",
            "code": "missing_branch_id",
            "accurate_id": str(po.accurate_id or ""),
            "writes": 0,
        }
    try:
        source = require_active_master_branch(db, source)
    except ValueError as exc:
        return {
            "ok": False,
            "outcome": "failed",
            "reason": "unknown_or_inactive_branch",
            "code": "unknown_or_inactive_branch",
            "accurate_id": str(po.accurate_id or ""),
            "source_branch_id": source,
            "detail": str(exc),
            "writes": 0,
        }

    existing_id = None
    if existing is not None:
        existing_id = normalize_branch_identity(
            existing.get("branch_id")
        ) or normalize_branch_identity(object_branch_code(existing))

    if existing_id and existing_id != source:
        return {
            "ok": True,
            "action": "conflict",
            "branch_id": existing_id,
            "source_branch_id": source,
            "accurate_id": str(po.accurate_id or ""),
        }
    if existing_id:
        return {
            "ok": True,
            "action": "preserve",
            "branch_id": existing_id,
            "source_branch_id": source,
            "accurate_id": str(po.accurate_id or ""),
        }
    return {
        "ok": True,
        "action": "set",
        "branch_id": source,
        "source_branch_id": source,
        "accurate_id": str(po.accurate_id or ""),
    }


def date_floor_reject_or_none(
    payload: dict[str, Any], *, accurate_id: str
) -> dict[str, Any] | None:
    """None = lanjut; dict outcome excluded/failed = zero write."""
    return detail_scope_outcome(payload, accurate_id=str(accurate_id))


def mark_branch_conflict_review(
    db: Database,
    *,
    accurate_id: str,
    existing_branch_id: str,
    source_branch_id: str,
) -> None:
    """Catat konflik cabang tanpa mengubah ``branch_id`` / operational snapshot lain."""
    from app.repositories.po_repository import po_collection, require_po_indexes

    require_po_indexes(db)
    po_collection(db).update_one(
        {"accurate_id": str(accurate_id)},
        {
            "$set": {
                "operational.branch_review": {
                    "status": "conflict",
                    "existing_branch_id": existing_branch_id,
                    "source_branch_id": source_branch_id,
                    "reason": "accurate_branch_differs_from_canonical",
                }
            }
        },
    )

