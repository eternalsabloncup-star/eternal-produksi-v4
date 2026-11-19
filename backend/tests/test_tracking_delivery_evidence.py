"""Join SJ → delivery_event → evidence: dedupe, exclusivity, fail-closed."""

from __future__ import annotations

from datetime import datetime, timezone

from pymongo.database import Database

from app.integrations.accurate.sample_payload import SAMPLE_SALES_ORDER_STRUCTURE
from app.services.po_sync import sync_sales_order_payload
from app.services.tracking_progress import (
    compute_progress,
    evaluate_milestones,
    qty_po_total,
)
from app.services.tracking_workflow_adapter import (
    eligible_delivered_sj_qty,
    load_workflow_snapshot,
)


def _sync_po(db: Database) -> dict:
    return sync_sales_order_payload(db, SAMPLE_SALES_ORDER_STRUCTURE)


def _ensure_colls(db: Database) -> None:
    for name in (
        "accurate_delivery_notes",
        "loading_events",
        "delivery_events",
        "evidence_files",
    ):
        if name not in db.list_collection_names():
            db.create_collection(name)


def _clear(db: Database) -> None:
    _ensure_colls(db)
    for name in (
        "accurate_delivery_notes",
        "loading_events",
        "delivery_events",
        "evidence_files",
    ):
        db[name].delete_many({})


def _insert_note(
    db: Database,
    *,
    sj: str,
    po: str,
    qty: float,
    superseded_by: str | None = None,
) -> None:
    db["accurate_delivery_notes"].insert_one(
        {
            "accurate_sj_id": sj,
            "accurate_po_id": po,
            "sj_no": f"SJ/{sj}",
            "items": [{"accurate_line_id": f"line-{sj}", "qty": qty}],
            "superseded_by": superseded_by,
        }
    )


def _insert_evidence(
    db: Database,
    *,
    evidence_id: str,
    scope: str,
    scope_id: str,
    deleted: bool = False,
) -> None:
    db["evidence_files"].insert_one(
        {
            "evidence_id": evidence_id,
            "scope": scope,
            "scope_id": scope_id,
            "deleted_at": datetime.now(timezone.utc) if deleted else None,
        }
    )


def _insert_success(
    db: Database,
    *,
    sj: str,
    po: str,
    attempt: int = 1,
    photo_ids: list[str] | None = None,
    sig_id: str | None = None,
    recipient_name: str | None = "Penerima Uji",
    write_evidence: bool = True,
    soft_delete_evidence: bool = False,
) -> tuple[list[str], str]:
    photos = photo_ids or [
        f"evid:delivery-photo:{sj}:v1",
        f"evid:delivery-photo:{sj}:v2",
    ]
    signature = sig_id or f"evid:delivery-signature:{sj}:v1"
    db["delivery_events"].insert_one(
        {
            "delivery_event_id": f"delivery:{sj}:attempt-{attempt}",
            "accurate_sj_id": sj,
            "accurate_po_id": po,
            "attempt": attempt,
            "status": "berhasil",
            "recipient_name": recipient_name,
            "photo_evidence_ids": photos,
            "signature_evidence_id": signature,
            "created_at": datetime.now(timezone.utc),
        }
    )
    if write_evidence:
        for pid in photos:
            _insert_evidence(
                db,
                evidence_id=pid,
                scope="delivery-photo",
                scope_id=sj,
                deleted=soft_delete_evidence,
            )
        _insert_evidence(
            db,
            evidence_id=signature,
            scope="delivery-signature",
            scope_id=sj,
            deleted=soft_delete_evidence,
        )
    return photos, signature


def _progress(db: Database, po_id: str = "95106"):
    from app.repositories.po_repository import get_by_accurate_id

    po = get_by_accurate_id(db, po_id)
    assert po is not None
    wf = load_workflow_snapshot(db, po_id)
    return po, wf, compute_progress(evaluate_milestones(po, wf), po, wf)


def test_one_sj_one_success_counts_once(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)
    _insert_note(db, sj="SJ-1", po="95106", qty=total)
    _insert_success(db, sj="SJ-1", po="95106")
    _, wf, progress = _progress(db)
    assert len(eligible_delivered_sj_qty(wf)) == 1
    assert progress["shipping"]["qty_delivered"] == total
    assert progress["order_complete"] is True


