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
from app.services.object_auth import (
    assert_branch_access,
    assert_operator_assignment,
)
from app.services.task_engine import (
    TaskPermissionError,
    can_act,
    confirm_task,
    require_action,
)

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


def _require_accepted_po(db: Database, accurate_po_id: str) -> dict[str, Any]:
    """Gate Terima PO — penolakan sebelum mutasi produksi."""
    from app.repositories.po_repository import get_by_accurate_id
    from app.services.po_acceptance import PoAcceptanceError, require_po_accepted

    po_id = str(accurate_po_id or "").strip()
    if not po_id:
        raise ValueError("accurate_po_id wajib.")
    po_doc = get_by_accurate_id(db, po_id)
    if po_doc is None:
        raise LookupError(f"PO tidak ditemukan: {po_id}")
    try:
        require_po_accepted(po_doc)
    except PoAcceptanceError as exc:
        raise ProductionGateError(str(exc)) from exc
    return po_doc


def _assert_po_branch(user: dict[str, Any], po_doc: dict[str, Any]) -> None:
    assert_branch_access(user, scope_object=po_doc)


def _assert_alloc_mutate(
    db: Database,
    user: dict[str, Any],
    alloc: dict[str, Any],
    *,
    allow_approver: bool = False,
) -> dict[str, Any]:
    """Cabang sebelum mutasi alokasi — assignment metadata bukan gate (Owner)."""
    from app.repositories.po_repository import get_by_accurate_id
    from app.services.object_auth import object_branch_code

    _ = allow_approver
    # Keep call for API compatibility; assert_operator_assignment is a no-op.
    assert_operator_assignment(
        db, user, allocation=alloc, allow_approver=allow_approver
    )
    po_doc = get_by_accurate_id(db, str(alloc.get("accurate_po_id") or ""))
    scope = po_doc if object_branch_code(po_doc) else (alloc if object_branch_code(alloc) else po_doc or alloc)
    assert_branch_access(user, scope_object=scope)
    return po_doc or {}


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
    po_doc = _require_accepted_po(db, accurate_po_id)
    _assert_po_branch(user, po_doc)
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
    _require_accepted_po(db, str(alloc["accurate_po_id"]))
    # Screen: screener role — assignment operator tidak berlaku; cabang wajib.
    from app.repositories.po_repository import get_by_accurate_id

    po_doc = get_by_accurate_id(db, str(alloc["accurate_po_id"]))
    assert_branch_access(user, scope_object=po_doc or alloc)
    key = screen_task_key(alloc)
    if tasks.get_task(db, key) is None:
        raise LookupError("Tugas Screen belum terbit untuk alokasi ini.")
    return confirm_task(
        db, user, task_key=key, qty=qty, idempotency_key=idempotency_key, note=note
    )


def screen_confirmed_qty(db: Database, alloc: dict[str, Any]) -> float:
    task = tasks.get_task(db, screen_task_key(alloc))
    return float((task or {}).get("qty_confirmed") or 0)


