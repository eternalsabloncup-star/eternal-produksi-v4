"""Guard bersama ingest PO Accurate — date floor + cabang kanonis.

Dipakai auto-sync (``po_job``) dan jalur ``po_sync`` agar kontrak tidak diverge.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.domain.po import PoHeaderModel
from app.services.accurate_auto_sync.date_floor import detail_scope_outcome
from app.services.branch_identity import normalize_branch_identity


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


def canonical_branch_id_field(doc: dict[str, Any] | None) -> str | None:
    """Hanya ``branch_id`` dokumen — jangan anggap meta Accurate sebagai keputusan existing."""
    if not isinstance(doc, dict):
        return None
    return normalize_branch_identity(doc.get("branch_id"))


def po_has_operational_dependencies(db: Database, accurate_id: str) -> bool:
    """Task / alokasi / jadwal / SJ terkait — gate aman sebelum mengisi cabang kosong."""
    aid = str(accurate_id)
    if db["operational_tasks"].find_one(
        {
            "$or": [
                {"accurate_po_id": aid},
                {"accurate_id": aid},
                {"ref.accurate_po_id": aid},
            ]
        },
        {"_id": 1},
    ):
        return True
    if db["production_allocations"].find_one(
        {"$or": [{"accurate_po_id": aid}, {"accurate_id": aid}]}, {"_id": 1}
    ):
        return True
    if db["accurate_delivery_notes"].find_one(
        {"$or": [{"accurate_po_id": aid}, {"accurate_po_ids": aid}]}, {"_id": 1}
    ):
        return True
    if db["schedule_commands"].find_one({"accurate_po_id": aid}, {"_id": 1}):
        return True
    return False


def cas_empty_branch_blocked_reason(
    db: Database,
    *,
    accurate_id: str,
    existing: dict[str, Any] | None = None,
) -> str | None:
    """None = CAS kosong diizinkan; string = fail-closed (jangan isi otomatis)."""
    from app.repositories.po_repository import get_by_accurate_id
    from app.services.po_acceptance import is_po_accepted

    doc = existing if isinstance(existing, dict) else get_by_accurate_id(db, accurate_id)
    if doc is None:
        # Insert baru ditangani $setOnInsert — bukan jalur CAS existing.
        return None
    if is_po_accepted(doc):
        return "po_accepted"
    if po_has_operational_dependencies(db, str(accurate_id)):
        return "operational_dependency_present"
    return None


def mark_branch_fill_blocked_review(
    db: Database,
    *,
    accurate_id: str,
    source_branch_id: str,
    reason: str,
) -> None:
    """Catat blokir CAS tanpa mengubah ``branch_id`` / field workflow lain."""
    from app.repositories.po_repository import po_collection, require_po_indexes

    require_po_indexes(db)
    po_collection(db).update_one(
        {"accurate_id": str(accurate_id)},
        {
            "$set": {
                "operational.branch_review": {
                    "status": "blocked",
                    "reason": reason,
                    "source_branch_id": source_branch_id,
                    "detail": "auto_fill_empty_branch_denied",
                }
            }
        },
    )


def fill_empty_branch_id_cas(
    db: Database,
    *,
    accurate_id: str,
    branch_id: str,
    owner_token: str | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """CAS isi ``branch_id`` hanya jika kosong, belum accepted, tanpa dependensi.

    - Acceptance: filter Mongo atomik (kosong + not accepted) — anti TOCTOU.
    - Dependensi lintas koleksi: pre-check + post-check fail-closed; bila race
      membentuk turunan setelah write, rollback nilai CAS yang baru diisi.
      Tidak mengklaim keamanan transaksi multi-dokumen spekulatif.
    """
    from app.repositories.po_repository import (
        clear_branch_id_if_matches,
        get_by_accurate_id,
        set_branch_id_if_empty_and_unaccepted,
    )
    from app.services.accurate_auto_sync.locks import require_owns_job_lock
    from app.services.po_acceptance import is_po_accepted

    if owner_token is not None:
        require_owns_job_lock(db, job="po", owner_token=owner_token)

    aid = str(accurate_id)
    blocked = cas_empty_branch_blocked_reason(
        db, accurate_id=aid, existing=existing
    )
    if blocked:
        mark_branch_fill_blocked_review(
            db,
            accurate_id=aid,
            source_branch_id=str(branch_id),
            reason=blocked,
        )
        return None

    filled = set_branch_id_if_empty_and_unaccepted(db, aid, branch_id)
    if filled is None:
        # CAS kalah: sudah ber-cabang, atau accepted menang race, atau hilang.
        live = get_by_accurate_id(db, aid)
        if live is not None and is_po_accepted(live) and not live.get("branch_id"):
            mark_branch_fill_blocked_review(
                db,
                accurate_id=aid,
                source_branch_id=str(branch_id),
                reason="po_accepted",
            )
        elif live is not None and po_has_operational_dependencies(db, aid) and not live.get(
            "branch_id"
        ):
            mark_branch_fill_blocked_review(
                db,
                accurate_id=aid,
                source_branch_id=str(branch_id),
                reason="operational_dependency_present",
            )
        return None

    # Post-check lintas koleksi: turunan muncul setelah/bersamaan CAS → rollback.
    if po_has_operational_dependencies(db, aid):
        clear_branch_id_if_matches(db, aid, str(filled.get("branch_id") or branch_id))
        mark_branch_fill_blocked_review(
            db,
            accurate_id=aid,
            source_branch_id=str(branch_id),
            reason="operational_dependency_race",
        )
        return None
    return filled


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

    Existing hanya dari field kanonis ``branch_id``. Meta Accurate adalah source,
    bukan keputusan cabang yang sudah dikunci.
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

    existing_id = canonical_branch_id_field(existing)

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