def test_duplicate_success_events_still_count_once(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    half = qty_po_total(po) / 2
    _insert_note(db, sj="SJ-DUP", po="95106", qty=half)
    _insert_success(db, sj="SJ-DUP", po="95106", attempt=1)
    _insert_success(
        db,
        sj="SJ-DUP",
        po="95106",
        attempt=2,
        photo_ids=["evid:delivery-photo:SJ-DUP:v3", "evid:delivery-photo:SJ-DUP:v4"],
        sig_id="evid:delivery-signature:SJ-DUP:v2",
    )
    _, wf, progress = _progress(db)
    assert len(eligible_delivered_sj_qty(wf)) == 1
    assert progress["shipping"]["qty_delivered"] == half
    assert progress["order_complete"] is False


def test_failed_then_valid_success(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)
    _insert_note(db, sj="SJ-RETRY", po="95106", qty=total)
    db["delivery_events"].insert_one(
        {
            "delivery_event_id": "delivery:SJ-RETRY:attempt-1",
            "accurate_sj_id": "SJ-RETRY",
            "accurate_po_id": "95106",
            "attempt": 1,
            "status": "gagal",
            "recipient_name": None,
            "photo_evidence_ids": [],
            "signature_evidence_id": None,
            "failure_reason": "tidak ada penerima",
            "created_at": datetime.now(timezone.utc),
        }
    )
    _insert_success(db, sj="SJ-RETRY", po="95106", attempt=2)
    _, _, progress = _progress(db)
    assert progress["shipping"]["qty_delivered"] == total
    assert progress["order_complete"] is True


def test_two_valid_partial_sj_complete_po(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)
    a, b = total / 2, total - total / 2
    _insert_note(db, sj="SJ-A", po="95106", qty=a)
    _insert_note(db, sj="SJ-B", po="95106", qty=b)
    _insert_success(db, sj="SJ-A", po="95106")
    _insert_success(db, sj="SJ-B", po="95106")
    _, wf, progress = _progress(db)
    assert len(eligible_delivered_sj_qty(wf)) == 2
    assert progress["shipping"]["qty_delivered"] == total
    assert progress["order_complete"] is True


def test_cross_sj_evidence_scope_id_mismatch_rejected(db: Database) -> None:
    """Evidence milik SJ-X tidak mengesahkan SJ-Y (scope_id wajib cocok)."""
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    half = qty_po_total(po) / 2
    shared_photos = ["evid:delivery-photo:SHARED:v1", "evid:delivery-photo:SHARED:v2"]
    shared_sig = "evid:delivery-signature:SHARED:v1"
    _insert_note(db, sj="SJ-X", po="95106", qty=half)
    _insert_note(db, sj="SJ-Y", po="95106", qty=half)
    for pid in shared_photos:
        _insert_evidence(db, evidence_id=pid, scope="delivery-photo", scope_id="SJ-X")
    _insert_evidence(
        db, evidence_id=shared_sig, scope="delivery-signature", scope_id="SJ-X"
    )
    for sj, name in (("SJ-X", "A"), ("SJ-Y", "B")):
        db["delivery_events"].insert_one(
            {
                "delivery_event_id": f"delivery:{sj}:attempt-1",
                "accurate_sj_id": sj,
                "accurate_po_id": "95106",
                "attempt": 1,
                "status": "berhasil",
                "recipient_name": name,
                "photo_evidence_ids": shared_photos,
                "signature_evidence_id": shared_sig,
            }
        )
    _, wf, progress = _progress(db)
    assert [r["accurate_sj_id"] for r in eligible_delivered_sj_qty(wf)] == ["SJ-X"]
    assert progress["shipping"]["qty_delivered"] == half


def test_exclusive_evidence_ids_reject_both_via_synthetic_workflow() -> None:
    """Dua SJ share evidence_id setelah lolos join → keduanya digugurkan."""
    import app.services.tracking_workflow_adapter as adapter

    shared = ("e1", "e2", "e3")
    wf = {
        "accurate_po_id": "95106",
        "delivery_notes": [
            {"accurate_sj_id": "SJ-1", "accurate_po_id": "95106", "items": [{"qty": 10}]},
            {"accurate_sj_id": "SJ-2", "accurate_po_id": "95106", "items": [{"qty": 10}]},
        ],
        "delivery_events_by_sj": {},
        "evidence_by_id": {},
    }

    def _fake_first(note, _workflow):
        return {"attempt": 1, "status": "berhasil"}, shared

    original = adapter.first_valid_success_event
    adapter.first_valid_success_event = _fake_first  # type: ignore[assignment]
    try:
        assert eligible_delivered_sj_qty(wf) == []
    finally:
        adapter.first_valid_success_event = original  # type: ignore[assignment]


def test_cross_po_event_ignored(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)
    _insert_note(db, sj="SJ-PO", po="95106", qty=total)
    # Event PO lain tidak masuk snapshot (filter accurate_po_id) → diabaikan.
    _insert_success(db, sj="SJ-PO", po="99999")
    _, _, progress = _progress(db)
    assert progress["shipping"]["qty_delivered"] == 0.0
    assert progress["order_complete"] is False


def test_missing_evidence_reference(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)
    _insert_note(db, sj="SJ-MISS", po="95106", qty=total)
    _insert_success(db, sj="SJ-MISS", po="95106", write_evidence=False)
    _, _, progress = _progress(db)
    assert progress["shipping"]["qty_delivered"] == 0.0


def test_missing_photos_signature_recipient(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)

    _insert_note(db, sj="SJ-BAD", po="95106", qty=total)
    db["delivery_events"].insert_one(
        {
            "delivery_event_id": "delivery:SJ-BAD:attempt-1",
            "accurate_sj_id": "SJ-BAD",
            "accurate_po_id": "95106",
            "attempt": 1,
            "status": "berhasil",
            "recipient_name": "Ada",
            "photo_evidence_ids": ["evid:delivery-photo:SJ-BAD:v1"],
            "signature_evidence_id": "evid:delivery-signature:SJ-BAD:v1",
        }
    )
    _insert_evidence(
        db,
        evidence_id="evid:delivery-photo:SJ-BAD:v1",
        scope="delivery-photo",
        scope_id="SJ-BAD",
    )
    _insert_evidence(
        db,
        evidence_id="evid:delivery-signature:SJ-BAD:v1",
        scope="delivery-signature",
        scope_id="SJ-BAD",
    )
    _, _, progress = _progress(db)
    assert progress["shipping"]["qty_delivered"] == 0.0

    _clear(db)
    _insert_note(db, sj="SJ-NOSIG", po="95106", qty=total)
    db["delivery_events"].insert_one(
        {
            "delivery_event_id": "delivery:SJ-NOSIG:attempt-1",
            "accurate_sj_id": "SJ-NOSIG",
            "accurate_po_id": "95106",
            "attempt": 1,
            "status": "berhasil",
            "recipient_name": "Ada",
            "photo_evidence_ids": [
                "evid:delivery-photo:SJ-NOSIG:v1",
                "evid:delivery-photo:SJ-NOSIG:v2",
            ],
            "signature_evidence_id": None,
        }
    )
    for pid in (
        "evid:delivery-photo:SJ-NOSIG:v1",
        "evid:delivery-photo:SJ-NOSIG:v2",
    ):
        _insert_evidence(
            db, evidence_id=pid, scope="delivery-photo", scope_id="SJ-NOSIG"
        )
    _, _, progress = _progress(db)
    assert progress["shipping"]["qty_delivered"] == 0.0

    _clear(db)
    _insert_note(db, sj="SJ-NONAME", po="95106", qty=total)
    _insert_success(db, sj="SJ-NONAME", po="95106", recipient_name="  ")
    _, _, progress = _progress(db)
    assert progress["shipping"]["qty_delivered"] == 0.0


def test_soft_deleted_evidence_not_counted(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)
    _insert_note(db, sj="SJ-DEL", po="95106", qty=total)
    _insert_success(db, sj="SJ-DEL", po="95106", soft_delete_evidence=True)
    _, _, progress = _progress(db)
    assert progress["shipping"]["qty_delivered"] == 0.0


def test_superseded_and_replacement(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)
    _insert_note(db, sj="SJ-OLD", po="95106", qty=total, superseded_by="SJ-NEW")
    _insert_success(db, sj="SJ-OLD", po="95106")
    _insert_note(db, sj="SJ-NEW", po="95106", qty=total)
    _insert_success(db, sj="SJ-NEW", po="95106")
    _, wf, progress = _progress(db)
    assert [r["accurate_sj_id"] for r in eligible_delivered_sj_qty(wf)] == ["SJ-NEW"]
    assert progress["shipping"]["qty_delivered"] == total
    assert progress["order_complete"] is True


def test_qty_cannot_exceed_unique_eligible_sj(db: Database) -> None:
    _sync_po(db)
    _clear(db)
    po, _, _ = _progress(db)
    total = qty_po_total(po)
    _insert_note(db, sj="SJ-ONLY", po="95106", qty=total)
    _insert_success(db, sj="SJ-ONLY", po="95106", attempt=1)
    _insert_success(
        db,
        sj="SJ-ONLY",
        po="95106",
        attempt=2,
        photo_ids=["evid:delivery-photo:SJ-ONLY:v3", "evid:delivery-photo:SJ-ONLY:v4"],
        sig_id="evid:delivery-signature:SJ-ONLY:v2",
    )
    _, wf, progress = _progress(db)
    delivered = progress["shipping"]["qty_delivered"]
    assert delivered == total
    assert delivered == sum(r["qty"] for r in eligible_delivered_sj_qty(wf))
    assert delivered <= total + 1e-9