def upload_sample_draft(
    db: Database,
    user: dict[str, Any],
    *,
    allocation_id: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> dict[str, Any]:
    """Unggah draft sampel — append-only; belum membuka approval / gate cetak."""
    require_action(db, user, task_type=SAMPLE_TASK, action="input")
    alloc = _require_allocation(db, allocation_id)
    _require_accepted_po(db, str(alloc["accurate_po_id"]))
    _assert_alloc_mutate(db, user, alloc)
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
    if samples.pending_sample(db, allocation_id) is not None:
        raise ProductionGateError(
            "Sampel masih menunggu approval — tunggu keputusan Kepala Produksi "
            "sebelum mengunggah draft baru."
        )

    # Fail-closed Master SEBELUM evidence/GridFS.
    sample_role = master.require_task_type_assigned_role(db, SAMPLE_TASK)
    master.require_task_type_assigned_role(db, APPROVAL_TASK)

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
        context={
            "accurate_line_id": alloc["accurate_line_id"],
            "sample_phase": "draft",
        },
    )
    try:
        sample = samples.create_sample(
            db,
            allocation_id=allocation_id,
            accurate_po_id=str(alloc["accurate_po_id"]),
            accurate_line_id=str(alloc["accurate_line_id"]),
            evidence_id=stored["evidence_id"],
            filename=filename,
            submitted_by=actor,
            status=samples.STATUS_DRAFT,
        )
    except Exception:
        # Jangan sisakan evidence tanpa baris sampel yang bisa dilacak.
        try:
            evidence.soft_delete_evidence(
                db,
                evidence_id=str(stored["evidence_id"]),
                actor_email=actor,
                reason="sample_insert_failed_cleanup",
            )
        except Exception:  # noqa: BLE001 — cleanup best-effort; raise original
            pass
        raise

    # Operator tetap punya tugas terbuka sampai Konfirmasi.
    sample_task = tasks.upsert_task(
        db,
        task_type=SAMPLE_TASK,
        scope="po_line_allocation",
        assigned_role=sample_role,
        ref=_alloc_ref(alloc),
        label="Konfirmasi Sampel",
        context={
            "latest_version": sample["version"],
            "status": sample["status"],
            "needs_resubmit": False,
            "display_kind": "draft",
            "active_draft_version": sample["version"],
        },
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": sample_task["task_key"]},
        {
            "$set": {
                "status": tasks.TASK_STATUS_OPEN,
                "label": "Konfirmasi Sampel",
                "context.latest_version": sample["version"],
                "context.status": samples.STATUS_DRAFT,
                "context.needs_resubmit": False,
                "context.display_kind": "draft",
                "context.active_draft_version": sample["version"],
            }
        },
    )

    append_audit_event(
        db,
        action="production.sample.draft_upload",
        actor_email=actor,
        target_email=None,
        detail={
            "allocation_id": allocation_id,
            "version": sample["version"],
            "evidence_id": stored["evidence_id"],
        },
    )
    return {"sample": sample, "evidence": stored, "phase": "draft"}


