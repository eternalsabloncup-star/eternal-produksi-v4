"""Resolver Accurate branchId → kode cabang Eternal (Master).

Kontrak:
- Master Cabang Eternal = sumber kebenaran internal.
- Accurate ``branchId`` numerik tidak pernah menjadi ``branch_code`` tanpa mapping.
- Fail-closed bila mapping absen/nonaktif/konflik.
- Tidak menulis Accurate / Mongo di modul ini.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pymongo.database import Database

from app.repositories import org_master_repository as org


class BranchResolveStatus(str, Enum):
    RESOLVED = "RESOLVED"
    MISSING_ACCURATE_ID = "MISSING_ACCURATE_ID"
    UNMAPPED = "UNMAPPED_ACCURATE_BRANCH_ID"
    INACTIVE_MASTER = "INVALID_MASTER_BRANCH"
    CONFLICT = "CONFLICT"
    INVALID_ID = "INVALID_ACCURATE_BRANCH_ID"


@dataclass(frozen=True)
class BranchResolveResult:
    status: BranchResolveStatus
    accurate_branch_id: str | None
    branch_code: str | None
    branch_name: str | None = None
    detail: str | None = None


def normalize_accurate_branch_id(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    # Accurate live: integer id — tolak string kosong / non-digit untuk mapping resmi.
    if not text.isdigit():
        # Izinkan ID alfanumerik sempit bila Accurate berubah, tapi jangan kode Master.
        cleaned = text.upper()
        if len(cleaned) > 32:
            return None
        return cleaned
    return text  # keep numeric string without inventing Master code


def extract_accurate_branch_id_from_po(doc: dict[str, Any] | None) -> str | None:
    if not isinstance(doc, dict):
        return None
    header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    meta = header.get("raw_sync_meta") if isinstance(header.get("raw_sync_meta"), dict) else {}
    for candidate in (
        doc.get("accurate_branch_id"),
        meta.get("accurate_branch_id"),
        header.get("branchId"),
        doc.get("branchId"),
    ):
        nid = normalize_accurate_branch_id(candidate)
        if nid:
            return nid
    return None


def resolve_eternal_branch_code(
    db: Database, accurate_branch_id: Any
) -> BranchResolveResult:
    """Map Accurate branch id → Master code aktif. Deterministik & idempotent."""
    nid = normalize_accurate_branch_id(accurate_branch_id)
    if not nid:
        return BranchResolveResult(
            status=BranchResolveStatus.MISSING_ACCURATE_ID,
            accurate_branch_id=None,
            branch_code=None,
            detail="accurate_branch_id kosong",
        )
    # Accurate live branchId terbukti numerik; tolak format tidak aman.
    if not nid.isdigit():
        return BranchResolveResult(
            status=BranchResolveStatus.INVALID_ID,
            accurate_branch_id=nid,
            branch_code=None,
            detail="format accurate_branch_id tidak valid (wajib numerik)",
        )

    matches = org.find_branches_by_accurate_id(db, nid)
    active = [m for m in matches if bool(m.get("is_active", False))]
    if len(active) > 1:
        return BranchResolveResult(
            status=BranchResolveStatus.CONFLICT,
            accurate_branch_id=nid,
            branch_code=None,
            detail=f"mapping ganda aktif: {[m.get('code') for m in active]}",
        )
    if len(active) == 1:
        code = str(active[0].get("code") or "").upper()
        return BranchResolveResult(
            status=BranchResolveStatus.RESOLVED,
            accurate_branch_id=nid,
            branch_code=code,
            branch_name=str(active[0].get("name") or "") or None,
        )
    if matches and not active:
        return BranchResolveResult(
            status=BranchResolveStatus.INACTIVE_MASTER,
            accurate_branch_id=nid,
            branch_code=str(matches[0].get("code") or "").upper() or None,
            detail="mapping menunjuk cabang nonaktif",
        )
    return BranchResolveResult(
        status=BranchResolveStatus.UNMAPPED,
        accurate_branch_id=nid,
        branch_code=None,
        detail="belum ada mapping Master",
    )


def mapping_status_for_branch(doc: dict[str, Any]) -> str:
    """Status tampilan UI Master Cabang."""
    aid = normalize_accurate_branch_id(doc.get("accurate_branch_id"))
    if not aid:
        return "unmapped"
    return "mapped"


def apply_branch_code_on_ingest(
    db: Database,
    *,
    existing_doc: dict[str, Any] | None,
    accurate_branch_id: Any,
) -> dict[str, Any]:
    """Tentukan apakah ingest boleh menulis branch_code.

    - Tidak pernah menimpa branch_code historis yang sudah terisi.
    - Hanya mengisi jika kosong/absen dan mapping RESOLVED.
    """
    from app.services.object_auth import object_branch_code

    current = object_branch_code(existing_doc) if existing_doc else None
    if current:
        return {
            "action": "preserve_existing",
            "branch_code": current,
            "resolve": None,
        }
    resolved = resolve_eternal_branch_code(db, accurate_branch_id)
    if resolved.status == BranchResolveStatus.RESOLVED and resolved.branch_code:
        return {
            "action": "set_from_mapping",
            "branch_code": resolved.branch_code,
            "resolve": resolved,
        }
    return {
        "action": "leave_unset",
        "branch_code": None,
        "resolve": resolved,
    }
