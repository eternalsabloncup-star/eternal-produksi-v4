"""T5 — checker fulfillment PO (Selesai) yang authoritative.

Default OFF via ``V4_STRICT_PO_COMPLETION_ENABLED`` agar deploy tidak
menggeser bucket monitoring legacy tanpa persetujuan Owner.

FIND-T2-01 (legacy vs strict — tidak mengubah aturan bisnis terpisah):
- Flag OFF: membership/Selesai boleh mengandalkan node historis
  (``all(_status_passed)``); checker ini tidak memaksa bucket Selesai.
- Flag ON (cutover): Selesai hanya jika seluruh qty kewajiban PO-line
  terkirim + bukti pengiriman aktif valid; packing parsial/delivery parsial
  tidak cukup. Konsekuensi cutover: PO yang “semua node hijau” tanpa
  evidence/qty penuh keluar dari Selesai hingga kewajiban terpenuhi.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.repositories import delivery_repository as deliveries
from app.repositories import evidence_repository as evidence
from app.repositories import task_repository as tasks
from app.services import production_service as production
from app.services.delivery_service import (
    DELIVERY_PHOTO_SCOPE,
    DELIVERY_SIGNATURE_SCOPE,
    MIN_DELIVERY_PHOTOS,
)
from app.services.po_acceptance import is_po_accepted


def _header_items(po_doc: dict[str, Any]) -> list[dict[str, Any]]:
    header = po_doc.get("header") if isinstance(po_doc.get("header"), dict) else {}
    return [i for i in (header.get("items") or []) if isinstance(i, dict)]


def _obligation_qty(item: dict[str, Any], db: Database, accurate_po_id: str) -> float:
    """Qty kewajiban fulfillment = target qty PO-line sah (Accurate terkini).

    Packing adalah availability gate Loading — bukan pengurang kewajiban Selesai.
    Partial packing/delivery tidak mengecilkan target. ``db``/``accurate_po_id``
    dipertahankan untuk signature pemanggil; tidak dipakai mengecilkan qty.
    """
    del db, accurate_po_id
    return float(item.get("qty") or 0)


def _delivered_qty_for_line(
    db: Database, accurate_po_id: str, accurate_line_id: str
) -> float:
    total = 0.0
    seen_sj: set[str] = set()
    for note in deliveries.list_delivery_notes(db, accurate_po_id=accurate_po_id):
        if note.get("superseded_by"):
            continue
        sj_id = str(note.get("accurate_sj_id") or "")
        if not sj_id or sj_id in seen_sj:
            continue
        events = deliveries.list_delivery_events(db, sj_id)
        if not any(e.get("status") == deliveries.DELIVERY_SUCCESS for e in events):
            continue
        seen_sj.add(sj_id)
        for item in note.get("items") or []:
            if str(item.get("accurate_line_id") or "") == accurate_line_id:
                total += float(item.get("qty") or 0)
    return total


def sj_has_active_delivery_evidence(db: Database, accurate_sj_id: str) -> bool:
    """≥2 foto aktif + 1 TTD aktif + nama penerima pada event sukses."""
    events = [
        e
        for e in deliveries.list_delivery_events(db, accurate_sj_id)
        if e.get("status") == deliveries.DELIVERY_SUCCESS
    ]
    if not events:
        return False
    # Ambil event sukses terbaru yang punya nama penerima.
    events_sorted = sorted(
        events, key=lambda e: e.get("created_at") or e.get("confirmed_at") or "", reverse=True
    )
    for ev in events_sorted:
        name = str(ev.get("recipient_name") or "").strip()
        if not name:
            continue
        photos = evidence.list_evidence(
            db, scope=DELIVERY_PHOTO_SCOPE, scope_id=accurate_sj_id
        )
        # Filter ke evidence IDs event bila ada.
        photo_ids = set(ev.get("photo_evidence_ids") or [])
        if photo_ids:
            photos = [p for p in photos if p.get("evidence_id") in photo_ids]
        sigs = evidence.list_evidence(
            db, scope=DELIVERY_SIGNATURE_SCOPE, scope_id=accurate_sj_id
        )
        sig_id = ev.get("signature_evidence_id")
        if sig_id:
            sigs = [s for s in sigs if s.get("evidence_id") == sig_id]
        if len(photos) >= MIN_DELIVERY_PHOTOS and len(sigs) >= 1:
            return True
    return False


def evaluate_po_fulfillment(db: Database, po_doc: dict[str, Any]) -> dict[str, Any]:
    """Hasil checker — tidak menulis data."""
    accurate_po_id = str(po_doc.get("accurate_id") or "")
    reasons: list[str] = []
    if not is_po_accepted(po_doc):
        return {
            "complete": False,
            "accurate_po_id": accurate_po_id,
            "reasons": ["PO belum accepted."],
            "lines": [],
        }

    items = _header_items(po_doc)
    if not items:
        return {
            "complete": False,
            "accurate_po_id": accurate_po_id,
            "reasons": ["PO tidak memiliki item line."],
            "lines": [],
        }

    line_results: list[dict[str, Any]] = []
    for item in items:
        line_id = str(item.get("accurate_line_id") or "").strip()
        if not line_id:
            reasons.append("Ada item tanpa accurate_line_id — fail-closed.")
            line_results.append(
                {
                    "accurate_line_id": None,
                    "ok": False,
                    "reason": "identitas line ambigu",
                }
            )
            continue
        obligation = _obligation_qty(item, db, accurate_po_id)
        shipped = _delivered_qty_for_line(db, accurate_po_id, line_id)
        ok = obligation > 0 and shipped + 1e-9 >= obligation
        if not ok:
            reasons.append(
                f"Line {line_id}: terkirim {shipped:g} < kewajiban {obligation:g}."
            )
        line_results.append(
            {
                "accurate_line_id": line_id,
                "process_kind": item.get("process_kind"),
                "qty_obligation": obligation,
                "qty_shipped": shipped,
                "ok": ok,
            }
        )

    notes = [
        n
        for n in deliveries.list_delivery_notes(db, accurate_po_id=accurate_po_id)
        if not n.get("superseded_by")
    ]
    if not notes:
        reasons.append("Belum ada Surat Jalan aktif.")
    for note in notes:
        sj_id = str(note.get("accurate_sj_id") or "")
        success = any(
            e.get("status") == deliveries.DELIVERY_SUCCESS
            for e in deliveries.list_delivery_events(db, sj_id)
        )
        if not success:
            # SJ tanpa success boleh jika tidak membawa qty kewajiban tersisa —
            # fail-closed: setiap SJ aktif harus sukses bila strict.
            reasons.append(f"SJ {note.get('sj_no') or sj_id} belum sukses terkirim.")
            continue
        if not sj_has_active_delivery_evidence(db, sj_id):
            reasons.append(
                f"SJ {note.get('sj_no') or sj_id}: bukti aktif belum lengkap "
                f"(≥{MIN_DELIVERY_PHOTOS} foto + TTD + nama)."
            )

    complete = not reasons and all(r.get("ok") for r in line_results)
    return {
        "complete": complete,
        "accurate_po_id": accurate_po_id,
        "reasons": reasons,
        "lines": line_results,
    }


def is_po_fulfillment_complete(db: Database, po_doc: dict[str, Any]) -> bool:
    if not get_settings().strict_po_completion_enabled:
        return False
    return bool(evaluate_po_fulfillment(db, po_doc).get("complete"))
