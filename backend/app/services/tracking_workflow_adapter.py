"""Adapter baca-saja ke kontrak workflow V4 (koleksi PR#6) tanpa mengimpor modulnya.

Sumber kebenaran nama koleksi / status diambil dari HEAD PR #6
(`accurate_delivery_notes`, `loading_events`, `delivery_events`, `evidence_files`).
Jika koleksi belum ada → ``available=False`` agar milestone Pending jujur.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure, PyMongoError

# Nama koleksi mengikuti kontrak PR#6 — jangan diganti lokal.
COLL_GATES = "po_gates"
COLL_TASKS = "operational_tasks"
COLL_DESIGN_SUBMISSIONS = "design_submissions"
COLL_DESIGN_APPROVALS = "design_approvals"
COLL_SCHEDULES = "production_schedules"
COLL_DELIVERY_NOTES = "accurate_delivery_notes"
COLL_LOADING_EVENTS = "loading_events"
COLL_DELIVERY_EVENTS = "delivery_events"
COLL_EVIDENCE_FILES = "evidence_files"
# Accurate auto-sync menulis ke accurate_payment_receipts (bukan payment_receipts).
COLL_PAYMENTS = "accurate_payment_receipts"
COLL_INVOICES = "accurate_sales_invoices"
COLL_DESIGN_LINKS = "design_approval_links"

# Status kontrak PR#6 (delivery_repository / evidence_repository).
DELIVERY_SUCCESS = "berhasil"
DELIVERY_FAILED = "gagal"
DELIVERY_PENDING = "menunggu_pengiriman"
LOADING_ACTIVE = "aktif"
LOADING_CANCELLED = "dibatalkan"
EVIDENCE_SCOPE_PHOTO = "delivery-photo"
EVIDENCE_SCOPE_SIGNATURE = "delivery-signature"
# Kontrak PR #6 (1b9f140): staging → context.delivery_commit=pending;
# setelah txn sukses → attach_evidence_to_delivery set "committed".
# Staging gagal di-soft-delete (abandoned), bukan status string terpisah.
DELIVERY_COMMIT_PENDING = "pending"
DELIVERY_COMMIT_COMMITTED = "committed"
# confirm_delivery_success: minimal 2 foto + 1 tanda tangan + nama penerima.
MIN_DELIVERY_PHOTOS = 2


def _safe_find(
    db: Database,
    collection: str,
    query: dict[str, Any],
    *,
    projection: dict[str, Any] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    try:
        if collection not in db.list_collection_names():
            return []
        cur = db[collection].find(query, projection or {"_id": 0}).limit(limit)
        return list(cur)
    except (PyMongoError, OperationFailure):
        return []


def _safe_find_one(
    db: Database,
    collection: str,
    query: dict[str, Any],
    *,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    rows = _safe_find(db, collection, query, projection=projection, limit=1)
    return rows[0] if rows else None


def collection_available(db: Database, name: str) -> bool:
    try:
        return name in db.list_collection_names()
    except (PyMongoError, OperationFailure):
        return False


def load_workflow_snapshot(db: Database, accurate_po_id: str) -> dict[str, Any]:
    """Snapshot bukti event untuk mapping milestone — tanpa side effect."""
    po_id = str(accurate_po_id)
    gates_doc = _safe_find_one(db, COLL_GATES, {"accurate_po_id": po_id})
    tasks = _safe_find(db, COLL_TASKS, {"ref.accurate_po_id": po_id})
    design_subs = _safe_find(db, COLL_DESIGN_SUBMISSIONS, {"accurate_po_id": po_id})
    design_appr = _safe_find(db, COLL_DESIGN_APPROVALS, {"accurate_po_id": po_id})
    # Link approval = penanda versi boleh dilihat customer (bukan status approved).
    design_links = _safe_find(db, COLL_DESIGN_LINKS, {"accurate_po_id": po_id})
    schedules = _safe_find(db, COLL_SCHEDULES, {"accurate_po_id": po_id})
    delivery_notes = _safe_find(db, COLL_DELIVERY_NOTES, {"accurate_po_id": po_id})
    loading_events = _safe_find(db, COLL_LOADING_EVENTS, {"accurate_po_id": po_id})
    delivery_events = _safe_find(db, COLL_DELIVERY_EVENTS, {"accurate_po_id": po_id})
    # Hanya receipt yang sudah terpetakan ke PO ini (bukan UNMAPPED).
    payments = _safe_find(db, COLL_PAYMENTS, {"accurate_po_id": po_id})
    # Relasi faktur: accurate_po_ids (array) memuat PO ini.
    invoices = _safe_find(db, COLL_INVOICES, {"accurate_po_ids": po_id})

    by_type: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        tt = str(t.get("task_type") or "")
        by_type.setdefault(tt, []).append(t)

    events_by_sj: dict[str, list[dict[str, Any]]] = {}
    referenced_evidence_ids: set[str] = set()
    for ev in delivery_events:
        sj = str(ev.get("accurate_sj_id") or "")
        events_by_sj.setdefault(sj, []).append(ev)
        for pid in ev.get("photo_evidence_ids") or []:
            if pid:
                referenced_evidence_ids.add(str(pid))
        sig = ev.get("signature_evidence_id")
        if sig:
            referenced_evidence_ids.add(str(sig))

    loading_by_sj: dict[str, list[dict[str, Any]]] = {}
    for lv in loading_events:
        sj = str(lv.get("accurate_sj_id") or "")
        loading_by_sj.setdefault(sj, []).append(lv)

    # Hanya evidence yang direferensikan event PO ini — tanpa scan global + limit 1000.
    evidence: list[dict[str, Any]] = []
    if referenced_evidence_ids:
        evidence = _safe_find(
            db,
            COLL_EVIDENCE_FILES,
            {"evidence_id": {"$in": sorted(referenced_evidence_ids)}},
            limit=max(len(referenced_evidence_ids) * 2, 50),
        )

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for e in evidence:
        eid = str(e.get("evidence_id") or "")
        if eid:
            evidence_by_id[eid] = e

    active_evidence_ids = {
        eid
        for eid, e in evidence_by_id.items()
        if e.get("deleted_at") is None
    }

    return {
        "accurate_po_id": po_id,
        "availability": {
            "gates": collection_available(db, COLL_GATES),
            "tasks": collection_available(db, COLL_TASKS),
            "design": collection_available(db, COLL_DESIGN_SUBMISSIONS),
            "schedules": collection_available(db, COLL_SCHEDULES),
            "delivery_notes": collection_available(db, COLL_DELIVERY_NOTES),
            "loading_events": collection_available(db, COLL_LOADING_EVENTS),
            "delivery_events": collection_available(db, COLL_DELIVERY_EVENTS),
            "delivery_evidence": collection_available(db, COLL_EVIDENCE_FILES),
            "payments": collection_available(db, COLL_PAYMENTS),
            "invoices": collection_available(db, COLL_INVOICES),
            "design_links": collection_available(db, COLL_DESIGN_LINKS),
        },
        "gates": gates_doc,
        "tasks_by_type": by_type,
        "tasks": tasks,
        "design_submissions": design_subs,
        "design_approvals": design_appr,
        "design_approval_links": design_links,
        "schedules": schedules,
        "delivery_notes": delivery_notes,
        "loading_events": loading_events,
        "delivery_events": delivery_events,
        "delivery_events_by_sj": events_by_sj,
        "loading_events_by_sj": loading_by_sj,
        "delivery_evidence": [
            e for e in evidence if e.get("deleted_at") is None
        ],
        "evidence_by_id": evidence_by_id,
        "active_evidence_ids": active_evidence_ids,
        "payments": payments,
        "invoices": invoices,
    }


def note_qty(note: dict[str, Any]) -> float:
    items = note.get("items") or []
    if items:
        return float(sum(float(i.get("qty") or 0) for i in items))
    return float(note.get("qty") or note.get("qty_total") or 0)


def _note_belongs_to_po(note: dict[str, Any], po_id: str) -> bool:
    return str(note.get("accurate_po_id") or "") == str(po_id)


def _note_is_active(note: dict[str, Any]) -> bool:
    """SJ aktif menurut field kontrak yang diketahui — fail-closed pada soft-delete."""
    if note.get("superseded_by"):
        return False
    if note.get("soft_deleted") is True:
        return False
    if note.get("deleted_at") is not None:
        return False
    return True


def _evidence_matches(
    *,
    evidence_id: str,
    expected_scope: str,
    accurate_sj_id: str,
    delivery_event_id: str,
    attempt: int,
    evidence_by_id: dict[str, dict[str, Any]],
) -> bool:
    doc = evidence_by_id.get(evidence_id)
    if not doc:
        return False
    if doc.get("deleted_at") is not None:
        return False
    if str(doc.get("scope") or "") != expected_scope:
        return False
    if str(doc.get("scope_id") or "") != str(accurate_sj_id):
        return False
    ctx = doc.get("context") if isinstance(doc.get("context"), dict) else {}
    # Kontrak PR #6 attach_evidence_to_delivery — semua field wajib cocok.
    if str(ctx.get("delivery_commit") or "") != DELIVERY_COMMIT_COMMITTED:
        return False
    if str(ctx.get("accurate_sj_id") or "") != str(accurate_sj_id):
        return False
    if str(ctx.get("delivery_event_id") or "") != str(delivery_event_id):
        return False
    try:
        if int(ctx["delivery_attempt"]) != int(attempt):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def validate_success_event_for_note(
    note: dict[str, Any],
    event: dict[str, Any],
    workflow: dict[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """Validasi join SJ → delivery_event → evidence sesuai kontrak PR#6.

    Return (ok, evidence_ids_terpakai). Fail-closed.
    """
    po_id = str(workflow.get("accurate_po_id") or "")
    sj = str(note.get("accurate_sj_id") or "")
    if not po_id or not sj:
        return False, ()
    if not _note_belongs_to_po(note, po_id) or not _note_is_active(note):
        return False, ()
    if event.get("status") != DELIVERY_SUCCESS:
        return False, ()
    if str(event.get("accurate_sj_id") or "") != sj:
        return False, ()
    if str(event.get("accurate_po_id") or "") != po_id:
        return False, ()
    if not str(event.get("recipient_name") or "").strip():
        return False, ()
    delivery_event_id = str(event.get("delivery_event_id") or "")
    if not delivery_event_id:
        return False, ()
    try:
        attempt = int(event["attempt"])
    except (KeyError, TypeError, ValueError):
        return False, ()

    photo_ids = [str(x) for x in (event.get("photo_evidence_ids") or []) if x]
    sig_id = str(event.get("signature_evidence_id") or "")
    if len(photo_ids) < MIN_DELIVERY_PHOTOS or not sig_id:
        return False, ()
    if len(set(photo_ids)) < MIN_DELIVERY_PHOTOS:
        return False, ()

    evidence_by_id: dict[str, dict[str, Any]] = workflow.get("evidence_by_id") or {}
    for pid in photo_ids:
        if not _evidence_matches(
            evidence_id=pid,
            expected_scope=EVIDENCE_SCOPE_PHOTO,
            accurate_sj_id=sj,
            delivery_event_id=delivery_event_id,
            attempt=attempt,
            evidence_by_id=evidence_by_id,
        ):
            return False, ()
    if not _evidence_matches(
        evidence_id=sig_id,
        expected_scope=EVIDENCE_SCOPE_SIGNATURE,
        accurate_sj_id=sj,
        delivery_event_id=delivery_event_id,
        attempt=attempt,
        evidence_by_id=evidence_by_id,
    ):
        return False, ()

    return True, tuple(list(photo_ids) + [sig_id])


def first_valid_success_event(
    note: dict[str, Any],
    workflow: dict[str, Any],
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Ambil satu event berhasil sah untuk SJ (dedupe per SJ di level pemanggil)."""
    sj = str(note.get("accurate_sj_id") or "")
    events = (workflow.get("delivery_events_by_sj") or {}).get(sj) or []
    for ev in events:
        ok, evid = validate_success_event_for_note(note, ev, workflow)
        if ok:
            return ev, evid
    return None, ()


