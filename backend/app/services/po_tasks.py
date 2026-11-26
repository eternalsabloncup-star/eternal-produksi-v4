"""Derivasi tugas operasional dari PO Accurate + enforcement dua gate.

Aturan terkunci:
- PO dengan minimal satu item Cetak → satu tugas Designer per PO.
- PO yang seluruh itemnya Non-Cetak → tanpa tugas Designer, Gate Design terpenuhi.
- Penjadwalan hanya muncul jika Gate Design DAN Gate Payment terbuka.
- Packing Non-Cetak: DINONAKTIFKAN Owner 2026-11-27 — Non-Cetak langsung Loading SJ
  (tidak membuat qc-packing baru; histori lama tetap).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any  # noqa: F401 — session Any

from pymongo.database import Database

from app.config import get_settings
from app.domain.operational import TaskRef
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks

PRINT_KIND = "Cetak"
NON_PRINT_KIND = "Non-Cetak"
QC_PACKING_TASK = "qc-packing"


def _header(po_doc: dict[str, Any]) -> dict[str, Any]:
    header = po_doc.get("header")
    return header if isinstance(header, dict) else {}


def print_lines(po_doc: dict[str, Any]) -> list[dict[str, Any]]:
    items = _header(po_doc).get("items") or []
    return [
        i
        for i in items
        if isinstance(i, dict) and str(i.get("process_kind") or "") == PRINT_KIND
    ]


def non_print_lines(po_doc: dict[str, Any]) -> list[dict[str, Any]]:
    items = _header(po_doc).get("items") or []
    return [
        i
        for i in items
        if isinstance(i, dict) and str(i.get("process_kind") or "") == NON_PRINT_KIND
    ]


def has_print_items(po_doc: dict[str, Any]) -> bool:
    return bool(print_lines(po_doc))


def parse_activation_datetime(raw: str | None) -> datetime | None:
    """Parse ISO-8601 activation boundary. Kosong/invalid → None (fitur OFF)."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def non_cetak_packing_activation_at() -> datetime | None:
    return parse_activation_datetime(get_settings().non_cetak_packing_activated_at)


def _coerce_accepted_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return parse_activation_datetime(str(value))


def is_non_cetak_packing_eligible(po_doc: dict[str, Any] | None) -> bool:
    """Owner 2026-11-27: Non-Cetak tidak lagi melewati Packing → selalu False.

    Fungsi + activation timestamp dipertahankan agar flag/env historis dan
    tes konfigurasi tidak rusak; derive_tasks tidak membuat qc-packing baru.
    """
    del po_doc
    return False


def _assigned_role(
    db: Database, task_type: str, fallback: str | None = None, *, session: Any | None = None
) -> str:
    """Request path: fail-closed Master — tanpa seed/createIndex/fallback role."""
    del session, fallback
    return master.require_task_type_assigned_role(db, task_type)


DESIGN_GATE_SYNC_NEEDED_REASON = "Perlu Sinkronisasi"
DESIGN_GATE_NON_PRINT_OPEN_REASON = (
    "Seluruh item Non-Cetak — tidak memerlukan desain."
)


def sync_design_gate_for_non_print_po(
    db: Database, po_doc: dict[str, Any]
) -> dict[str, Any] | None:
    """PO tanpa item Cetak: Gate Design terpenuhi tanpa tugas Designer."""
    if has_print_items(po_doc):
        return None
    accurate_po_id = str(po_doc.get("accurate_id") or "")
    current = gates.get_gates(db, accurate_po_id).get("design", {})
    if current.get("state") == gates.GATE_OPEN:
        return None
    return gates.set_gate(
        db,
        accurate_po_id=accurate_po_id,
        gate="design",
        state=gates.GATE_OPEN,
        reason=DESIGN_GATE_NON_PRINT_OPEN_REASON,
        actor_email=None,
        detail={"auto": True},
    )


