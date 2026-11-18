"""Checkpoint D — Persiapan, Screen, Sampel, Cetak, QC & Packing.

Satu sumber Qty: ledger ``task_confirmations`` milik task engine Checkpoint A.
Kuota Helper dipakai bersama antar operator pada satu item line dan dipesan
secara atomik sebelum konfirmasi cetak ditulis.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.domain.operational import TaskRef, build_task_key
from app.repositories import evidence_repository as evidence
from app.repositories import master_repository as master
from app.repositories import sample_repository as samples
from app.repositories import schedule_repository as schedules
from app.repositories import task_repository as tasks
from app.services.audit_log import append_audit_event
from app.services.task_engine import confirm_task, require_action

PREP_TASK = "persiapan-cup"
SCREEN_TASK = "screen"
SAMPLE_TASK = "konfirmasi-sample"
APPROVAL_TASK = "approval-sampel"
PRINT_TASK = "cetak"
QC_TASK = "qc-packing"

SAMPLE_SCOPE = "operator-sample"


class ProductionGateError(RuntimeError):
    """Prasyarat produksi belum terpenuhi (kuota, screen, atau sampel)."""


class ProductionQuantityError(ValueError):
    """Qty melampaui jadwal, kuota Helper, atau hasil cetak tersedia."""


def _line_ref(alloc: dict[str, Any]) -> TaskRef:
    return TaskRef(
        accurate_po_id=str(alloc["accurate_po_id"]),
        po_no=str(alloc.get("po_no") or ""),
        accurate_line_id=str(alloc["accurate_line_id"]),
        item_code=str(alloc.get("item_code") or ""),
        process_kind="Cetak",
    )


def _alloc_ref(alloc: dict[str, Any]) -> TaskRef:
    ref = _line_ref(alloc)
    ref.allocation_id = str(alloc["allocation_id"])
    return ref


def prep_task_key(accurate_po_id: str, accurate_line_id: str) -> str:
    return build_task_key(
        PREP_TASK,
        "po_line",
        TaskRef(accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id),
    )


def print_task_key(alloc: dict[str, Any]) -> str:
    return build_task_key(PRINT_TASK, "po_line_allocation", _alloc_ref(alloc))


def screen_task_key(alloc: dict[str, Any]) -> str:
    return build_task_key(SCREEN_TASK, "po_line_allocation", _alloc_ref(alloc))


def qc_task_key(accurate_po_id: str, accurate_line_id: str) -> str:
    return build_task_key(
        QC_TASK,
        "po_line",
        TaskRef(accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id),
    )


def _require_allocation(db: Database, allocation_id: str) -> dict[str, Any]:
    alloc = schedules.get_allocation(db, allocation_id)
    if alloc is None:
        raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}")
    if alloc.get("status") != schedules.STATUS_ACTIVE:
        raise ProductionGateError(f"Alokasi {allocation_id} tidak aktif.")
    return alloc


def printed_total_for_line(
    db: Database, *, accurate_po_id: str, accurate_line_id: str
) -> float:
    total = 0.0
    for alloc in schedules.list_allocations(
        db, accurate_line_id=accurate_line_id, include_released=True
    ):
        task = tasks.get_task(db, print_task_key(alloc))
        total += float((task or {}).get("qty_confirmed") or 0)
    return total


def helper_quota_state(
    db: Database, *, accurate_po_id: str, accurate_line_id: str
) -> dict[str, Any]:
    """Kuota bersama Helper untuk satu item line (bukan per operator)."""
    prep = tasks.get_task(db, prep_task_key(accurate_po_id, accurate_line_id)) or {}
    prepared = float(prep.get("qty_confirmed") or 0)
    consumed = float(prep.get("qty_consumed") or 0)
    target = float(prep.get("qty_target") or 0)
    return {
        "qty_target": target,
        "qty_prepared": prepared,
        "qty_belum_disiapkan": max(target - prepared, 0),
        "qty_consumed": consumed,
        "qty_available": max(prepared - consumed, 0),
    }


def confirm_preparation(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    accurate_line_id: str,
    qty: float,
    idempotency_key: str,
    note: str | None = None,
) -> dict[str, Any]:
    key = prep_task_key(accurate_po_id, accurate_line_id)
    if tasks.get_task(db, key) is None:
        raise LookupError(
            "Tugas Persiapan Cup belum terbit — jadwal belum dikonfirmasi."
        )
    result = confirm_task(
        db, user, task_key=key, qty=qty, idempotency_key=idempotency_key, note=note
    )
    result["quota"] = helper_quota_state(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    return result


def confirm_screen(
    db: Database,
    user: dict[str, Any],
    *,
    allocation_id: str,
    qty: float,
    idempotency_key: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Qty Screen diinput manual; tidak dipaksa sama dengan Qty jadwal."""
    alloc = _require_allocation(db, allocation_id)
    key = screen_task_key(alloc)
    if tasks.get_task(db, key) is None:
        raise LookupError("Tugas Screen belum terbit untuk alokasi ini.")
    return confirm_task(
        db, user, task_key=key, qty=qty, idempotency_key=idempotency_key, note=note
    )


