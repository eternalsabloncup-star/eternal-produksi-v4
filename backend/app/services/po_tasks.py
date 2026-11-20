"""Derivasi tugas operasional dari PO Accurate + enforcement dua gate.

Aturan terkunci:
- PO dengan minimal satu item Cetak → satu tugas Designer per PO.
- PO yang seluruh itemnya Non-Cetak → tanpa tugas Designer, Gate Design terpenuhi.
- Penjadwalan hanya muncul jika Gate Design DAN Gate Payment terbuka.
"""

from __future__ import annotations

from typing import Any  # noqa: F401 — session Any

from pymongo.database import Database

from app.domain.operational import TaskRef
from app.repositories import gate_repository as gates
from app.repositories import master_repository as master
from app.repositories import task_repository as tasks

PRINT_KIND = "Cetak"


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


def has_print_items(po_doc: dict[str, Any]) -> bool:
    return bool(print_lines(po_doc))


def _assigned_role(
    db: Database, task_type: str, fallback: str | None = None, *, session: Any | None = None
) -> str:
    """Request path: fail-closed Master — tanpa seed/createIndex/fallback role."""
    del session, fallback
    return master.require_task_type_assigned_role(db, task_type)


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
        reason="Seluruh item Non-Cetak — tidak memerlukan desain.",
        actor_email=None,
        detail={"auto": True},
    )


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
            skipped.append("design:semua-item-non-cetak")
            if sess is None and is_po_accepted(po_doc):
                sync_design_gate_for_non_print_po(db, po_doc)

        scheduling_open = gates.both_gates_open(db, accurate_po_id)
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

        return {
            "accurate_po_id": accurate_po_id,
            "created_task_keys": created,
            "skipped": skipped,
            "scheduling_unlocked": scheduling_open,
            "gates": gates.get_gates(db, accurate_po_id),
        }

    # Sudah di dalam txn pemanggil (accept): renew di session yang sama.
    if session is not None:
        return _body(session)
    # Standalone: commit boundary = renew + upsert dalam satu txn.
    return mutate_under_writer_fence(db, writer_fence, _body)