def submit_sample(
    db: Database,
    user: dict[str, Any],
    *,
    allocation_id: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> dict[str, Any]:
    """Alias: unggah draft. Approval hanya lewat confirm_sample_submission."""
    return upload_sample_draft(
        db,
        user,
        allocation_id=allocation_id,
        filename=filename,
        content_type=content_type,
        content=content,
    )


def confirm_sample_submission(
    db: Database,
    user: dict[str, Any],
    *,
    allocation_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Konfirmasi draft aktif → Menunggu Approval (satu versi submitted)."""
    if not (
        can_act(db, user, task_type=SAMPLE_TASK, action="confirm")
        or can_act(db, user, task_type=SAMPLE_TASK, action="input")
    ):
        raise TaskPermissionError(
            "Akses ditolak untuk konfirmasi sampel."
        )
    alloc = _require_allocation(db, allocation_id)
    _require_accepted_po(db, str(alloc["accurate_po_id"]))
    _assert_alloc_mutate(db, user, alloc)

    # Idempotency: konfirmasi ulang saat sudah pending → tidak gandakan tugas.
    pending = samples.pending_sample(db, allocation_id)
    if pending is not None:
        return {
            "sample": pending,
            "duplicate": True,
            "phase": "menunggu_approval",
        }

    draft = samples.latest_draft(db, allocation_id)
    if draft is None:
        raise ProductionGateError(
            "Belum ada draft sampel aktif — unggah file terlebih dahulu."
        )

    sample_role = master.require_task_type_assigned_role(db, SAMPLE_TASK)
    approval_role = master.require_task_type_assigned_role(db, APPROVAL_TASK)
    actor = str(user.get("email_normalized") or "")

    submitted = samples.confirm_draft_sample(
        db,
        allocation_id=allocation_id,
        version=int(draft["version"]),
        actor_email=actor,
    )
    if submitted is None:
        # Race: draft berubah / sudah dikonfirmasi proses lain.
        pending_now = samples.pending_sample(db, allocation_id)
        if pending_now is not None:
            return {
                "sample": pending_now,
                "duplicate": True,
                "phase": "menunggu_approval",
            }
        raise ProductionGateError(
            "Draft sampel berubah — muat ulang lalu konfirmasi lagi."
        )

    sample_key = build_task_key(SAMPLE_TASK, "po_line_allocation", _alloc_ref(alloc))
    tasks.upsert_task(
        db,
        task_type=SAMPLE_TASK,
        scope="po_line_allocation",
        assigned_role=sample_role,
        ref=_alloc_ref(alloc),
        label="Konfirmasi Sampel",
        context={
            "latest_version": submitted["version"],
            "status": submitted["status"],
            "needs_resubmit": False,
            "display_kind": "menunggu-approval",
        },
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": sample_key},
        {
            "$set": {
                "status": tasks.TASK_STATUS_DONE,
                "label": "Konfirmasi Sampel",
                "context.latest_version": submitted["version"],
                "context.status": submitted["status"],
                "context.needs_resubmit": False,
                "context.display_kind": "menunggu-approval",
                "context.active_draft_version": None,
            }
        },
    )

    approval = tasks.upsert_task(
        db,
        task_type=APPROVAL_TASK,
        scope="po_line_allocation",
        assigned_role=approval_role,
        ref=_alloc_ref(alloc),
        label="Approval Sampel",
        context={
            "sample_version": submitted["version"],
            "operator_email": alloc.get("operator_email"),
            "status": submitted["status"],
        },
    )
    tasks.tasks_collection(db).update_one(
        {"task_key": approval["task_key"]},
        {
            "$set": {
                "status": tasks.TASK_STATUS_OPEN,
                "context.sample_version": submitted["version"],
                "context.status": submitted["status"],
            }
        },
    )

    append_audit_event(
        db,
        action="production.sample.confirm",
        actor_email=actor,
        target_email=None,
        detail={
            "allocation_id": allocation_id,
            "version": submitted["version"],
            "evidence_id": submitted.get("evidence_id"),
            "idempotency_key": idempotency_key,
        },
    )
    return {
        "sample": submitted,
        "duplicate": False,
        "phase": "menunggu_approval",
    }


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
    _require_accepted_po(db, str(alloc["accurate_po_id"]))
    _assert_alloc_mutate(db, user, alloc, allow_approver=True)
    if not approved and not (reason or "").strip():
        raise ValueError("Alasan penolakan sampel wajib diisi.")

    # Validasi Master sebelum mutasi keputusan — cegah tugas parsial.
    if approved:
        master.require_task_type_assigned_role(db, PRINT_TASK)
    else:
        master.require_task_type_assigned_role(db, SAMPLE_TASK)

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
    sample_key = build_task_key(SAMPLE_TASK, "po_line_allocation", _alloc_ref(alloc))

    if approved:
        tasks.tasks_collection(db).update_one(
            {"task_key": approval_key},
            {
                "$set": {
                    "status": tasks.TASK_STATUS_DONE,
                    "context.status": sample["status"],
                    "context.sample_version": version,
                }
            },
        )
        _ensure_print_task(db, alloc)
    else:
        # Penolakan: keputusan approval selesai; pekerjaan kembali ke operator
        # untuk sampel ulang (versi baru). Tidak membuka tugas Designer.
        tasks.tasks_collection(db).update_one(
            {"task_key": approval_key},
            {
                "$set": {
                    "status": tasks.TASK_STATUS_DONE,
                    "context.status": sample["status"],
                    "context.sample_version": version,
                    "context.reject_reason": (reason or "").strip(),
                }
            },
        )
        sample_role = master.require_task_type_assigned_role(db, SAMPLE_TASK)
        # Identity task_key sama — bukan alokasi/tugas baru. Label UI: Sampel Ulang.
        tasks.upsert_task(
            db,
            task_type=SAMPLE_TASK,
            scope="po_line_allocation",
            assigned_role=sample_role,
            ref=_alloc_ref(alloc),
            label="Sampel Ulang",
            context={
                "latest_version": version,
                "status": sample["status"],
                "reject_reason": (reason or "").strip(),
                "needs_resubmit": True,
                "display_kind": "sampel-ulang",
            },
        )
        tasks.tasks_collection(db).update_one(
            {"task_key": sample_key},
            {
                "$set": {
                    "status": tasks.TASK_STATUS_OPEN,
                    "label": "Sampel Ulang",
                    "context.needs_resubmit": True,
                    "context.display_kind": "sampel-ulang",
                    "context.reject_reason": (reason or "").strip(),
                    "context.status": sample["status"],
                }
            },
        )

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
    print_role = master.require_task_type_assigned_role(db, PRINT_TASK)
    return tasks.upsert_task(
        db,
        task_type=PRINT_TASK,
        scope="po_line_allocation",
        assigned_role=print_role,
        ref=_alloc_ref(alloc),
        qty_target=float(alloc["qty"]),
        label="Cetak",
        context={"operator_email": alloc.get("operator_email")},
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
    """Cetak massal parsial dengan penjagaan jadwal + kuota Helper bersama.

    Pemesanan kuota dan penulisan ledger berada dalam satu transaksi replica
    set, sehingga proses yang mati tidak pernah menyisakan kuota terpakai
    tanpa konfirmasi cetak yang sah.
    """
    require_action(db, user, task_type=PRINT_TASK, action="confirm")
    if qty <= 0:
        raise ProductionQuantityError("Qty cetak harus lebih besar dari nol.")

    alloc = _require_allocation(db, allocation_id)
    accurate_po_id = str(alloc["accurate_po_id"])
    accurate_line_id = str(alloc["accurate_line_id"])
    _require_accepted_po(db, accurate_po_id)
    _assert_alloc_mutate(db, user, alloc)

    if screen_confirmed_qty(db, alloc) <= 0:
        raise ProductionGateError("Gate Screen belum terpenuhi untuk alokasi ini.")
    if samples.approved_sample(db, allocation_id) is None:
        raise ProductionGateError(
            "Sampel untuk alokasi ini belum disetujui — cetak massal terkunci."
        )

    # Master cetak + QC wajib sebelum mutasi/transaksi (hindari QC orphan).
    _ensure_print_task(db, alloc)
    master.require_task_type_assigned_role(db, QC_TASK)

    # Retry/double-click dikenali sebelum transaksi dimulai.
    existing = tasks.find_confirmation(db, idempotency_key)
    if existing is not None:
        result = {
            "task": tasks.get_task(db, print_task_key(alloc)),
            "confirmation": existing,
            "duplicate": True,
        }
        result["quota"] = helper_quota_state(
            db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
        )
        return result

    quota_before = helper_quota_state(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    if quota_before["qty_available"] < qty:
        raise ProductionQuantityError(
            f"Kuota Persiapan Cup tersisa {quota_before['qty_available']:g} — "
            f"tidak cukup untuk {qty:g}."
        )

    def _operation(session: Any) -> dict[str, Any]:
        # Serialize dengan revisi kapasitas line; muat ulang qty alokasi hidup.
        schedules.touch_line_guard(db, accurate_line_id, session=session)
        live = schedules.get_allocation(db, allocation_id, session=session)
        if live is None:
            raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}")
        live_qty = float(live.get("qty") or 0)
        pkey = print_task_key(live)
        # Target cetak = qty alokasi terkini (bukan stale upsert sebelumnya).
        tasks.tasks_collection(db).update_one(
            {"task_key": pkey},
            {"$set": {"qty_target": live_qty}},
            session=session,
        )
        return confirm_task(
            db,
            user,
            task_key=pkey,
            qty=qty,
            idempotency_key=idempotency_key,
            note=note,
            session=session,
            extra_reservation={
                "task_key": prep_task_key(accurate_po_id, accurate_line_id),
                "qty": qty,
                "error": (
                    "Kuota Persiapan Cup tidak mencukupi untuk konfirmasi cetak ini."
                ),
            },
        )

    result = tasks.run_in_transaction(db, _operation)
    _sync_qc_task(db, alloc)
    result["quota"] = helper_quota_state(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    return result


def _sync_qc_task(db: Database, alloc: dict[str, Any]) -> dict[str, Any]:
    """Target QC = total hasil cetak terkonfirmasi seluruh operator pada line."""
    qc_role = master.require_task_type_assigned_role(db, QC_TASK)
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
        assigned_role=qc_role,
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
    """Satu Qty berarti lolos QC sekaligus selesai Packing.

    Target operasional = Qty cetak sah line (bukan Qty PO penuh).
    Maksimum tambahan = cetak − packing terkonfirmasi.
    """
    po_doc = _require_accepted_po(db, accurate_po_id)
    _assert_po_branch(user, po_doc)
    key = qc_task_key(accurate_po_id, accurate_line_id)
    task = tasks.get_task(db, key)
    if task is None:
        raise LookupError("Tugas QC & Packing belum terbit — belum ada hasil cetak.")

    printed = printed_total_for_line(
        db, accurate_po_id=accurate_po_id, accurate_line_id=accurate_line_id
    )
    already = float((task or {}).get("qty_confirmed") or 0)
    remaining = max(printed - already, 0.0)
    if qty > remaining:
        raise ProductionQuantityError(
            f"Qty QC/Packing {qty:g} melebihi sisa hasil cetak yang belum dipacking "
            f"({remaining:g}). Qty cetak sah {printed:g}, sudah packing {already:g}."
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
