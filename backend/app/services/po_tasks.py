"""Derivasi tugas operasional dari PO Accurate + enforcement dua gate.

Aturan terkunci:
- PO dengan minimal satu item Cetak → satu tugas Designer per PO.
- PO yang seluruh itemnya Non-Cetak → tanpa tugas Designer, Gate Design terpenuhi.
- Penjadwalan hanya muncul jika Gate Design DAN Gate Payment terbuka.
"""

from __future__ import annotations

from typing import Any

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


def _assigned_role(db: Database, task_type: str, fallback: str) -> str:
    master.seed_master_defaults(db)
    return master.assigned_role_for(db, task_type) or fallback


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


def derive_tasks_for_po(db: Database, po_doc: dict[str, Any]) -> dict[str, Any]:
    """Buat/refresh tugas untuk satu PO. Idempoten: tidak menggandakan tugas."""
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
            assigned_role=_assigned_role(db, "design", "designer"),
            ref=TaskRef(accurate_po_id=accurate_po_id, po_no=po_no),
            label="Design",
            context={"print_line_count": len(lines), "customer": header.get("customer")},
        )
        created.append(design["task_key"])
    else:
        skipped.append("design:semua-item-non-cetak")
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
            skipped.append(f"penjadwalan:{ref.accurate_line_id}:gate-belum-terpenuhi")
            continue
        task = tasks.upsert_task(
            db,
            task_type="penjadwalan",
            scope="po_line",
            assigned_role=_assigned_role(db, "penjadwalan", "production-head"),
            ref=ref,
            qty_target=float(line.get("qty") or 0),
            label="Penjadwalan",
            context={"item_name": line.get("item_name"), "unit": line.get("unit")},
        )
        created.append(task["task_key"])

    return {
        "accurate_po_id": accurate_po_id,
        "created_task_keys": created,
        "skipped": skipped,
        "scheduling_unlocked": scheduling_open,
        "gates": gates.get_gates(db, accurate_po_id),
    }