def _mark_design_gate_sync_needed(
    db: Database,
    *,
    accurate_po_id: str,
    error: str,
    actor_email: str | None = None,
) -> None:
    """Gate tetap closed; reason tampilan = Perlu Sinkronisasi. Fail-soft bila set_gate gagal."""
    try:
        gates.set_gate(
            db,
            accurate_po_id=accurate_po_id,
            gate="design",
            state=gates.GATE_CLOSED,
            reason=DESIGN_GATE_SYNC_NEEDED_REASON,
            actor_email=actor_email,
            detail={
                "sync_failed": True,
                "error": (error or "")[:500],
                "auto": True,
            },
        )
    except Exception:
        # Jangan menggagalkan acceptance hanya karena penanda UI gagal.
        return


def attempt_sync_design_gate_for_non_print_po(
    db: Database,
    po_doc: dict[str, Any],
    *,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Sync Gate Design Non-Cetak di luar txn accept.

    Tidak membuka Payment Gate. Tidak membuat tugas.
    Kegagalan → Gate tetap closed + status ``needed`` (Perlu Sinkronisasi).
    """
    from app.services.audit_log import append_audit_event

    accurate_po_id = str(po_doc.get("accurate_id") or "")
    if has_print_items(po_doc):
        return {
            "applicable": False,
            "synced": True,
            "status": "n_a",
            "display": None,
            "error": None,
            "accurate_po_id": accurate_po_id,
        }
    if not accurate_po_id:
        return {
            "applicable": True,
            "synced": False,
            "status": "needed",
            "display": DESIGN_GATE_SYNC_NEEDED_REASON,
            "error": "accurate_po_id kosong",
            "accurate_po_id": "",
        }

    current = gates.get_gates(db, accurate_po_id).get("design", {})
    if current.get("state") == gates.GATE_OPEN:
        return {
            "applicable": True,
            "synced": True,
            "status": "ok",
            "display": None,
            "error": None,
            "accurate_po_id": accurate_po_id,
            "duplicate": True,
        }

    try:
        sync_design_gate_for_non_print_po(db, po_doc)
        after = gates.get_gates(db, accurate_po_id).get("design", {})
        if after.get("state") != gates.GATE_OPEN:
            raise RuntimeError("Gate Design masih tertutup setelah sync.")
        append_audit_event(
            db,
            action="design.gate.sync_ok",
            actor_email=actor_email,
            target_email=None,
            detail={
                "accurate_po_id": accurate_po_id,
                "reason": DESIGN_GATE_NON_PRINT_OPEN_REASON,
            },
        )
        return {
            "applicable": True,
            "synced": True,
            "status": "ok",
            "display": None,
            "error": None,
            "accurate_po_id": accurate_po_id,
            "duplicate": False,
        }
    except Exception as exc:
        err_text = str(exc) or exc.__class__.__name__
        _mark_design_gate_sync_needed(
            db,
            accurate_po_id=accurate_po_id,
            error=err_text,
            actor_email=actor_email,
        )
        append_audit_event(
            db,
            action="design.gate.sync_failed",
            actor_email=actor_email,
            target_email=None,
            detail={
                "accurate_po_id": accurate_po_id,
                "error": err_text[:500],
                "display": DESIGN_GATE_SYNC_NEEDED_REASON,
            },
        )
        return {
            "applicable": True,
            "synced": False,
            "status": "needed",
            "display": DESIGN_GATE_SYNC_NEEDED_REASON,
            "error": err_text[:500],
            "accurate_po_id": accurate_po_id,
        }


def derive_tasks_for_po(
    db: Database,
    po_doc: dict[str, Any],
    *,
    session: Any | None = None,
    require_accepted: bool = False,
) -> dict[str, Any]:
    """Buat/refresh tugas untuk satu PO. Idempoten: tidak menggandakan tugas."""
    from app.services.po_acceptance import is_po_accepted, require_po_accepted
    from app.services.po_repair_maintenance import WriterFence, po_repair_writer_section

    with po_repair_writer_section(db) as fence:
        return _derive_tasks_under_writer_lease(
            db,
            po_doc,
            session=session,
            require_accepted=require_accepted,
            writer_fence=fence,
        )


def _derive_tasks_under_writer_lease(
    db: Database,
    po_doc: dict[str, Any],
    *,
    session: Any | None = None,
    require_accepted: bool = False,
    writer_fence: WriterFence,
) -> dict[str, Any]:
    from app.services.po_acceptance import is_po_accepted, require_po_accepted
    from app.services.po_repair_maintenance import mutate_under_writer_fence

    if require_accepted:
        require_po_accepted(po_doc)

    def _body(sess: Any | None) -> dict[str, Any]:
        if sess is not None:
            writer_fence.assert_valid(db, session=sess)
        accurate_po_id = str(po_doc.get("accurate_id") or "")
        if not accurate_po_id:
            raise ValueError("PO wajib memiliki accurate_id.")
        header = _header(po_doc)
        po_no = str(po_doc.get("po_no") or header.get("po_no") or "")
        created: list[str] = []
        skipped: list[str] = []

        lines = print_lines(po_doc)
        if lines:
            design = tasks.upsert_task(
                db,
                task_type="design",
                scope="po",
                assigned_role=_assigned_role(
                    db, "design", "designer", session=sess
                ),
                ref=TaskRef(accurate_po_id=accurate_po_id, po_no=po_no),
                label="Design",
                context={
                    "print_line_count": len(lines),
                    "customer": header.get("customer"),
                },
                session=sess,
            )
            created.append(design["task_key"])
        else:
            # Gate Design Non-Cetak di-sync SETELAH txn (set_gate punya preflight indeks).
            skipped.append("design:semua-item-non-cetak")

        scheduling_open = gates.both_gates_open(
            db, accurate_po_id, session=sess
        )
        for line in lines:
            ref = TaskRef(
                accurate_po_id=accurate_po_id,
                po_no=po_no,
                accurate_line_id=str(line.get("accurate_line_id") or ""),
                item_code=str(line.get("item_code") or ""),
                process_kind=PRINT_KIND,
            )
            if not scheduling_open:
                skipped.append(
                    f"penjadwalan:{ref.accurate_line_id}:gate-belum-terpenuhi"
                )
                continue
            task = tasks.upsert_task(
                db,
                task_type="penjadwalan",
                scope="po_line",
                assigned_role=_assigned_role(
                    db, "penjadwalan", "production-head", session=sess
                ),
                ref=ref,
                qty_target=float(line.get("qty") or 0),
                label="Penjadwalan",
                context={
                    "item_name": line.get("item_name"),
                    "unit": line.get("unit"),
                },
                session=sess,
            )
            created.append(task["task_key"])

        # Owner: jangan buat Packing Non-Cetak baru (direct Loading).
        # Task histori (jika ada) tidak dihapus/diubah di sini.
        nc_eligible = is_non_cetak_packing_eligible(po_doc)
        if non_print_lines(po_doc):
            skipped.append("qc-packing:non-cetak:owner-direct-loading")

        return {
            "accurate_po_id": accurate_po_id,
            "created_task_keys": created,
            "skipped": skipped,
            "scheduling_unlocked": scheduling_open,
            "non_cetak_packing_eligible": nc_eligible,
            "gates": gates.get_gates(db, accurate_po_id, session=sess),
        }

    # Sudah di dalam txn pemanggil (accept): renew di session yang sama.
    # Accept path memanggil attempt_sync_design_gate_for_non_print_po di luar txn.
    if session is not None:
        return _body(session)
    # Standalone: commit boundary = renew + upsert dalam satu txn.
    result = mutate_under_writer_fence(db, writer_fence, _body)
    # set_gate tidak aman di dalam txn writer — sync Gate Design Non-Cetak di sini.
    if is_po_accepted(po_doc):
        sync_info = attempt_sync_design_gate_for_non_print_po(db, po_doc)
        accurate_po_id = str(result.get("accurate_po_id") or po_doc.get("accurate_id") or "")
        if accurate_po_id:
            result = {
                **result,
                "gates": gates.get_gates(db, accurate_po_id),
                "scheduling_unlocked": gates.both_gates_open(db, accurate_po_id),
                "design_gate_sync": sync_info,
            }
    return result
