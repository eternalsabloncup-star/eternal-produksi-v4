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
from app.services.task_engine import require_action

LOADING_TASK = "qty-loading-sj-berbeda"
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
    return TaskRef(
        accurate_po_id=str(note["accurate_po_id"]),
        po_no=str(note.get("po_no") or ""),
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


def has_successful_delivery(db: Database, accurate_sj_id: str) -> bool:
    return any(
        e["status"] == deliveries.DELIVERY_SUCCESS
        for e in deliveries.list_delivery_events(db, accurate_sj_id)
    )


def ingest_delivery_note(
    db: Database, user: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Sinkron SJ + penerapan aturan revisi sebagian item (Opsi B Owner).

    - Belum Loading → cukup simpan revisi; Loading biasa setelah valid.
    - Sudah Loading, belum terkirim → ``PERLU_LOADING_ULANG`` dan Loading lama
      harus dibatalkan secara teraudit sebelum Loading ulang.
    - Sudah terkirim → ``PERLU_KOREKSI_ADMINISTRASI``; Loading dan bukti
      historis tidak disentuh.
    """
    result = deliveries.upsert_delivery_note(db, payload)
    note = result["delivery_note"]
    sj_id = str(note["accurate_sj_id"])

    if not result["source_changed"]:
        return {**result, "revision_status": note.get("revision_status")}

    delivered = has_successful_delivery(db, sj_id)
    active_loading = deliveries.get_loading_event(db, sj_id)
    reason = "Revisi item Surat Jalan dari Accurate."

    if delivered:
        status = deliveries.REVISION_NEEDS_ADMIN_CORRECTION
    elif active_loading is not None:
        status = deliveries.REVISION_NEEDS_RELOADING
    else:
        status = deliveries.REVISION_ACTIVE

    updated = deliveries.set_revision_status(
        db, accurate_sj_id=sj_id, status=status, reason=reason
    )
    append_audit_event(
        db,
        action="delivery.note.revision",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={
            "accurate_sj_id": sj_id,
            "sj_version": result["sj_version"],
            "changed_items": result["changed_items"],
            "revision_status": status,
            "active_loading_id": (active_loading or {}).get("loading_id"),
        },
    )
    return {**result, "delivery_note": updated, "revision_status": status}


def validate_loading(db: Database, accurate_sj_id: str) -> dict[str, Any]:
    """Cek setiap item SJ terhadap Qty QC (Cetak) atau SJ itu sendiri (Non-Cetak)."""
    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    if note.get("superseded_by"):
        raise LoadingValidationError(
            f"Surat Jalan {note['sj_no']} sudah digantikan oleh "
            f"{note['superseded_by']} — tidak berlaku untuk Loading."
        )

    po_doc = get_by_accurate_id(db, str(note["accurate_po_id"]))
    if po_doc is None:
        raise LookupError(f"PO Accurate tidak ditemukan: {note['accurate_po_id']}")
    lines = _po_line_index(po_doc)

    checks: list[dict[str, Any]] = []
    for item in note["items"]:
        line_id = str(item["accurate_line_id"])
        po_line = lines.get(line_id)
        if po_line is None:
            checks.append(
                {
                    **item,
                    "process_kind": None,
                    "qty_available": 0.0,
                    "matched": False,
                    "reason": (
                        f"Item line {line_id} tidak ditemukan pada PO — identitas ambigu."
                    ),
                }
            )
            continue

        process_kind = str(po_line.get("process_kind") or "")
        if process_kind == "Cetak":
            qc_task = (
                tasks.get_task(
                    db,
                    production.qc_task_key(str(note["accurate_po_id"]), line_id),
                )
                or {}
            )
            packed = float(qc_task.get("qty_confirmed") or 0)
            used = float(qc_task.get("qty_consumed") or 0)
            available = max(packed - used, 0)
            matched = available >= float(item["qty"]) > 0
            reason = (
                None
                if matched
                else (
                    f"Qty QC & Packing tersedia {available:g}, "
                    f"Qty SJ {float(item['qty']):g}."
                )
            )
        else:
            # Non-Cetak mengikuti SJ Accurate tanpa ledger produksi.
            available = float(item["qty"])
            matched = float(item["qty"]) > 0
            reason = None if matched else "Qty SJ tidak valid."

        checks.append(
            {
                **item,
                "process_kind": process_kind,
                "qty_available": available,
                "matched": matched,
                "reason": reason,
            }
        )

    active_loading = deliveries.get_loading_event(db, accurate_sj_id)
    revision_status = note.get("revision_status") or deliveries.REVISION_ACTIVE
    return {
        "accurate_sj_id": accurate_sj_id,
        "sj_no": note["sj_no"],
        "sj_version": note.get("sj_version", 1),
        "revision_status": revision_status,
        "accurate_po_id": note["accurate_po_id"],
        "items": checks,
        "all_matched": all(c["matched"] for c in checks),
        "already_loaded": active_loading is not None,
        "active_loading_id": (active_loading or {}).get("loading_id"),
        "needs_reloading": revision_status == deliveries.REVISION_NEEDS_RELOADING,
        "delivered": has_successful_delivery(db, accurate_sj_id),
    }


def _prepare_delivery_txn(db: Database) -> None:
    """Verifikasi indeks wajib + seed master di LUAR transaksi.

    Tidak createIndex di request path. DDL dilarang di dalam transaksi.
    """
    from app.services.index_preflight import require_delivery_txn_indexes

    require_delivery_txn_indexes(db)
    master.seed_master_defaults(db)


def confirm_loading(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Satu aksi Loading untuk seluruh item SJ — Qty QC + Loading + Driver + audit atomik."""
    require_action(db, user, task_type=DRIVER_TASK, action="confirm")
    _prepare_delivery_txn(db)

    existing = deliveries.get_loading_event(db, accurate_sj_id)
    if existing is not None:
        note_now = deliveries.get_delivery_note(db, accurate_sj_id) or {}
        if note_now.get("revision_status") == deliveries.REVISION_NEEDS_RELOADING:
            raise LoadingValidationError(
                "Surat Jalan direvisi: batalkan Loading lama terlebih dahulu "
                "sebelum Loading ulang."
            )
        return {
            "loading": existing,
            "driver_task_key": build_task_key(
                DRIVER_TASK, "delivery_note", _sj_ref(note_now)
            )
            if note_now
            else None,
            "duplicate": True,
        }

    validation = validate_loading(db, accurate_sj_id)
    if not validation["all_matched"]:
        raise LoadingValidationError(
            "Loading terkunci: Qty item tidak cocok dengan Surat Jalan."
        )

    note = deliveries.get_delivery_note(db, accurate_sj_id)
    actor = str(user.get("email_normalized") or "")
    sequence = deliveries.next_loading_sequence(db, accurate_sj_id)
    assigned_role = master.assigned_role_for(db, DRIVER_TASK) or "driver"

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

        for check in validation["items"]:
            if check["process_kind"] != "Cetak":
                continue
            reserved = tasks.reserve_quota(
                db,
                task_key=production.qc_task_key(
                    str(note["accurate_po_id"]), str(check["accurate_line_id"])
                ),
                qty=float(check["qty"]),
                session=session,
            )
            if not reserved:
                raise LoadingValidationError(
                    f"Qty QC & Packing untuk item line {check['accurate_line_id']} "
                    "tidak lagi mencukupi."
                )
        loading = deliveries.insert_loading_event(
            db,
            accurate_sj_id=str(note["accurate_sj_id"]),
            accurate_po_id=str(note["accurate_po_id"]),
            sj_no=str(note["sj_no"]),
            items=[
                {
                    "sj_line_id": c["sj_line_id"],
                    "accurate_line_id": c["accurate_line_id"],
                    "item_code": c["item_code"],
                    "qty": c["qty"],
                    "unit": c["unit"],
                    "process_kind": c["process_kind"],
                }
                for c in validation["items"]
            ],
            actor_email=actor,
            idempotency_key=idempotency_key,
            sj_version=int(note.get("sj_version") or 1),
            sequence=sequence,
            session=session,
        )
        deliveries.set_revision_status(
            db,
            accurate_sj_id=accurate_sj_id,
            status=deliveries.REVISION_ACTIVE,
            reason="Loading dikonfirmasi untuk versi SJ terbaru.",
            session=session,
        )
        driver_task = _ensure_driver_task(
            db, note, loading, assigned_role=assigned_role, session=session
        )
        append_audit_event(
            db,
            action="delivery.loading.confirm",
            actor_email=actor,
            target_email=None,
            detail={
                "accurate_sj_id": accurate_sj_id,
                "sj_no": note["sj_no"],
                "loading_id": loading["loading_id"],
                "sj_version": loading.get("sj_version"),
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
    if session is None:
        master.seed_master_defaults(db)
    role = assigned_role or master.assigned_role_for(db, DRIVER_TASK) or "driver"
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
        },
        session=session,
    )
    # Loading ulang mengaktifkan kembali tugas yang sama dengan Loading baru.
    tasks.tasks_collection(db).update_one(
        {"task_key": task["task_key"]},
        {"$set": {"status": tasks.TASK_STATUS_OPEN}},
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
    require_action(db, user, task_type=DRIVER_TASK, action="cancel")
    if not reason.strip():
        raise ValueError("Alasan pembatalan Loading wajib diisi.")
    _prepare_delivery_txn(db)

    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    if has_successful_delivery(db, accurate_sj_id):
        raise LoadingCancelError(
            "Pengiriman sudah berhasil dengan bukti penerimaan — Loading tidak "
            "dapat dibatalkan."
        )

    loading = deliveries.get_loading_event(db, accurate_sj_id)
    if loading is None:
        return {"cancelled": False, "duplicate": True, "loading": None}

    actor = str(user.get("email_normalized") or "")
    loading_id = str(loading["loading_id"])
    driver_key = build_task_key(DRIVER_TASK, "delivery_note", _sj_ref(note))
    cancel_reason = reason.strip()

    def _operation(session: Any) -> dict[str, Any]:
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
        # Reversal memakai Qty yang benar-benar dikonsumsi Loading tersebut.
        for item in cancelled.get("items") or []:
            if item.get("process_kind") != "Cetak":
                continue
            qty = float(item["qty"])
            tasks.tasks_collection(db).update_one(
                {
                    "task_key": production.qc_task_key(
                        str(cancelled["accurate_po_id"]), str(item["accurate_line_id"])
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
    validation = validate_loading(db, accurate_sj_id)
    if validation["all_matched"]:
        raise LoadingValidationError(
            "Qty Loading sudah cocok dengan Surat Jalan — tugas koordinasi tidak dibuat."
        )
    note = deliveries.get_delivery_note(db, accurate_sj_id)
    master.seed_master_defaults(db)
    mismatched = [c for c in validation["items"] if not c["matched"]]
    task = tasks.upsert_task(
        db,
        task_type=LOADING_TASK,
        scope="delivery_note",
        assigned_role=master.assigned_role_for(db, LOADING_TASK) or "production-head",
        ref=_sj_ref(note),
        label="Qty Loading dan SJ Berbeda",
        coordination=True,
        context={
            "message": DISCREPANCY_MESSAGE,
            "sj_no": note["sj_no"],
            "mismatched_items": [
                {
                    "accurate_line_id": c["accurate_line_id"],
                    "item_code": c["item_code"],
                    "qty_sj": c["qty"],
                    "qty_available": c["qty_available"],
                    "reason": c["reason"],
                }
                for c in mismatched
            ],
        },
    )
    append_audit_event(
        db,
        action="delivery.loading.discrepancy",
        actor_email=str(user.get("email_normalized") or ""),
        target_email=None,
        detail={"accurate_sj_id": accurate_sj_id, "mismatch_count": len(mismatched)},
    )
    return task


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


def confirm_delivery_success(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    recipient_name: str,
    photos: list[tuple[str, str, bytes]],
    signature: tuple[str, str, bytes],
) -> dict[str, Any]:
    """Minimal dua foto + satu tanda tangan + nama penerima; kurir dari sesi."""
    require_action(db, user, task_type=DRIVER_TASK, action="confirm")

    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
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
    photo_ids: list[str] = []
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
            context={"accurate_po_id": note["accurate_po_id"]},
        )
        photo_ids.append(stored["evidence_id"])

    sig_stored = evidence.store_evidence(
        db,
        scope=DELIVERY_SIGNATURE_SCOPE,
        scope_id=str(accurate_sj_id),
        filename=signature[0],
        content_type=signature[1],
        content=signature[2],
        uploaded_by=courier,
        kind="delivery-signature",
        context={"recipient_name": recipient_name.strip()},
    )

    event = deliveries.insert_delivery_event(
        db,
        accurate_sj_id=str(accurate_sj_id),
        accurate_po_id=str(note["accurate_po_id"]),
        attempt=deliveries.next_delivery_attempt(db, accurate_sj_id),
        status=deliveries.DELIVERY_SUCCESS,
        courier_email=courier,
        recipient_name=recipient_name.strip(),
        photo_evidence_ids=photo_ids,
        signature_evidence_id=sig_stored["evidence_id"],
        failure_reason=None,
    )

    driver_key = build_task_key(DRIVER_TASK, "delivery_note", _sj_ref(note))
    tasks.tasks_collection(db).update_one(
        {"task_key": driver_key},
        {
            "$set": {
                "status": tasks.TASK_STATUS_DONE,
                "context.last_delivery_status": deliveries.DELIVERY_SUCCESS,
            }
        },
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
            "recipient_name": recipient_name.strip(),
        },
    )
    return event


def confirm_delivery_failure(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_sj_id: str,
    reason: str,
) -> dict[str, Any]:
    """Pengiriman gagal → tugas koordinasi Kepala Produksi, tanpa reschedule."""
    require_action(db, user, task_type=DRIVER_TASK, action="confirm")
    if not reason.strip():
        raise DeliveryEvidenceError("Alasan pengiriman gagal wajib diisi.")

    note = deliveries.get_delivery_note(db, accurate_sj_id)
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    _require_active_loading(db, accurate_sj_id)

    courier = str(user.get("email_normalized") or "")
    event = deliveries.insert_delivery_event(
        db,
        accurate_sj_id=str(accurate_sj_id),
        accurate_po_id=str(note["accurate_po_id"]),
        attempt=deliveries.next_delivery_attempt(db, accurate_sj_id),
        status=deliveries.DELIVERY_FAILED,
        courier_email=courier,
        recipient_name=None,
        photo_evidence_ids=[],
        signature_evidence_id=None,
        failure_reason=reason.strip(),
    )

    master.seed_master_defaults(db)
    task = tasks.upsert_task(
        db,
        task_type=DELIVERY_FAILURE_TASK,
        scope="delivery_note",
        assigned_role=master.assigned_role_for(db, DELIVERY_FAILURE_TASK)
        or "production-head",
        ref=_sj_ref(note),
        label="Pengiriman Gagal — Koordinasi Administrasi",
        coordination=True,
        context={
            "sj_no": note["sj_no"],
            "failure_reason": reason.strip(),
            "attempt": event["attempt"],
            "courier_email": courier,
        },
    )
    append_audit_event(
        db,
        action="delivery.confirm.failure",
        actor_email=courier,
        target_email=None,
        detail={
            "accurate_sj_id": accurate_sj_id,
            "attempt": event["attempt"],
            "reason": reason.strip(),
        },
    )
    return {"event": event, "coordination_task_key": task["task_key"]}


def soft_delete_delivery_evidence(
    db: Database,
    user: dict[str, Any],
    *,
    evidence_id: str,
    reason: str,
) -> dict[str, Any]:
    """Hapus bukti = soft delete; file dan riwayat tetap untuk audit berizin."""
    require_action(db, user, task_type=DRIVER_TASK, action="manage_evidence")
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
    *,
    ship_date_from: str | None = None,
    ship_date_to: str | None = None,
) -> dict[str, Any]:
    """Jadwal Pengiriman: satu baris per SJ + ringkasan periode."""
    notes = deliveries.list_delivery_notes(
        db, ship_date_from=ship_date_from, ship_date_to=ship_date_to
    )
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