def screen_confirmed_qty(db: Database, alloc: dict[str, Any]) -> float:
    task = tasks.get_task(db, screen_task_key(alloc))
    return float((task or {}).get("qty_confirmed") or 0)


def submit_sample(
    db: Database,
    user: dict[str, Any],
    *,
    allocation_id: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> dict[str, Any]:
    """Sampel milik satu alokasi operator; revisi menambah versi baru."""
    require_action(db, user, task_type=SAMPLE_TASK, action="input")
    alloc = _require_allocation(db, allocation_id)
    if screen_confirmed_qty(db, alloc) <= 0:
        raise ProductionGateError(
            "Screen untuk alokasi ini belum dikonfirmasi Screener."
        )
    quota = helper_quota_state(
        db,
        accurate_po_id=str(alloc["accurate_po_id"]),
        accurate_line_id=str(alloc["accurate_line_id"]),
    )
    if quota["qty_available"] <= 0:
        raise ProductionGateError("Kuota Persiapan Cup dari Helper belum tersedia.")

    actor = str(user.get("email_normalized") or "")
    stored = evidence.store_evidence(
        db,
        scope=SAMPLE_SCOPE,
        scope_id=allocation_id,
        filename=filename,
        content_type=content_type,
        content=content,
        uploaded_by=actor,
        kind="operator-sample",
        context={"accurate_line_id": alloc["accurate_line_id"]},
    )
    sample = samples.create_sample(
        db,
        allocation_id=allocation_id,
        accurate_po_id=str(alloc["accurate_po_id"]),
        accurate_line_id=str(alloc["accurate_line_id"]),
        evidence_id=stored["evidence_id"],
        filename=filename,
        submitted_by=actor,
    )

    master.seed_master_defaults(db)
    tasks.upsert_task(
        db,
        task_type=SAMPLE_TASK,
        scope="po_line_allocation",
        assigned_role=master.assigned_role_for(db, SAMPLE_TASK) or "operator",
        ref=_alloc_ref(alloc),
        label="Upload/Konfirmasi Sample",
        context={"latest_version": sample["version"], "status": sample["status"]},
    )
    approval = tasks.upsert_task(
        db,
        task_type=APPROVAL_TASK,
        scope="po_line_allocation",
        assigned_role=master.assigned_role_for(db, APPROVAL_TASK) or "production-head",
        ref=_alloc_ref(alloc),
        label="Approval Sampel",
        context={
            "sample_version": sample["version"],
            "operator_email": alloc.get("operator_email"),
            "status": sample["status"],
        },
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": approval["task_key"]},
        {"$set": {"status": tasks.TASK_STATUS_OPEN}},
    )

    append_audit_event(
        db,
        action="production.sample.upload",
        actor_email=actor,
        target_email=None,
        detail={
            "allocation_id": allocation_id,
            "version": sample["version"],
            "evidence_id": stored["evidence_id"],
        },
    )
    return {"sample": sample, "evidence": stored}


def decide_sample(
    db: Database,
    user: dict[str, Any],
    *,
    allocation_id: str,
    version: int,
    approved: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    require_action(db, user, task_type=APPROVAL_TASK, action="approve")
    alloc = _require_allocation(db, allocation_id)
    if not approved and not (reason or "").strip():
        raise ValueError("Alasan penolakan sampel wajib diisi.")

    actor = str(user.get("email_normalized") or "")
    sample = samples.decide_sample(
        db,
        allocation_id=allocation_id,
        version=version,
        approved=approved,
        actor_email=actor,
        reason=reason,
    )

    approval_key = build_task_key(APPROVAL_TASK, "po_line_allocation", _alloc_ref(alloc))
    tasks.tasks_collection(db).update_one(
        {"task_key": approval_key},
        {
            "$set": {
                "status": tasks.TASK_STATUS_DONE
                if approved
                else tasks.TASK_STATUS_OPEN,
                "context.status": sample["status"],
                "context.sample_version": version,
            }
        },
    )

    if approved:
        _ensure_print_task(db, alloc)

    append_audit_event(
        db,
        action="production.sample.approve" if approved else "production.sample.reject",
        actor_email=actor,
        target_email=None,
        detail={
            "allocation_id": allocation_id,
            "version": version,
            "reason": (reason or "").strip() or None,
        },
    )
    return sample


def _ensure_print_task(db: Database, alloc: dict[str, Any]) -> dict[str, Any]:
    master.seed_master_defaults(db)
    return tasks.upsert_task(
        db,
        task_type=PRINT_TASK,
        scope="po_line_allocation",
        assigned_role=master.assigned_role_for(db, PRINT_TASK) or "operator",
        ref=_alloc_ref(alloc),
        qty_target=float(alloc["qty"]),
        label="Cetak",
        context={"operator_email": alloc.get("operator_email")},
    )


def _reserve_helper_quota(
    db: Database, *, accurate_po_id: str, accurate_line_id: str, qty: float
) -> bool:
    """Pesan kuota Helper secara atomik; False bila kuota tidak cukup."""
    key = prep_task_key(accurate_po_id, accurate_line_id)
    updated = tasks.tasks_collection(db).find_one_and_update(
        {
            "task_key": key,
            "$expr": {
                "$lte": [
                    {"$add": [{"$ifNull": ["$qty_consumed", 0]}, qty]},
                    "$qty_confirmed",
                ]
            },
        },
        {"$inc": {"qty_consumed": qty}},
    )
    return updated is not None


def _release_helper_quota(
    db: Database, *, accurate_po_id: str, accurate_line_id: str, qty: float
) -> None:
    tasks.tasks_collection(db).update_one(
        {"task_key": prep_task_key(accurate_po_id, accurate_line_id)},
        {"$inc": {"qty_consumed": -qty}},
    )


def confirm_print(
    db: Database,
    user: dict[str, Any],
    *,
    allocation_id: str,
    qty: float,
    idempotency_key: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Cetak massal parsial dengan penjagaan jadwal + kuota Helper bersama."""
    require_action(db, user, task_type=PRINT_TASK, action="confirm")
    if qty <= 0:
        raise ProductionQuantityError("Qty cetak harus lebih besar dari nol.")

    alloc = _require_allocation(db, allocation_id)
    accurate_po_id = str(alloc["accurate_po_id"])
    accurate_line_id = str(alloc["accurate_line_id"])

    if screen_confirmed_qty(db, alloc) <= 0:
        raise ProductionGateError("Gate Screen belum terpenuhi untuk alokasi ini.")
    if samples.approved_sample(db, allocation_id) is None:
        raise ProductionGateError(
            "Sampel untuk alokasi ini belum disetujui — cetak massal terkunci."
        )

    _ensure_print_task(db, alloc)

    # Kuota Helper dipesan lebih dulu; dilepas kembali bila konfirmasi gagal.
    if not _reserve_helper_quota(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id, qty=qty
    ):
        quota = helper_quota_state(
            db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
        )
        raise ProductionQuantityError(
            f"Kuota Persiapan Cup tersisa {quota['qty_available']:g} — "
            f"tidak cukup untuk {qty:g}."
        )

    try:
        result = confirm_task(
            db,
            user,
            task_key=print_task_key(alloc),
            qty=qty,
            idempotency_key=idempotency_key,
            note=note,
        )
    except Exception:
        _release_helper_quota(
            db,
            accurate_po_id=accurate_po_id,
            accurate_line_id=accurate_line_id,
            qty=qty,
        )
        raise

    if result.get("duplicate"):
        # Retry/double-click: Qty tidak ditambah, kuota tidak boleh ikut terpakai.
        _release_helper_quota(
            db,
            accurate_po_id=accurate_po_id,
            accurate_line_id=accurate_line_id,
            qty=qty,
        )

    _sync_qc_task(db, alloc)
    result["quota"] = helper_quota_state(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    return result


def _sync_qc_task(db: Database, alloc: dict[str, Any]) -> dict[str, Any]:
    """Target QC = total hasil cetak terkonfirmasi seluruh operator pada line."""
    master.seed_master_defaults(db)
    accurate_po_id = str(alloc["accurate_po_id"])
    accurate_line_id = str(alloc["accurate_line_id"])
    printed = printed_total_for_line(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    if printed <= 0:
        return {}
    return tasks.upsert_task(
        db,
        task_type=QC_TASK,
        scope="po_line",
        assigned_role=master.assigned_role_for(db, QC_TASK) or "helper",
        ref=_line_ref(alloc),
        qty_target=printed,
        label="QC & Packing",
        context={"qty_printed": printed},
    )


def confirm_qc_packing(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_po_id: str,
    accurate_line_id: str,
    qty: float,
    idempotency_key: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Satu Qty berarti lolos QC sekaligus selesai Packing."""
    key = qc_task_key(accurate_po_id, accurate_line_id)
    task = tasks.get_task(db, key)
    if task is None:
        raise LookupError("Tugas QC & Packing belum terbit — belum ada hasil cetak.")

    printed = printed_total_for_line(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": key}, {"$set": {"qty_target": printed}}
    )
    result = confirm_task(
        db, user, task_key=key, qty=qty, idempotency_key=idempotency_key, note=note
    )
    result["qty_printed"] = printed
    result["qty_ready_to_ship"] = float(result["task"]["qty_confirmed"])
    return result


def line_production_state(
    db: Database, po_doc: dict[str, Any], accurate_line_id: str
) -> dict[str, Any]:
    accurate_po_id = str(po_doc.get("accurate_id") or "")
    quota = helper_quota_state(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    allocations = []
    for alloc in schedules.list_allocations(
        db, accurate_line_id=accurate_line_id, include_released=True
    ):
        print_task = tasks.get_task(db, print_task_key(alloc)) or {}
        allocations.append(
            {
                **alloc,
                "qty_screen": screen_confirmed_qty(db, alloc),
                "qty_printed": float(print_task.get("qty_confirmed") or 0),
                "sample_latest": samples.latest_sample(db, alloc["allocation_id"]),
                "sample_approved": samples.approved_sample(db, alloc["allocation_id"])
                is not None,
            }
        )
    qc_task = tasks.get_task(db, qc_task_key(accurate_po_id, accurate_line_id)) or {}
    printed = printed_total_for_line(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    packed = float(qc_task.get("qty_confirmed") or 0)
    return {
        "accurate_po_id": accurate_po_id,
        "accurate_line_id": accurate_line_id,
        "helper_quota": quota,
        "allocations": allocations,
        "qty_printed_total": printed,
        "qty_packed": packed,
        "qty_waiting_qc": max(printed - packed, 0),
    }
