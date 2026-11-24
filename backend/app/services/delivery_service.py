"""Checkpoint E — Loading per Surat Jalan, tugas Driver, dan bukti pengiriman."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.domain.operational import TaskRef, build_task_key
from app.repositories import delivery_repository as deliveries
from app.repositories import evidence_repository as evidence
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id
from app.services import production_service as production
from app.services.audit_log import append_audit_event
from app.services.delivery_print_status import apply_schedule_print_status
from app.services.object_auth import (
    actor_email,
    assert_branch_access,
    filter_delivery_notes_for_actor,
)
from app.services.task_engine import TaskPermissionError, can_act, require_action

LOADING_TASK = "qty-loading-sj-berbeda"
LOADING_SJ_TASK = "loading-sj"
PRELIMINARY_LOADING_TASK = "loading-menunggu-sj"
DRIVER_TASK = "pengiriman"
DELIVERY_FAILURE_TASK = "pengiriman-gagal-koordinasi"

DELIVERY_PHOTO_SCOPE = "delivery-photo"
DELIVERY_SIGNATURE_SCOPE = "delivery-signature"

MIN_DELIVERY_PHOTOS = 2

DISCREPANCY_MESSAGE = (
    "Mohon konfirmasi ke administrasi karena qty loading dan SJ berbeda"
)


class LoadingValidationError(RuntimeError):
    """Loading terkunci karena Qty/identitas tidak cocok atau SJ direvisi."""


class LoadingCancelError(RuntimeError):
    """Pembatalan Loading tidak sah pada kondisi saat ini."""


class DeliveryEvidenceError(ValueError):
    """Bukti pengiriman belum memenuhi syarat."""


def _sj_ref(note: dict[str, Any]) -> TaskRef:
    """TaskRef Loading SJ keyed by SJ id — singular PO hanya bila legacy single-SO."""
    singular = str(note.get("accurate_po_id") or "").strip()
    if deliveries.note_is_multi_po(note):
        singular = ""
    return TaskRef(
        accurate_po_id=singular or None,
        po_no=str(note.get("po_no") or "") if singular else "",
        accurate_delivery_note_id=str(note["accurate_sj_id"]),
        sj_no=str(note.get("sj_no") or ""),
    )


def _po_line_index(po_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    return {
        str(i.get("accurate_line_id")): i
        for i in (header.get("items") or [])
        if isinstance(i, dict) and i.get("accurate_line_id")
    }


def has_successful_delivery(
    db: Database, accurate_sj_id: str, *, session: Any | None = None
) -> bool:
    if session is not None:
        return deliveries.has_successful_delivery_event(
            db, accurate_sj_id, session=session
        )
    return deliveries.has_successful_delivery_event(db, accurate_sj_id)


def note_all_parent_pos_accepted(
    db: Database,
    note: dict[str, Any],
    *,
    session: Any | None = None,
) -> bool:
    """True iff every parent PO of the SJ is Accepted (fail-closed).

    Empty/missing parent refs → False. Missing PO doc → False.
    Sumber otoritatif: ``operational.acceptance.status == accepted`` via
    ``is_po_accepted`` — tidak menduplikasi aturan acceptance.
    """
    from app.services.po_acceptance import is_po_accepted

    parent_ids = deliveries.note_parent_po_ids(note)
    if not parent_ids:
        return False
    for po_id in parent_ids:
        po_doc = get_by_accurate_id(db, po_id, session=session)
        if po_doc is None or not is_po_accepted(po_doc):
            return False
    return True


def _loading_sj_soft_archived(task: dict[str, Any] | None) -> bool:
    if not isinstance(task, dict):
        return False
    ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
    archive = ctx.get("loading_sj_archive")
    return isinstance(archive, dict) and archive.get("archived") is True


def _require_accepted_po_for_note(db: Database, note: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed: Loading/pengiriman hanya setelah SEMUA parent PO Accepted.

    Sumber otoritatif: ``operational.acceptance.status == accepted``.
    Tidak mengubah Gate Payment dan tidak auto-accept PO.
    Multi-PO: tidak memilih satu parent sembarang — semua harus accepted.
    """
    from app.services.po_acceptance import require_po_accepted

    parent_ids = deliveries.note_parent_po_ids(note)
    if not parent_ids:
        raise LookupError("Surat Jalan tanpa referensi PO — operasi ditolak.")
    first: dict[str, Any] | None = None
    for po_id in parent_ids:
        po_doc = get_by_accurate_id(db, po_id)
        if po_doc is None:
            raise LookupError(f"PO Accurate tidak ditemukan: {po_id}")
        require_po_accepted(po_doc)
        if first is None:
            first = po_doc
    assert first is not None
    return first