def note_has_active_delivery_success(
    note: dict[str, Any],
    workflow: dict[str, Any],
) -> bool:
    """True jika SJ punya ≥1 event berhasil dengan bukti lengkap & identity cocok."""
    ev, _ = first_valid_success_event(note, workflow)
    return ev is not None


def eligible_delivered_sj_qty(
    workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    """Daftar SJ eligible setelah dedupe + exclusive evidence.

    Satu ``accurate_sj_id`` → satu qty. Evidence ID yang dipakai >1 SJ
    membuat semua SJ terkait digugurkan (fail-closed cross-reuse).
    """
    po_id = str(workflow.get("accurate_po_id") or "")
    by_sj: dict[str, dict[str, Any]] = {}
    for note in workflow.get("delivery_notes") or []:
        if not _note_belongs_to_po(note, po_id) or not _note_is_active(note):
            continue
        sj = str(note.get("accurate_sj_id") or "")
        if not sj or sj in by_sj:
            continue
        ev, evid = first_valid_success_event(note, workflow)
        if ev is None:
            continue
        by_sj[sj] = {
            "accurate_sj_id": sj,
            "qty": note_qty(note),
            "evidence_ids": evid,
            "attempt": ev.get("attempt"),
        }

    owner: dict[str, str] = {}
    conflicted: set[str] = set()
    for sj, row in by_sj.items():
        for eid in row["evidence_ids"]:
            prev = owner.get(eid)
            if prev is None:
                owner[eid] = sj
            elif prev != sj:
                conflicted.add(prev)
                conflicted.add(sj)

    return [row for sj, row in by_sj.items() if sj not in conflicted]


def note_has_active_loading(note: dict[str, Any], workflow: dict[str, Any]) -> bool:
    sj = str(note.get("accurate_sj_id") or "")
    rows = (workflow.get("loading_events_by_sj") or {}).get(sj) or []
    return any(r.get("status") == LOADING_ACTIVE for r in rows)


def task_aggregate(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    if not tasks:
        return {
            "present": False,
            "status": None,
            "qty_target": 0.0,
            "qty_confirmed": 0.0,
            "updated_at": None,
        }
    statuses = [str(t.get("status") or "") for t in tasks]
    qty_target = sum(float(t.get("qty_target") or 0) for t in tasks)
    qty_confirmed = sum(float(t.get("qty_confirmed") or 0) for t in tasks)
    done = all(s == "selesai" for s in statuses)
    partial = any(s in ("sebagian", "selesai") for s in statuses)
    if done:
        status = "selesai"
    elif partial:
        status = "sebagian"
    else:
        status = "belum_dikerjakan"
    timestamps: list[datetime] = []
    for t in tasks:
        for key in ("updated_at", "completed_at", "created_at"):
            val = t.get(key)
            if isinstance(val, datetime):
                timestamps.append(val)
    updated = max(timestamps) if timestamps else None
    return {
        "present": True,
        "status": status,
        "qty_target": qty_target,
        "qty_confirmed": qty_confirmed,
        "updated_at": updated,
        "count": len(tasks),
    }


def gate_state(gates_doc: dict[str, Any] | None, gate: str) -> str | None:
    if not gates_doc:
        return None
    node = gates_doc.get(gate)
    if not isinstance(node, dict):
        return None
    return node.get("state")


def as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None