def ingest_delivery_note(
    db: Database, user: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Sinkron SJ + penerapan aturan revisi sebagian item (Opsi B Owner).

    Snapshot revisi, status Opsi B, dan audit di satu transaksi replica set.
    - Belum Loading → cukup simpan revisi; Loading biasa setelah valid.
    - Sudah Loading, belum terkirim → ``PERLU_LOADING_ULANG`` dan Loading lama
      harus dibatalkan secara teraudit sebelum Loading ulang.
    - Sudah terkirim → ``PERLU_KOREKSI_ADMINISTRASI``; Loading dan bukti
      historis tidak disentuh.
    """
    from app.services.index_preflight import require_delivery_txn_indexes

    require_delivery_txn_indexes(db)
    actor = str(user.get("email_normalized") or "")

    def _operation(session: Any) -> dict[str, Any]:
        from app.services.po_repair_maintenance import active_writer_fence

        fence = active_writer_fence()
        if fence is not None:
            fence.assert_valid(db, session=session)
        result = deliveries.apply_upsert_delivery_note(db, payload, session=session)
        note = result["delivery_note"]
        sj_id = str(note["accurate_sj_id"])

        # True no-op: SJ lama tanpa perubahan item/ship_date — jangan ensure ulang.
        # Insert pertama: source_changed=False (diff vs None kosong) tetapi was_insert=True
        # → wajib evaluasi ensure_loading_sj_task.
        if not result["source_changed"] and not result["was_insert"]:
            return {**result, "revision_status": note.get("revision_status")}

        if result["was_insert"] and not result["source_changed"]:
            loading_task = ensure_loading_sj_task(db, note, session=session)
            return {
                **result,
                "revision_status": note.get("revision_status"),
                "loading_task_key": (loading_task or {}).get("task_key"),
            }

        delivered = deliveries.has_successful_delivery_event(
            db, sj_id, session=session
        )
        active_loading = deliveries.get_active_loading_event(
            db, sj_id, session=session
        )
        reason = "Revisi item Surat Jalan dari Accurate."

        if delivered:
            status = deliveries.REVISION_NEEDS_ADMIN_CORRECTION
        elif active_loading is not None:
            status = deliveries.REVISION_NEEDS_RELOADING
        else:
            status = deliveries.REVISION_ACTIVE

        updated = deliveries.set_revision_status(
            db,
            accurate_sj_id=sj_id,
            status=status,
            reason=reason,
            session=session,
        )
        append_audit_event(
            db,
            action="delivery.note.revision",
            actor_email=actor,
            target_email=None,
            detail={
                "accurate_sj_id": sj_id,
                "sj_version": result["sj_version"],
                "changed_items": result["changed_items"],
                "revision_status": status,
                "active_loading_id": (active_loading or {}).get("loading_id"),
            },
            session=session,
        )
        loading_task = ensure_loading_sj_task(db, updated or note, session=session)
        return {
            **result,
            "delivery_note": updated,
            "revision_status": status,
            "loading_task_key": (loading_task or {}).get("task_key"),
        }

    return deliveries.run_delivery_mongo_txn(db, _operation)


def _packing_available_for_line(
    db: Database, *, accurate_po_id: str, accurate_line_id: str
) -> tuple[float, dict[str, Any] | None]:
    """qty_available = confirmed − consumed pada tugas qc-packing line."""
    qc_task = tasks.get_task(
        db, production.qc_task_key(accurate_po_id, accurate_line_id)
    )
    if qc_task is None:
        return 0.0, None
    packed = float(qc_task.get("qty_confirmed") or 0)
    used = float(qc_task.get("qty_consumed") or 0)
    return max(packed - used, 0.0), qc_task


def _non_cetak_loading_check(
    db: Database,
    *,
    accurate_po_id: str,
    line_id: str,
    sj_qty: float,
    item: dict[str, Any],
) -> dict[str, Any]:
    """Validasi Non-Cetak: ledger Packing bila enforced; legacy self-compare bila OFF."""
    from app.config import get_settings

    enforced = bool(get_settings().loading_packing_ledger_enforced)
    available, qc_task = _packing_available_for_line(
        db, accurate_po_id=accurate_po_id, accurate_line_id=line_id
    )
    if enforced:
        if qc_task is None:
            return {
                **item,
                "process_kind": "Non-Cetak",
                "qty_available": 0.0,
                "matched": False,
                "ledger_mode": "packing_required",
                "reason": (
                    "Ledger Packing Non-Cetak belum ada — Loading jalur baru "
                    "fail-closed (bukan menganggap qty tersedia)."
                ),
            }
        matched = available >= sj_qty > 0
        return {
            **item,
            "process_kind": "Non-Cetak",
            "qty_available": available,
            "matched": matched,
            "ledger_mode": "packing",
            "reason": (
                None
                if matched
                else (
                    f"Qty Packing Non-Cetak tersedia {available:g}, "
                    f"Qty SJ {sj_qty:g}."
                )
            ),
        }

    # Legacy (flag OFF): self-compare SJ — tidak memperketat data historis.
    # Jika ledger Packing sudah ada, pakai ledger (lebih ketat hanya bila data siap).
    if qc_task is not None:
        matched = available >= sj_qty > 0
        return {
            **item,
            "process_kind": "Non-Cetak",
            "qty_available": available,
            "matched": matched,
            "ledger_mode": "packing_optional",
            "reason": (
                None
                if matched
                else (
                    f"Qty Packing Non-Cetak tersedia {available:g}, "
                    f"Qty SJ {sj_qty:g}."
                )
            ),
        }
    return {
        **item,
        "process_kind": "Non-Cetak",
        "qty_available": sj_qty,
        "matched": sj_qty > 0,
        "ledger_mode": "legacy_sj_self",
        "reason": None if sj_qty > 0 else "Qty SJ tidak valid.",
    }


def validate_loading(
    db: Database,
    accurate_sj_id: str,
    user: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cek setiap item SJ terhadap Qty Packing tersedia (Cetak + Non-Cetak).

    Non-Cetak: ledger Packing bila ``V4_LOADING_PACKING_LEDGER_ENFORCED`` atau
    task packing sudah ada; legacy self-compare hanya saat flag OFF + tanpa ledger.
    """
    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    if user is not None:
        assert_branch_access(user, scope_object=note)
    if note.get("superseded_by"):
        raise LoadingValidationError(
            f"Surat Jalan {note['sj_no']} sudah digantikan oleh "
            f"{note['superseded_by']} — tidak berlaku untuk Loading."
        )

    parent_ids = deliveries.note_parent_po_ids(note)
    if not parent_ids:
        raise LookupError("Surat Jalan tanpa referensi PO — operasi ditolak.")
    po_by_id: dict[str, dict[str, Any]] = {}
    lines_by_po: dict[str, dict[str, dict[str, Any]]] = {}
    for po_id in parent_ids:
        po_doc = get_by_accurate_id(db, po_id)
        if po_doc is None:
            raise LookupError(f"PO Accurate tidak ditemukan: {po_id}")
        po_by_id[po_id] = po_doc
        lines_by_po[po_id] = _po_line_index(po_doc)

    checks: list[dict[str, Any]] = []
    for item in note["items"]:
        line_id = str(item["accurate_line_id"])
        accurate_po_id = deliveries.item_source_po_id(item, note)
        if not accurate_po_id or accurate_po_id not in lines_by_po:
            checks.append(
                {
                    **item,
                    "process_kind": None,
                    "qty_available": 0.0,
                    "matched": False,
                    "reason": (
                        f"Item line {line_id} tanpa parent PO terverifikasi — fail-closed."
                    ),
                }
            )
            continue
        lines = lines_by_po[accurate_po_id]
        po_line = lines.get(line_id)
        if po_line is None:
            checks.append(
                {
                    **item,
                    "accurate_po_id": accurate_po_id,
                    "process_kind": None,
                    "qty_available": 0.0,
                    "matched": False,
                    "reason": (
                        f"Item line {line_id} tidak ditemukan pada PO {accurate_po_id} "
                        "— identitas ambigu."
                    ),
                }
            )
            continue

        process_kind = str(po_line.get("process_kind") or "")
        sj_qty = float(item["qty"])
        if process_kind == "Cetak":
            available, _qc = _packing_available_for_line(
                db, accurate_po_id=accurate_po_id, accurate_line_id=line_id
            )
            matched = available >= sj_qty > 0
            reason = (
                None
                if matched
                else (
                    f"Qty QC & Packing tersedia {available:g}, "
                    f"Qty SJ {sj_qty:g}."
                )
            )
            checks.append(
                {
                    **item,
                    "accurate_po_id": accurate_po_id,
                    "process_kind": process_kind,
                    "qty_available": available,
                    "matched": matched,
                    "ledger_mode": "packing",
                    "reason": reason,
                }
            )
        elif process_kind == "Non-Cetak":
            check = _non_cetak_loading_check(
                db,
                accurate_po_id=accurate_po_id,
                line_id=line_id,
                sj_qty=sj_qty,
                item=item,
            )
            check["accurate_po_id"] = accurate_po_id
            checks.append(check)
        else:
            checks.append(
                {
                    **item,
                    "accurate_po_id": accurate_po_id,
                    "process_kind": process_kind or None,
                    "qty_available": 0.0,
                    "matched": False,
                    "reason": (
                        f"process_kind tidak dikenali untuk line {line_id} — fail-closed."
                    ),
                }
            )

    active_loading = deliveries.get_loading_event(db, accurate_sj_id)
    revision_status = note.get("revision_status") or deliveries.REVISION_ACTIVE
    result: dict[str, Any] = {
        "accurate_sj_id": accurate_sj_id,
        "sj_no": note["sj_no"],
        "sj_version": note.get("sj_version", 1),
        "revision_status": revision_status,
        "accurate_po_ids": parent_ids,
        "is_multi_po": deliveries.note_is_multi_po(note),
        "items": checks,
        "all_matched": all(c["matched"] for c in checks),
        "already_loaded": active_loading is not None,
        "active_loading_id": (active_loading or {}).get("loading_id"),
        "needs_reloading": revision_status == deliveries.REVISION_NEEDS_RELOADING,
        "delivered": has_successful_delivery(db, accurate_sj_id),
    }
    if not deliveries.note_is_multi_po(note):
        result["accurate_po_id"] = parent_ids[0]
    return result


def _prepare_delivery_txn(db: Database) -> str:
    """Verifikasi read-only indeks + Master di LUAR transaksi.

    Tidak createIndex, tidak seed/upsert Master. DDL dilarang di dalam transaksi.
    Fail-closed jika prasyarat belum siap (bootstrap eksplisit di luar request).
    """
    from app.services.index_preflight import require_delivery_txn_indexes

    require_delivery_txn_indexes(db)
    return master.require_task_type_assigned_role(db, DRIVER_TASK)


def loading_sj_task_key(accurate_sj_id: str) -> str:
    return build_task_key(
        LOADING_SJ_TASK,
        "delivery_note",
        TaskRef(accurate_delivery_note_id=str(accurate_sj_id)),
    )


def _require_loading_permission(db: Database, user: dict[str, Any], *, action: str) -> None:
    """Loading auth: saat loading-sj ON wajib grant loading-sj (tanpa fallback pengiriman).

    Flag OFF: kompatibilitas legacy — loading-sj ATAU pengiriman.* masih diterima.
    """
    from app.config import get_settings

    if get_settings().loading_sj_task_enabled:
        require_action(db, user, task_type=LOADING_SJ_TASK, action=action)
        return
    if can_act(db, user, task_type=LOADING_SJ_TASK, action=action):
        return
    if can_act(db, user, task_type=DRIVER_TASK, action=action):
        return
    raise TaskPermissionError(
        f"Akses ditolak untuk aksi '{action}' pada pekerjaan Loading "
        f"('{LOADING_SJ_TASK}' / '{DRIVER_TASK}')."
    )


def _assert_loading_assignee(db: Database, user: dict[str, Any], accurate_sj_id: str) -> None:
    """Jika tugas loading-sj punya assignee, hanya assignee (atau ops.full/SA) yang confirm."""
    from app.auth.permissions import PERM_OPERATIONS_FULL, has_permission
    from app.auth.roles import is_super_admin
    from app.config import get_settings

    if not get_settings().loading_sj_task_enabled:
        return
    task = tasks.get_task(db, loading_sj_task_key(accurate_sj_id))
    if task is None:
        return
    assigned = str((task.get("context") or {}).get("assigned_user_email") or "").strip().lower()
    if not assigned:
        return
    if is_super_admin(user.get("role")) or has_permission(user, PERM_OPERATIONS_FULL):
        return
    actor = (actor_email(user) or str(user.get("email_normalized") or "")).strip().lower()
    if actor != assigned:
        raise TaskPermissionError(
            "Hanya petugas yang ditugaskan pada Loading SJ ini yang boleh konfirmasi."
        )


def _loading_sj_task_ready_ids(
    db: Database,
    sj_ids: list[str],
    *,
    session: Any | None = None,
    extra_ready_sj_ids: list[str] | None = None,
) -> list[str]:
    """SJ yang sudah punya tugas loading-sj valid (bukan sekadar dokumen SJ)."""
    ready: list[str] = []
    seen: set[str] = set()
    for raw in list(extra_ready_sj_ids or []) + list(sj_ids):
        sj_id = str(raw or "").strip()
        if not sj_id or sj_id in seen:
            continue
        seen.add(sj_id)
        if tasks.get_task(db, loading_sj_task_key(sj_id), session=session) is None:
            continue
        ready.append(sj_id)
    return ready


def _set_preliminary_lifecycle_status(
    db: Database,
    task_key: str,
    *,
    can_close: bool,
    session: Any | None = None,
) -> None:
    """Selesai informatif hanya jika semua SJ terkait punya loading-sj.

    Tidak menyelesaikan Loading SJ / Driver — hanya menutup tujuan menunggu.
    """
    from datetime import datetime, timezone

    status = tasks.TASK_STATUS_DONE if can_close else tasks.TASK_STATUS_OPEN
    tasks.tasks_collection(db).update_one(
        {"task_key": task_key},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
        session=session,
    )


def upsert_preliminary_loading_for_line(
    db: Database,
    *,
    accurate_po_id: str,
    accurate_line_id: str,
    assigned_role: str,
    ref: TaskRef,
    qty_printed: float,
    label: str = "Loading — Menunggu Surat Jalan",
    prev_context: dict[str, Any] | None = None,
    extra_links: list[dict[str, str]] | None = None,
    extra_ready_sj_ids: list[str] | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Upsert tugas menunggu-SJ + tautan SJ (PO+line) + readiness loading-sj.

    SJ_EXISTS ≠ SJ_LOADING_TASK_READY. Preliminary boleh ``selesai`` hanya jika
    setiap SJ terkait memiliki tugas ``loading-sj``. Tanpa itu tetap visible.
    """
    po_id = str(accurate_po_id or "").strip()
    line_id = str(accurate_line_id or "").strip()
    prev = dict(prev_context or {})
    linked_ids = [str(x) for x in (prev.get("linked_sj_ids") or []) if str(x).strip()]
    linked_nos = [str(x) for x in (prev.get("linked_sj_nos") or []) if str(x).strip()]
    merged_links = list(
        deliveries.list_sj_links_for_po_line(
            db, accurate_po_id=po_id, accurate_line_id=line_id
        )
    )
    for link in extra_links or []:
        if isinstance(link, dict):
            merged_links.append(link)
    for link in merged_links:
        sj_id = str(link.get("accurate_sj_id") or "").strip()
        sj_no = str(link.get("sj_no") or "").strip()
        if sj_id and sj_id not in linked_ids:
            linked_ids.append(sj_id)
        if sj_no and sj_no not in linked_nos:
            linked_nos.append(sj_no)

    waiting_for_sj = len(linked_ids) == 0
    ready_ids = _loading_sj_task_ready_ids(
        db,
        linked_ids,
        session=session,
        extra_ready_sj_ids=extra_ready_sj_ids,
    )
    ready_set = set(ready_ids)
    missing_ids = [sj for sj in linked_ids if sj not in ready_set]
    loading_task_ready = (not waiting_for_sj) and len(missing_ids) == 0
    can_close = loading_task_ready

    if waiting_for_sj:
        display = "Menunggu Surat Jalan"
    elif loading_task_ready:
        display = "SJ tersedia — konfirmasi pada tugas Loading Surat Jalan"
    else:
        display = "SJ tersedia — tugas Loading belum siap"

    ctx: dict[str, Any] = {
        **prev,
        "qty_printed": qty_printed,
        "waiting_for_sj": waiting_for_sj,
        "sj_available": not waiting_for_sj,
        "sj_exists": not waiting_for_sj,
        "loading_task_ready": loading_task_ready,
        "ready_loading_sj_ids": ready_ids,
        "missing_loading_sj_ids": missing_ids,
        "display_status": display,
        "linked_sj_ids": linked_ids,
        "linked_sj_nos": linked_nos,
    }
    if not waiting_for_sj:
        ctx["association"] = "line_id_match"
    key = build_task_key(
        PRELIMINARY_LOADING_TASK,
        "po_line",
        TaskRef(accurate_po_id=po_id, accurate_line_id=line_id),
    )
    task = tasks.upsert_task(
        db,
        task_type=PRELIMINARY_LOADING_TASK,
        scope="po_line",
        assigned_role=assigned_role,
        ref=ref,
        qty_target=qty_printed,
        label=label,
        context=ctx,
        session=session,
    )
    _set_preliminary_lifecycle_status(
        db, key, can_close=can_close, session=session
    )
    return tasks.get_task(db, key, session=session) or task


def _link_preliminary_loading_for_note(
    db: Database,
    note: dict[str, Any],
    *,
    session: Any | None = None,
) -> None:
    """Tautkan tugas menunggu-SJ (po_line) ke SJ lewat accurate_line_id saja.

    Tidak menebak asosiasi dari qty/urutan/nama. Tidak menandai Loading SJ selesai
    dan tidak memanggil confirm_loading. Multi-PO: tautkan per parent PO item.
    """
    sj_id = str(note.get("accurate_sj_id") or "").strip()
    sj_no = str(note.get("sj_no") or "").strip()
    extra = (
        [{"accurate_sj_id": sj_id, "sj_no": sj_no}]
        if sj_id
        else []
    )
    # loading-sj baru dalam txn yang sama → anggap ready untuk SJ ini.
    extra_ready = [sj_id] if sj_id else []
    for item in note.get("items") or []:
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("accurate_line_id") or "").strip()
        po_id = deliveries.item_source_po_id(item, note)
        if not line_id or not po_id:
            continue
        key = build_task_key(
            PRELIMINARY_LOADING_TASK,
            "po_line",
            TaskRef(accurate_po_id=po_id, accurate_line_id=line_id),
        )
        existing = tasks.get_task(db, key, session=session)
        if existing is None:
            continue
        po_no = ""
        for assoc in note.get("parent_associations") or []:
            if isinstance(assoc, dict) and str(assoc.get("accurate_po_id")) == po_id:
                po_no = str(assoc.get("po_no") or "")
                break
        if not po_no:
            po_no = str(
                (existing.get("ref") or {}).get("po_no") or note.get("po_no") or ""
            )
        upsert_preliminary_loading_for_line(
            db,
            accurate_po_id=po_id,
            accurate_line_id=line_id,
            assigned_role=str(existing.get("assigned_role") or "helper"),
            ref=TaskRef(
                accurate_po_id=po_id,
                accurate_line_id=line_id,
                po_no=po_no,
                item_code=str((existing.get("ref") or {}).get("item_code") or ""),
            ),
            qty_printed=float(
                (existing.get("context") or {}).get("qty_printed")
                or existing.get("qty_target")
                or 0
            ),
            label=str(existing.get("label") or "Loading — Menunggu Surat Jalan"),
            prev_context=dict((existing.get("context") or {})),
            extra_links=extra,
            extra_ready_sj_ids=extra_ready,
            session=session,
        )


def ensure_loading_sj_task(
    db: Database,
    note: dict[str, Any],
    *,
    assigned_user_email: str | None = None,
    session: Any | None = None,
) -> dict[str, Any] | None:
    """Idempotent: satu tugas loading-sj per SJ bila flag aktif + Master siap.

    F08: create/upsert hanya bila SEMUA parent PO Accepted. SJ note tetap
    independen — gate ini tidak menghapus note. Soft-archived tasks are never
    recreated/unarchived here (Owner restore is separate).

    Resync/revisi SJ tidak menghapus ``assigned_user_email`` existing kecuali
    pemanggil mengirim assignee baru secara eksplisit (reassignment).
    """
    from app.config import get_settings

    if not get_settings().loading_sj_task_enabled:
        return None
    try:
        role = master.require_task_type_assigned_role(db, LOADING_SJ_TASK)
    except (LookupError, master.MissingMasterConfigError):
        return None
    ref = _sj_ref(note)
    key = build_task_key(LOADING_SJ_TASK, "delivery_note", ref)
    existing = tasks.get_task(db, key, session=session)

    # Soft-archive: preserve identity; do not auto-restore on reimport/ensure.
    if _loading_sj_soft_archived(existing):
        return existing

    # Fail-closed create gate — keep existing historical task if present.
    if not note_all_parent_pos_accepted(db, note, session=session):
        return existing

    prev_ctx = dict((existing or {}).get("context") or {})
    parent_ids = deliveries.note_parent_po_ids(note)
    ctx: dict[str, Any] = {
        **prev_ctx,
        "sj_no": note.get("sj_no"),
        "sj_version": note.get("sj_version"),
        "item_count": len(note.get("items") or []),
        "accurate_po_ids": parent_ids,
        "is_multi_po": deliveries.note_is_multi_po(note),
    }
    if assigned_user_email is not None:
        email = assigned_user_email.strip().lower()
        if email:
            ctx["assigned_user_email"] = email
        else:
            ctx.pop("assigned_user_email", None)
    task = tasks.upsert_task(
        db,
        task_type=LOADING_SJ_TASK,
        scope="delivery_note",
        assigned_role=role,
        ref=ref,
        label="Loading Surat Jalan",
        context=ctx,
        session=session,
    )
    _link_preliminary_loading_for_note(db, note, session=session)
    return task


def resume_loading_sj_tasks_for_accepted_po(
    db: Database,
    accurate_po_id: str,
    *,
    session: Any | None = None,
) -> dict[str, Any]:
    """Idempotent: ensure Loading SJ for active notes linked to an accepted PO.

    Called from PO acceptance transition *after* acceptance commits (no new
    timer/worker). Multi-parent SJ only create when *all* parents are accepted.

    Returns only task keys that are actually ready (accepted parents, not
    soft-archived). Deferred multi-parent / gated notes are listed separately —
    never reported as resumed.
    """
    from app.services.display_enrichment import list_active_delivery_notes_for_po

    po_id = str(accurate_po_id or "").strip()
    if not po_id:
        return {
            "ok": True,
            "task_keys": [],
            "deferred_sj_ids": [],
            "skipped_archived_sj_ids": [],
        }

    keys: list[str] = []
    deferred: list[str] = []
    skipped_archived: list[str] = []
    for note in list_active_delivery_notes_for_po(db, po_id):
        sj_id = str(note.get("accurate_sj_id") or "").strip()
        task = ensure_loading_sj_task(db, note, session=session)
        if _loading_sj_soft_archived(task):
            if sj_id and sj_id not in skipped_archived:
                skipped_archived.append(sj_id)
            continue
        if task is None or not note_all_parent_pos_accepted(
            db, note, session=session
        ):
            if sj_id and sj_id not in deferred:
                deferred.append(sj_id)
            continue
        key = str(task.get("task_key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return {
        "ok": True,
        "task_keys": keys,
        "deferred_sj_ids": deferred,
        "skipped_archived_sj_ids": skipped_archived,
    }


def assign_loading_sj_task(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    assigned_user_email: str,
) -> dict[str, Any]:
    """KP/Admin (edit) menugaskan satu user Helper/Driver ke Loading SJ."""
    _require_loading_permission(db, user, action="edit")
    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    assert_branch_access(user, scope_object=note)
    # Assignment is an operational mutation — same Accepted gate as Loading confirm.
    _require_accepted_po_for_note(db, note)
    email = assigned_user_email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("assigned_user_email wajib email valid.")
    task = ensure_loading_sj_task(db, note, assigned_user_email=email)
    if task is None:
        raise LookupError(
            "Tugas Loading SJ belum aktif — set V4_LOADING_SJ_TASK_ENABLED dan "
            "bootstrap Master task_type=loading-sj."
        )
    from pymongo import ReturnDocument

    updated = tasks.tasks_collection(db).find_one_and_update(
        {"task_key": task["task_key"]},
        {"$set": {"context.assigned_user_email": email}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    append_audit_event(
        db,
        action="delivery.loading.assign",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=email,
        detail={"accurate_sj_id": accurate_sj_id, "task_key": task["task_key"]},
    )
    return updated or task


def confirm_loading_match(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Aksi Sesuai: validasi ulang + confirm_loading + selesaikan tugas loading-sj."""
    result = confirm_loading(
        db, user, accurate_sj_id=accurate_sj_id, idempotency_key=idempotency_key
    )
    key = loading_sj_task_key(accurate_sj_id)
    task = tasks.get_task(db, key)
    if task is not None and task.get("status") != tasks.TASK_STATUS_DONE:
        tasks.tasks_collection(db).update_one(
            {"task_key": key},
            {"$set": {"status": tasks.TASK_STATUS_DONE}},
        )
        result["loading_task_key"] = key
    return result


def reject_loading_mismatch(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Aksi Tidak Sesuai: discrepancy KP (idempotent) — tidak membuka delivery."""
    _require_loading_permission(db, user, action="confirm")
    _assert_loading_assignee(db, user, accurate_sj_id)
    note_scope = deliveries.get_delivery_note(db, accurate_sj_id)
    if note_scope is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    assert_branch_access(user, scope_object=note_scope)
    # Owner: aksi Loading (Sesuai/Tidak Sesuai) hanya setelah PO Accepted.
    _require_accepted_po_for_note(db, note_scope)
    # Paksa mismatch path: raise_quantity_discrepancy menolak jika all_matched.
    # Jika validasi ternyata cocok, tetap buat catatan "Tidak Sesuai" via context.
    validation = validate_loading(db, accurate_sj_id, user=user)
    disc_key = build_task_key(LOADING_TASK, "delivery_note", _sj_ref(note_scope))
    existing_disc = tasks.get_task(db, disc_key)
    if existing_disc is not None:
        return {
            "discrepancy_task": existing_disc,
            "validation": validation,
            "duplicate": True,
        }
    if validation.get("all_matched"):
        # Owner: user menyatakan Tidak Sesuai — catat discrepancy dengan alasan manual.
        note = deliveries.get_delivery_note(db, accurate_sj_id)
        from app.services.index_preflight import require_delivery_txn_indexes

        require_delivery_txn_indexes(db)
        assigned_role = master.require_task_type_assigned_role(db, LOADING_TASK)
        actor = str(user.get("email_normalized") or "")
        context = {
            "message": DISCREPANCY_MESSAGE,
            "sj_no": (note or {}).get("sj_no"),
            "manual_mismatch": True,
            "reason": (reason or "").strip() or "Petugas menandai Tidak Sesuai.",
            "mismatched_items": [],
        }

        def _operation(session: Any) -> dict[str, Any]:
            task = tasks.upsert_task(
                db,
                task_type=LOADING_TASK,
                scope="delivery_note",
                assigned_role=assigned_role,
                ref=_sj_ref(note or {}),
                label="Qty Loading dan SJ Berbeda",
                coordination=True,
                context=context,
                session=session,
            )
            append_audit_event(
                db,
                action="delivery.loading.mismatch_declared",
                actor_email=actor,
                target_email=None,
                detail={
                    "accurate_sj_id": accurate_sj_id,
                    "task_key": task["task_key"],
                    "reason": context["reason"],
                },
                session=session,
            )
            return task

        disc = tasks.run_in_transaction(db, _operation)
        return {"discrepancy_task": disc, "validation": validation, "duplicate": False}

    disc = raise_quantity_discrepancy(db, user, accurate_sj_id=accurate_sj_id)
    return {"discrepancy_task": disc, "validation": validation, "duplicate": False}


def confirm_loading(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    idempotency_key: str,
    assigned_driver_email: str | None = None,
) -> dict[str, Any]:
    """Satu aksi Loading untuk seluruh item SJ — Qty Packing + Loading + audit atomik.

    Owner: Loading actor is NOT the courier. Do not stamp driver assignment.
    ``assigned_driver_email`` is accepted for API compatibility but ignored
    (no mandatory courier selection).
    """
    _ = assigned_driver_email
    _require_loading_permission(db, user, action="confirm")
    _assert_loading_assignee(db, user, accurate_sj_id)
    assigned_role = _prepare_delivery_txn(db)

    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    # Branch + Accepted before idempotent early-return — no cross-branch leak.
    assert_branch_access(user, scope_object=note)
    _require_accepted_po_for_note(db, note)

    existing = deliveries.get_loading_event(db, accurate_sj_id)
    if existing is not None:
        if note.get("revision_status") == deliveries.REVISION_NEEDS_RELOADING:
            raise LoadingValidationError(
                "Surat Jalan direvisi: batalkan Loading lama terlebih dahulu "
                "sebelum Loading ulang."
            )
        return {
            "loading": existing,
            "driver_task_key": build_task_key(
                DRIVER_TASK, "delivery_note", _sj_ref(note)
            ),
            "duplicate": True,
        }

    validation = validate_loading(db, accurate_sj_id)
    if not validation["all_matched"]:
        raise LoadingValidationError(
            "Loading terkunci: Qty item tidak cocok dengan Surat Jalan."
        )

    actor = actor_email(user) or str(user.get("email_normalized") or "")
    sequence = deliveries.next_loading_sequence(db, accurate_sj_id)

    def _operation(session: Any) -> dict[str, Any]:
        # Race: Loading aktif muncul setelah cek di luar txn.
        raced = deliveries.loading_events_collection(db).find_one(
            {
                "accurate_sj_id": str(accurate_sj_id),
                "status": deliveries.LOADING_ACTIVE,
            },
            {"_id": 0},
            session=session,
        )
        if raced is not None:
            return {"loading": raced, "driver_task": None, "duplicate": True}

        consumed_keys: set[tuple[str, str]] = set()
        for check in validation["items"]:
            ledger_mode = str(check.get("ledger_mode") or "")
            uses_packing = check["process_kind"] == "Cetak" or ledger_mode.startswith(
                "packing"
            )
            if not uses_packing:
                continue
            line_id = str(check["accurate_line_id"])
            item_po = str(
                check.get("accurate_po_id")
                or deliveries.item_source_po_id(check, note)
                or ""
            )
            if not item_po:
                raise LoadingValidationError(
                    f"Item line {line_id} tanpa parent PO — Loading ditolak."
                )
            reserved = tasks.reserve_quota(
                db,
                task_key=production.qc_task_key(item_po, line_id),
                qty=float(check["qty"]),
                session=session,
            )
            if not reserved:
                raise LoadingValidationError(
                    f"Qty Packing untuk item line {line_id} "
                    "tidak lagi mencukupi."
                )
            consumed_keys.add((item_po, line_id))
        parent_ids = deliveries.note_parent_po_ids(note)
        loading_po_id = (
            ""
            if deliveries.note_is_multi_po(note)
            else str(note.get("accurate_po_id") or (parent_ids[0] if parent_ids else ""))
        )
        loading = deliveries.insert_loading_event(
            db,
            accurate_sj_id=str(note["accurate_sj_id"]),
            accurate_po_id=loading_po_id,
            accurate_po_ids=parent_ids,
            sj_no=str(note["sj_no"]),
            items=[
                {
                    "sj_line_id": c["sj_line_id"],
                    "accurate_line_id": c["accurate_line_id"],
                    "accurate_po_id": str(
                        c.get("accurate_po_id")
                        or deliveries.item_source_po_id(c, note)
                        or ""
                    ),
                    "item_code": c["item_code"],
                    "qty": c["qty"],
                    "unit": c["unit"],
                    "process_kind": c["process_kind"],
                    "packing_consumed": (
                        str(c.get("accurate_po_id") or deliveries.item_source_po_id(c, note) or ""),
                        str(c["accurate_line_id"]),
                    )
                    in consumed_keys,
                }
                for c in validation["items"]
            ],
            actor_email=actor,
            idempotency_key=idempotency_key,
            sj_version=int(note.get("sj_version") or 1),
            sequence=sequence,
            session=session,
        )
        # Hanya clear status revisi jika snapshot masih versi yang di-Loading.
        # Race revisi bersamaan: versi naik → jangan timpa PERLU_LOADING_ULANG.
        note_now = deliveries.delivery_notes_collection(db).find_one(
            {"accurate_sj_id": str(accurate_sj_id)},
            {"_id": 0, "sj_version": 1},
            session=session,
        ) or {}
        if int(note_now.get("sj_version") or 1) == int(loading.get("sj_version") or 1):
            deliveries.set_revision_status(
                db,
                accurate_sj_id=accurate_sj_id,
                status=deliveries.REVISION_ACTIVE,
                reason="Loading dikonfirmasi untuk versi SJ terbaru.",
                session=session,
            )
        else:
            deliveries.set_revision_status(
                db,
                accurate_sj_id=accurate_sj_id,
                status=deliveries.REVISION_NEEDS_RELOADING,
                reason="Surat Jalan direvisi selama konfirmasi Loading.",
                session=session,
            )
        # Owner: never stamp courier from Loading actor; leave historical metadata as-is.
        note_for_task = dict(note)
        driver_task = _ensure_driver_task(
            db, note_for_task, loading, assigned_role=assigned_role, session=session
        )
        append_audit_event(
            db,
            action="delivery.loading.confirm",
            actor_email=actor,
            target_email=None,
            detail={
                "accurate_sj_id": accurate_sj_id,
                "sj_no": note_for_task["sj_no"],
                "loading_id": loading["loading_id"],
                "sj_version": loading.get("sj_version"),
                "loading_actor_email": actor,
                "item_count": len(loading["items"]),
                "qty_by_line": [
                    {
                        "accurate_line_id": i["accurate_line_id"],
                        "qty": i["qty"],
                        "process_kind": i.get("process_kind"),
                    }
                    for i in loading["items"]
                ],
            },
            session=session,
        )
        return {"loading": loading, "driver_task": driver_task, "duplicate": False}

    try:
        result = tasks.run_in_transaction(db, _operation)
    except DuplicateKeyError:
        # Unique partial index aktif Loading: konfirmasi paralel kalah → treat as duplicate.
        existing_after = deliveries.get_loading_event(db, accurate_sj_id)
        if existing_after is None:
            raise
        return {
            "loading": existing_after,
            "driver_task_key": build_task_key(
                DRIVER_TASK, "delivery_note", _sj_ref(note)
            ),
            "duplicate": True,
        }

    if result["duplicate"]:
        return {
            "loading": result["loading"],
            "driver_task_key": build_task_key(
                DRIVER_TASK, "delivery_note", _sj_ref(note)
            ),
            "duplicate": True,
        }
    return {
        "loading": result["loading"],
        "driver_task_key": result["driver_task"]["task_key"],
        "duplicate": False,
    }


def _ensure_driver_task(
    db: Database,
    note: dict[str, Any],
    loading: dict[str, Any],
    *,
    assigned_role: str | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Satu tugas Driver per SJ, terikat pada Loading aktif (bukan per item)."""
    role = assigned_role or master.require_task_type_assigned_role(db, DRIVER_TASK)
    task = tasks.upsert_task(
        db,
        task_type=DRIVER_TASK,
        scope="delivery_note",
        assigned_role=role,
        ref=_sj_ref(note),
        label="Pengiriman",
        context={
            "destination": note.get("destination"),
            "shipment": note.get("shipment"),
            "ship_date": note.get("ship_date"),
            "item_count": len(note.get("items") or []),
            "loading_id": loading["loading_id"],
            "sj_version": loading.get("sj_version"),
            "loading_cancelled": False,
            "assigned_driver_email": note.get("assigned_driver_email")
            or note.get("driver_email"),
            "driver_email": note.get("driver_email")
            or note.get("assigned_driver_email"),
        },
        session=session,
    )
    # Loading ulang mengaktifkan kembali tugas yang sama dengan Loading baru.
    tasks.tasks_collection(db).update_one(
        {"task_key": task["task_key"]},
        {
            "$set": {
                "status": tasks.TASK_STATUS_OPEN,
                "context.assigned_driver_email": note.get("assigned_driver_email")
                or note.get("driver_email"),
                "context.driver_email": note.get("driver_email")
                or note.get("assigned_driver_email"),
                "context.loading_id": loading["loading_id"],
                "context.sj_version": loading.get("sj_version"),
                "context.loading_cancelled": False,
            }
        },
        session=session,
    )
    return tasks.get_task(db, task["task_key"], session=session) or task


def _require_active_loading(db: Database, accurate_sj_id: str) -> dict[str, Any]:
    """Driver hanya boleh bekerja atas Loading yang masih aktif."""
    loading = deliveries.get_loading_event(db, accurate_sj_id)
    if loading is None:
        raise LoadingValidationError(
            "Tidak ada Loading aktif untuk SJ ini — Loading ulang diperlukan."
        )
    note = deliveries.get_delivery_note(db, accurate_sj_id) or {}
    if note.get("revision_status") == deliveries.REVISION_NEEDS_RELOADING:
        raise LoadingValidationError(
            "Surat Jalan direvisi: konfirmasi pengiriman diblokir sampai "
            "Loading dibatalkan dan dikonfirmasi ulang."
        )
    if int(loading.get("sj_version") or 1) != int(note.get("sj_version") or 1):
        raise LoadingValidationError(
            "Loading aktif mengacu versi SJ lama — Loading ulang diperlukan."
        )
    return loading


def cancel_loading(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    reason: str,
) -> dict[str, Any]:
    """Batalkan Loading aktif; reversal Qty + Driver + audit dalam satu transaksi."""
    _require_loading_permission(db, user, action="cancel")
    if not reason.strip():
        raise ValueError("Alasan pembatalan Loading wajib diisi.")
    _prepare_delivery_txn(db)

    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    assert_branch_access(user, scope_object=note)

    loading = deliveries.get_loading_event(db, accurate_sj_id)
    if loading is None:
        # Fast path di luar txn; race delivery sukses tetap dicek di dalam txn.
        if has_successful_delivery(db, accurate_sj_id):
            raise LoadingCancelError(
                "Pengiriman sudah berhasil dengan bukti penerimaan — Loading tidak "
                "dapat dibatalkan."
            )
        return {"cancelled": False, "duplicate": True, "loading": None}

    actor = str(user.get("email_normalized") or "")
    loading_id = str(loading["loading_id"])
    driver_key = build_task_key(DRIVER_TASK, "delivery_note", _sj_ref(note))
    cancel_reason = reason.strip()

    def _operation(session: Any) -> dict[str, Any]:
        # Race cancel × delivery success: cek ulang di dalam transaksi.
        if deliveries.has_successful_delivery_event(
            db, accurate_sj_id, session=session
        ):
            raise LoadingCancelError(
                "Pengiriman sudah berhasil dengan bukti penerimaan — Loading tidak "
                "dapat dibatalkan."
            )
        cancelled = deliveries.cancel_loading_event(
            db,
            loading_id=loading_id,
            actor_email=actor,
            reason=cancel_reason,
            session=session,
        )
        if cancelled is None:
            # Pembatalan paralel/berulang: tidak ada reversal kedua.
            return {"cancelled": False, "duplicate": True, "loading": None}

        reverted_items: list[dict[str, Any]] = []
        qty_reverted_total = 0.0
        # Reversal hanya untuk item yang benar-benar mengonsumsi ledger Packing.
        for item in cancelled.get("items") or []:
            packing_consumed = item.get("packing_consumed")
            if packing_consumed is None:
                # Dokumen Loading lama: Cetak diasumsikan consumed; Non-Cetak tidak.
                packing_consumed = item.get("process_kind") == "Cetak"
            if not packing_consumed:
                continue
            qty = float(item["qty"])
            item_po = str(
                item.get("accurate_po_id") or cancelled.get("accurate_po_id") or ""
            )
            if not item_po:
                raise LoadingCancelError(
                    "Loading historis tanpa parent PO per item — "
                    "reversal Packing fail-closed."
                )
            tasks.tasks_collection(db).update_one(
                {
                    "task_key": production.qc_task_key(
                        item_po, str(item["accurate_line_id"])
                    )
                },
                {"$inc": {"qty_consumed": -qty}},
                session=session,
            )
            reverted_items.append(
                {
                    "accurate_line_id": item["accurate_line_id"],
                    "qty": qty,
                    "process_kind": item.get("process_kind"),
                }
            )
            qty_reverted_total += qty

        deliveries.set_revision_status(
            db,
            accurate_sj_id=accurate_sj_id,
            status=deliveries.REVISION_NEEDS_RELOADING,
            reason=cancel_reason,
            session=session,
        )
        # Tugas Driver tidak actionable untuk Loading yang dibatalkan.
        tasks.tasks_collection(db).update_one(
            {"task_key": driver_key},
            {
                "$set": {
                    "context.loading_cancelled": True,
                    "context.cancelled_loading_id": loading_id,
                    "status": tasks.TASK_STATUS_OPEN,
                }
            },
            session=session,
        )
        append_audit_event(
            db,
            action="delivery.loading.cancel",
            actor_email=actor,
            target_email=None,
            detail={
                "accurate_sj_id": accurate_sj_id,
                "sj_no": note.get("sj_no"),
                "loading_id": loading_id,
                "reason": cancel_reason,
                "actor_email": actor,
                "cancelled_at": (cancelled.get("cancelled_at") or "").isoformat()
                if hasattr(cancelled.get("cancelled_at"), "isoformat")
                else str(cancelled.get("cancelled_at") or ""),
                "reverted_items": reverted_items,
                "qty_reverted_total": qty_reverted_total,
                "sj_version_at_cancel": note.get("sj_version"),
            },
            session=session,
        )
        return {"cancelled": True, "duplicate": False, "loading": cancelled}

    return tasks.run_in_transaction(db, _operation)


def raise_quantity_discrepancy(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
) -> dict[str, Any]:
    """Tombol Perbedaan Qty → tugas koordinasi Kepala Produksi (baris merah)."""
    from app.services.index_preflight import require_delivery_txn_indexes

    _require_loading_permission(db, user, action="confirm")
    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    assert_branch_access(user, scope_object=note)
    _assert_loading_assignee(db, user, accurate_sj_id)
    # Owner: aksi Loading (termasuk discrepancy) hanya setelah PO Accepted.
    _require_accepted_po_for_note(db, note)

    validation = validate_loading(db, accurate_sj_id, user=user)
    if validation["all_matched"]:
        raise LoadingValidationError(
            "Qty Loading sudah cocok dengan Surat Jalan — tugas koordinasi tidak dibuat."
        )
    # Fail-closed di luar txn: tidak seed Master, tidak createIndex.
    require_delivery_txn_indexes(db)
    assigned_role = master.require_task_type_assigned_role(db, LOADING_TASK)
    mismatched = [c for c in validation["items"] if not c["matched"]]
    actor = str(user.get("email_normalized") or "")
    context = {
        "message": DISCREPANCY_MESSAGE,
        "sj_no": note["sj_no"],
        "mismatched_items": [
            {
                "accurate_line_id": c["accurate_line_id"],
                "item_code": c.get("item_code"),
                "item_name": c.get("item_name"),
                "qty_sj": c["qty"],
                "qty_available": c["qty_available"],
                "reason": c["reason"],
            }
            for c in mismatched
        ],
    }

    def _operation(session: Any) -> dict[str, Any]:
        task = tasks.upsert_task(
            db,
            task_type=LOADING_TASK,
            scope="delivery_note",
            assigned_role=assigned_role,
            ref=_sj_ref(note),
            label="Qty Loading dan SJ Berbeda",
            coordination=True,
            context=context,
            session=session,
        )
        append_audit_event(
            db,
            action="delivery.loading.discrepancy",
            actor_email=actor,
            target_email=None,
            detail={
                "accurate_sj_id": accurate_sj_id,
                "mismatch_count": len(mismatched),
                "task_key": task["task_key"],
            },
            session=session,
        )
        return task

    return tasks.run_in_transaction(db, _operation)


def confirm_coordination(
    db: Database,
    user: dict[str, Any],
    *,
    task_key: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Konfirmasi koordinasi: menandai tugas selesai, tidak membuka Loading."""
    task = tasks.get_task(db, task_key)
    if task is None:
        raise LookupError(f"Tugas koordinasi tidak ditemukan: {task_key}")
    task_type = str(task.get("task_type") or "")
    if task_type not in {LOADING_TASK, DELIVERY_FAILURE_TASK}:
        raise ValueError("Tugas ini bukan tugas koordinasi.")
    require_action(db, user, task_type=task_type, action="confirm")

    if tasks.find_confirmation(db, idempotency_key) is not None:
        return {"task": tasks.get_task(db, task_key), "duplicate": True}

    result = tasks.confirm_task_qty(
        db,
        task_key=task_key,
        qty=1,
        actor_email=str(user.get("email_normalized") or ""),
        actor_role="production-head",
        idempotency_key=idempotency_key,
        note="Koordinasi administrasi telah dilakukan.",
    )
    append_audit_event(
        db,
        action=f"delivery.coordination.{task_type}",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={"task_key": task_key, "ref": task.get("ref") or {}},
    )
    return result


def _abandon_staged_delivery_evidence(
    db: Database, *, evidence_ids: list[str], actor_email: str
) -> None:
    """Recovery non-atomik: soft-delete staging bila transaksi bisnis gagal.

    Kegagalan cleanup tidak boleh mengubah status bisnis (sukses/gagal tetap
    ditentukan oleh transaksi Mongo); orphan GridFS ditangani maintenance.
    """
    for evidence_id in evidence_ids:
        try:
            evidence.soft_delete_evidence(
                db,
                evidence_id=evidence_id,
                actor_email=actor_email,
                reason="recovery: konfirmasi pengiriman tidak commit",
            )
        except Exception:  # noqa: BLE001 — kompensasi terbaik; jangan naikkan status palsu
            continue


def confirm_delivery_success(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    recipient_name: str,
    photos: list[tuple[str, str, bytes]],
    signature: tuple[str, str, bytes],
) -> dict[str, Any]:
    """Minimal dua foto + satu tanda tangan + nama penerima; kurir dari sesi.

    GridFS/evidence disimpan sebagai staging di luar transaksi. Event sukses,
    status Driver, tautan metadata evidence, dan audit bisnis di satu transaksi
    Mongo. Staging orphan di-soft-delete jika commit gagal (file historis tetap).
    """
    from app.services.index_preflight import require_delivery_txn_indexes
    from pymongo.errors import DuplicateKeyError

    require_action(db, user, task_type=DRIVER_TASK, action="confirm")
    require_delivery_txn_indexes(db)

    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    # Branch + Accepted before idempotent early-return — no protected-data leak.
    assert_branch_access(user, scope_object=note)
    _require_accepted_po_for_note(db, note)

    # Idempotensi: sudah sukses → kembalikan event yang sama, tanpa bukti baru.
    existing_success = deliveries.delivery_events_collection(db).find_one(
        {
            "accurate_sj_id": str(accurate_sj_id),
            "status": deliveries.DELIVERY_SUCCESS,
        },
        {"_id": 0},
    )
    if existing_success is not None:
        return existing_success

    _require_active_loading(db, accurate_sj_id)
    if len(photos) < MIN_DELIVERY_PHOTOS:
        raise DeliveryEvidenceError(
            f"Konfirmasi pengiriman memerlukan minimal {MIN_DELIVERY_PHOTOS} foto."
        )
    if not signature or not signature[2]:
        raise DeliveryEvidenceError("Tanda tangan penerima wajib diisi.")
    if not recipient_name.strip():
        raise DeliveryEvidenceError("Nama penerima wajib diisi.")

    courier = str(user.get("email_normalized") or "")
    recipient = recipient_name.strip()
    staged_ids: list[str] = []
    photo_ids: list[str] = []

    try:
        for filename, content_type, content in photos:
            stored = evidence.store_evidence(
                db,
                scope=DELIVERY_PHOTO_SCOPE,
                scope_id=str(accurate_sj_id),
                filename=filename,
                content_type=content_type,
                content=content,
                uploaded_by=courier,
                kind="delivery-photo",
                context={
                    "accurate_po_id": note.get("accurate_po_id"),
                    "accurate_po_ids": deliveries.note_parent_po_ids(note),
                    "accurate_sj_id": str(accurate_sj_id),
                    "delivery_commit": "pending",
                },
            )
            photo_ids.append(stored["evidence_id"])
            staged_ids.append(stored["evidence_id"])

        sig_stored = evidence.store_evidence(
            db,
            scope=DELIVERY_SIGNATURE_SCOPE,
            scope_id=str(accurate_sj_id),
            filename=signature[0],
            content_type=signature[1],
            content=signature[2],
            uploaded_by=courier,
            kind="delivery-signature",
            context={
                "recipient_name": recipient,
                "accurate_sj_id": str(accurate_sj_id),
                "delivery_commit": "pending",
            },
        )
        staged_ids.append(sig_stored["evidence_id"])
    except Exception:
        _abandon_staged_delivery_evidence(
            db, evidence_ids=staged_ids, actor_email=courier
        )
        raise

    driver_key = build_task_key(DRIVER_TASK, "delivery_note", _sj_ref(note))
    all_evidence_ids = list(photo_ids) + [sig_stored["evidence_id"]]

    def _operation(session: Any) -> dict[str, Any]:
        # Race cancel / revisi / konfirmasi paralel — cek ulang di dalam txn.
        if deliveries.has_successful_delivery_event(
            db, accurate_sj_id, session=session
        ):
            existing = deliveries.delivery_events_collection(db).find_one(
                {
                    "accurate_sj_id": str(accurate_sj_id),
                    "status": deliveries.DELIVERY_SUCCESS,
                },
                {"_id": 0},
                session=session,
            )
            return {"event": existing, "duplicate": True}

        active = deliveries.get_active_loading_event(
            db, accurate_sj_id, session=session
        )
        if active is None:
            raise LoadingValidationError(
                "Tidak ada Loading aktif untuk SJ ini — Loading ulang diperlukan."
            )
        note_now = deliveries.delivery_notes_collection(db).find_one(
            {"accurate_sj_id": str(accurate_sj_id)},
            {"_id": 0, "sj_version": 1, "revision_status": 1},
            session=session,
        ) or {}
        if note_now.get("revision_status") == deliveries.REVISION_NEEDS_RELOADING:
            raise LoadingValidationError(
                "Surat Jalan direvisi: konfirmasi pengiriman diblokir sampai "
                "Loading dibatalkan dan dikonfirmasi ulang."
            )
        if int(active.get("sj_version") or 1) != int(note_now.get("sj_version") or 1):
            raise LoadingValidationError(
                "Loading aktif mengacu versi SJ lama — Loading ulang diperlukan."
            )

        parent_ids = deliveries.note_parent_po_ids(note)
        event_po_id = (
            ""
            if deliveries.note_is_multi_po(note)
            else str(note.get("accurate_po_id") or (parent_ids[0] if parent_ids else ""))
        )
        event = deliveries.insert_delivery_event(
            db,
            accurate_sj_id=str(accurate_sj_id),
            accurate_po_id=event_po_id,
            accurate_po_ids=parent_ids,
            attempt=deliveries.next_delivery_attempt(
                db, accurate_sj_id, session=session
            ),
            status=deliveries.DELIVERY_SUCCESS,
            courier_email=courier,
            recipient_name=recipient,
            photo_evidence_ids=photo_ids,
            signature_evidence_id=sig_stored["evidence_id"],
            failure_reason=None,
            session=session,
        )
        evidence.attach_evidence_to_delivery(
            db,
            evidence_ids=all_evidence_ids,
            accurate_sj_id=str(accurate_sj_id),
            delivery_event_id=str(event["delivery_event_id"]),
            attempt=int(event["attempt"]),
            session=session,
        )
        tasks.tasks_collection(db).update_one(
            {"task_key": driver_key},
            {
                "$set": {
                    "status": tasks.TASK_STATUS_DONE,
                    "context.last_delivery_status": deliveries.DELIVERY_SUCCESS,
                }
            },
            session=session,
        )
        append_audit_event(
            db,
            action="delivery.confirm.success",
            actor_email=courier,
            target_email=None,
            detail={
                "accurate_sj_id": accurate_sj_id,
                "attempt": event["attempt"],
                "photo_count": len(photo_ids),
                "recipient_name": recipient,
                "delivery_event_id": event["delivery_event_id"],
                "photo_evidence_ids": photo_ids,
                "signature_evidence_id": sig_stored["evidence_id"],
            },
            session=session,
        )
        return {"event": event, "duplicate": False}

    try:
        result = tasks.run_in_transaction(db, _operation)
    except DuplicateKeyError:
        # Unique partial sukses per SJ: konfirmasi paralel kalah → event yang menang.
        existing = deliveries.delivery_events_collection(db).find_one(
            {
                "accurate_sj_id": str(accurate_sj_id),
                "status": deliveries.DELIVERY_SUCCESS,
            },
            {"_id": 0},
        )
        _abandon_staged_delivery_evidence(
            db, evidence_ids=staged_ids, actor_email=courier
        )
        if existing is None:
            raise
        return existing
    except Exception:
        _abandon_staged_delivery_evidence(
            db, evidence_ids=staged_ids, actor_email=courier
        )
        raise

    if result.get("duplicate"):
        _abandon_staged_delivery_evidence(
            db, evidence_ids=staged_ids, actor_email=courier
        )
    return result["event"]


def confirm_delivery_failure(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    reason: str,
) -> dict[str, Any]:
    """Pengiriman gagal → tugas koordinasi Kepala Produksi, tanpa reschedule."""
    from app.services.index_preflight import require_delivery_txn_indexes

    require_action(db, user, task_type=DRIVER_TASK, action="confirm")
    if not reason.strip():
        raise DeliveryEvidenceError("Alasan pengiriman gagal wajib diisi.")

    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    assert_branch_access(user, scope_object=note)
    _require_active_loading(db, accurate_sj_id)
    # Owner: konfirmasi gagal kirim juga mutasi state — butuh Accepted.
    _require_accepted_po_for_note(db, note)

    # Fail-closed sebelum mutasi: tidak seed Master, tidak createIndex.
    require_delivery_txn_indexes(db)
    assigned_role = master.require_task_type_assigned_role(db, DELIVERY_FAILURE_TASK)

    courier = actor_email(user) or str(user.get("email_normalized") or "")
    fail_reason = reason.strip()

    def _operation(session: Any) -> dict[str, Any]:
        parent_ids = deliveries.note_parent_po_ids(note)
        event_po_id = (
            ""
            if deliveries.note_is_multi_po(note)
            else str(note.get("accurate_po_id") or (parent_ids[0] if parent_ids else ""))
        )
        event = deliveries.insert_delivery_event(
            db,
            accurate_sj_id=str(accurate_sj_id),
            accurate_po_id=event_po_id,
            accurate_po_ids=parent_ids,
            attempt=deliveries.next_delivery_attempt(
                db, accurate_sj_id, session=session
            ),
            status=deliveries.DELIVERY_FAILED,
            courier_email=courier,
            recipient_name=None,
            photo_evidence_ids=[],
            signature_evidence_id=None,
            failure_reason=fail_reason,
            session=session,
        )
        task = tasks.upsert_task(
            db,
            task_type=DELIVERY_FAILURE_TASK,
            scope="delivery_note",
            assigned_role=assigned_role,
            ref=_sj_ref(note),
            label="Pengiriman Gagal — Koordinasi Administrasi",
            coordination=True,
            context={
                "sj_no": note["sj_no"],
                "failure_reason": fail_reason,
                "attempt": event["attempt"],
                "courier_email": courier,
            },
            session=session,
        )
        append_audit_event(
            db,
            action="delivery.confirm.failure",
            actor_email=courier,
            target_email=None,
            detail={
                "accurate_sj_id": accurate_sj_id,
                "attempt": event["attempt"],
                "reason": fail_reason,
                "coordination_task_key": task["task_key"],
            },
            session=session,
        )
        return {"event": event, "coordination_task_key": task["task_key"]}

    return tasks.run_in_transaction(db, _operation)


def soft_delete_delivery_evidence(
    db: Database,
    user: dict[str, Any],
    *,
    evidence_id: str,
    reason: str,
) -> dict[str, Any]:
    """Hapus bukti = soft delete; file dan riwayat tetap untuk audit berizin."""
    require_action(db, user, task_type=DRIVER_TASK, action="manage_evidence")
    meta = evidence.get_evidence(db, evidence_id)
    if meta is None:
        raise LookupError(f"Bukti tidak ditemukan: {evidence_id}")
    sj_id = str(
        (meta.get("context") or {}).get("accurate_sj_id")
        or meta.get("scope_id")
        or ""
    )
    if sj_id:
        note = deliveries.get_delivery_note(db, sj_id) or {"accurate_sj_id": sj_id}
        assert_branch_access(user, scope_object=note)
    doc = evidence.soft_delete_evidence(
        db,
        evidence_id=evidence_id,
        actor_email=str(user.get("email_normalized") or ""),
        reason=reason,
    )
    append_audit_event(
        db,
        action="delivery.evidence.soft_delete",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={"evidence_id": evidence_id, "reason": reason.strip()},
    )
    return doc


def active_delivery_evidence(db: Database, accurate_sj_id: str) -> dict[str, Any]:
    return {
        "photos": evidence.list_evidence(
            db, scope=DELIVERY_PHOTO_SCOPE, scope_id=str(accurate_sj_id)
        ),
        "signatures": evidence.list_evidence(
            db, scope=DELIVERY_SIGNATURE_SCOPE, scope_id=str(accurate_sj_id)
        ),
    }


def delivery_schedule(
    db: Database,
    user: dict[str, Any] | None = None,
    *,
    ship_date_from: str | None = None,
    ship_date_to: str | None = None,
) -> dict[str, Any]:
    """Jadwal Pengiriman: satu baris per SJ + ringkasan periode.

    Akun terbatas hanya melihat SJ cabangnya; ``operations.full`` /
    ``access.all_branches`` / Super Admin melihat semua.
    """
    notes = deliveries.list_delivery_notes(
        db, ship_date_from=ship_date_from, ship_date_to=ship_date_to
    )
    if user is not None:
        notes = filter_delivery_notes_for_actor(user, notes)
    rows = []
    qty_by_unit: dict[str, float] = {}
    destinations: set[str] = set()
    for note in notes:
        loading = deliveries.get_loading_event(db, note["accurate_sj_id"])
        events = deliveries.list_delivery_events(db, note["accurate_sj_id"])
        last_event = events[-1] if events else None
        row_qty: dict[str, float] = {}
        for item in note["items"]:
            unit = str(item.get("unit") or "-")
            qty_by_unit[unit] = qty_by_unit.get(unit, 0) + float(item["qty"])
            row_qty[unit] = row_qty.get(unit, 0) + float(item["qty"])
        if note.get("destination"):
            destinations.add(str(note["destination"]))
        revision_status = note.get("revision_status") or deliveries.REVISION_ACTIVE
        rows.append(
            {
                **note,
                "item_count": len(note.get("items") or []),
                "qty_by_unit": row_qty,
                "loaded": loading is not None,
                "loaded_at": (loading or {}).get("created_at"),
                "loading_id": (loading or {}).get("loading_id"),
                "revision_status": revision_status,
                "needs_reloading": revision_status
                == deliveries.REVISION_NEEDS_RELOADING,
                "delivery_status": last_event["status"]
                if last_event
                else deliveries.DELIVERY_PENDING,
                "attempts": len(events),
                "driver_email": (last_event or {}).get("courier_email") or "",
                "recipient_name": (last_event or {}).get("recipient_name") or "",
            }
        )
    apply_schedule_print_status(db, rows)
    return {
        "items": rows,
        "summary": {
            "sj_count": len(rows),
            # Satuan berbeda tidak dijumlahkan tanpa aturan konversi Owner.
            "qty_by_unit": qty_by_unit,
            "destination_count": len(destinations),
        },
        "source": "mongodb",
    }
