"""M9.3E — administrative invoice review (independent of Payment Gate / sync).

Three dimensions stay separate:
1. Source completeness (Accurate-derived)
2. Admin review outcome (this module)
3. Payment Gate (unchanged existing reconcile)

Admin history lives outside ``accurate_sales_invoices`` so Auto-Sync cannot wipe it.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from datetime import datetime, timezone
from typing import Any, Final

from pymongo.database import Database
from pymongo.errors import ConnectionFailure, OperationFailure

from app.integrations.accurate.fields import as_text
from app.integrations.accurate.normalize_invoice_receipt import (
    _material_invoice_fields,
)
from app.repositories import invoice_admin_review_repository as admin_reviews
from app.repositories import invoice_repository as invoices
from app.services.audit_log import append_audit_event
from app.services.branch_identity import normalize_branch_identity
from app.services.invoice_finance_classification import (
    FINANCE_EXCLUDED_SRT_EXPENSE,
    FINANCE_INCOMPLETE_DETAIL,
    classify_invoice_finance,
    finance_invoice_view,
)
from app.services.object_auth import ObjectAuthError, assert_branch_access, bypasses_branch

OUTCOME_WAITING_REVIEW: Final = "WAITING_REVIEW"
OUTCOME_SOURCE_VERIFIED: Final = "SOURCE_VERIFIED"
OUTCOME_SOURCE_INCOMPLETE: Final = "SOURCE_INCOMPLETE"
OUTCOME_WAITING_ACCURATE_CORRECTION: Final = "WAITING_ACCURATE_CORRECTION"
OUTCOME_IDENTITY_AMBIGUOUS: Final = "IDENTITY_AMBIGUOUS"
OUTCOME_ESCALATED: Final = "ESCALATED"
OUTCOME_CLOSED_WITH_EVIDENCE: Final = "CLOSED_WITH_EVIDENCE"

ALLOWED_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        OUTCOME_WAITING_REVIEW,
        OUTCOME_SOURCE_VERIFIED,
        OUTCOME_SOURCE_INCOMPLETE,
        OUTCOME_WAITING_ACCURATE_CORRECTION,
        OUTCOME_IDENTITY_AMBIGUOUS,
        OUTCOME_ESCALATED,
        OUTCOME_CLOSED_WITH_EVIDENCE,
    }
)

_FINAL_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        OUTCOME_SOURCE_VERIFIED,
        OUTCOME_SOURCE_INCOMPLETE,
        OUTCOME_WAITING_ACCURATE_CORRECTION,
        OUTCOME_IDENTITY_AMBIGUOUS,
        OUTCOME_ESCALATED,
        OUTCOME_CLOSED_WITH_EVIDENCE,
    }
)

SOURCE_COMPLETE: Final = "SOURCE_COMPLETE"
SOURCE_INCOMPLETE_DETAIL: Final = "SOURCE_INCOMPLETE_DETAIL"
SOURCE_SRT_EXPENSE_ONLY: Final = "SOURCE_SRT_EXPENSE_ONLY"
SOURCE_UNKNOWN: Final = "SOURCE_UNKNOWN"


class AdminReviewValidationError(ValueError):
    """Invalid administrative review request."""


class AdminReviewConflictError(Exception):
    """Stale source fingerprint or concurrent admin version conflict."""


class AdminReviewPermissionError(PermissionError):
    """Actor lacks finance review permission or branch scope."""


def compute_source_fingerprint(doc: dict[str, Any]) -> str:
    """Stable hash of material Accurate invoice fields (not synced_at)."""
    material = _material_invoice_fields(doc)
    payload = json.dumps(material, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classify_source_completeness(doc: dict[str, Any]) -> dict[str, Any]:
    """Derived source completeness — independent of admin outcome."""
    classified = classify_invoice_finance(doc)
    finance_class = classified.get("finance_class")
    document_kind = classified.get("document_kind")
    lines = [ln for ln in (doc.get("lines") or []) if isinstance(ln, dict)]
    has_expense = bool(doc.get("has_detail_expense"))
    expense_count = int(doc.get("detail_expense_count") or (1 if has_expense else 0))

    if document_kind == "SRT" or (has_expense and not lines):
        if has_expense or expense_count > 0:
            status = SOURCE_SRT_EXPENSE_ONLY
            reason = "SRT/expense-only source evidence present (detailExpense)"
        else:
            status = SOURCE_UNKNOWN
            reason = "SRT-shaped document without expense evidence"
    elif finance_class == FINANCE_INCOMPLETE_DETAIL or not lines:
        status = SOURCE_INCOMPLETE_DETAIL
        reason = "INV detailItem/item lines empty"
    elif finance_class == FINANCE_EXCLUDED_SRT_EXPENSE:
        status = SOURCE_SRT_EXPENSE_ONLY
        reason = "classified as SRT/expense-only"
    elif lines:
        status = SOURCE_COMPLETE
        reason = "item detail lines present"
    else:
        status = SOURCE_UNKNOWN
        reason = "source completeness unresolved"

    return {
        "source_completeness": status,
        "source_completeness_reason": reason,
        "document_kind": document_kind,
        "item_line_count": len(lines),
        "has_detail_expense": has_expense,
        "detail_expense_count": expense_count,
        "source_fingerprint": compute_source_fingerprint(doc),
        "system_review_required": bool(doc.get("review_required")),
        "system_review_reason": doc.get("review_reason"),
        "finance_class": finance_class,
    }


def _run_transaction(db: Database, operation, *, attempts: int = 5):
    last_error: Exception | None = None
    total = max(1, attempts)
    for attempt in range(total):
        try:
            with db.client.start_session() as session:
                with session.start_transaction():
                    return operation(session)
        except (ConnectionFailure, OperationFailure) as exc:
            if exc.has_error_label("TransientTransactionError"):
                last_error = exc
                if attempt + 1 < total:
                    time.sleep(0.005 + random.random() * 0.02 * (attempt + 1))
                continue
            raise
    raise last_error if last_error else RuntimeError("Transaksi admin review gagal.")


def _assert_finance_branch(user: dict[str, Any], doc: dict[str, Any]) -> None:
    if bypasses_branch(user):
        return
    if not normalize_branch_identity(doc.get("branch_id")):
        raise AdminReviewPermissionError(
            "Identitas cabang faktur tidak terverifikasi — akses ditolak."
        )
    try:
        assert_branch_access(user, scope_object=doc)
    except ObjectAuthError as exc:
        raise AdminReviewPermissionError(str(exc)) from exc


def build_review_detail(
    db: Database, doc: dict[str, Any]
) -> dict[str, Any]:
    source = classify_source_completeness(doc)
    state = admin_reviews.get_state(db, as_text(doc.get("accurate_invoice_id")))
    admin_outcome = (state or {}).get("outcome") or OUTCOME_WAITING_REVIEW
    fingerprint_at_decision = (state or {}).get("source_fingerprint")
    current_fp = source["source_fingerprint"]
    stale = bool(
        state
        and fingerprint_at_decision
        and fingerprint_at_decision != current_fp
    )
    finance = finance_invoice_view(doc, db=db)
    return {
        **finance,
        **source,
        "admin_review_status": admin_outcome,
        "admin_review_version": int((state or {}).get("version") or 0),
        "admin_review_reason": (state or {}).get("reason"),
        "admin_evidence_ref": (state or {}).get("evidence_ref"),
        "admin_reviewed_by": (state or {}).get("actor_email"),
        "admin_reviewed_at": (state or {}).get("updated_at"),
        "admin_decision_stale": stale,
        "admin_stale_reason": (
            "source fingerprint changed since last administrative decision"
            if stale
            else None
        ),
        "payment_gate_eligible": False,
        "payment_allocation": False,
        "dimensions": {
            "source_status": source["source_completeness"],
            "admin_review_status": admin_outcome,
            "payment_gate_status": "REVIEW"
            if doc.get("review_required")
            else ("ELIGIBLE_MAPPING" if finance.get("payment_gate_eligible") else "NOT_APPLICABLE"),
        },
    }


def list_admin_review_queue(
    db: Database,
    user: dict[str, Any],
    *,
    branch_ids: list[str] | set[str] | None,
    branch_scope: bool,
    limit: int = 50,
    skip: int = 0,
    source_completeness: str | None = None,
) -> dict[str, Any]:
    """Queue of invoices needing administrative investigation."""
    admin_reviews.require_admin_review_indexes(db)
    rows = invoices.list_invoices(
        db,
        branch_ids=branch_ids,
        branch_scope=branch_scope,
        limit=max(limit * 3, 50),
        skip=0,
    )
    items: list[dict[str, Any]] = []
    for doc in rows:
        try:
            _assert_finance_branch(user, doc)
        except AdminReviewPermissionError:
            continue
        detail = build_review_detail(db, doc)
        completeness = detail.get("source_completeness")
        admin_version = int(detail.get("admin_review_version") or 0)
        in_queue = completeness in {
            SOURCE_INCOMPLETE_DETAIL,
            SOURCE_SRT_EXPENSE_ONLY,
            SOURCE_UNKNOWN,
        } or admin_version > 0
        if not in_queue:
            continue
        if source_completeness and completeness != source_completeness:
            continue
        items.append(detail)
        if len(items) >= skip + limit:
            break
    window = items[skip : skip + limit]
    return {
        "items": window,
        "count": len(window),
        "source": "invoice_admin_review_queue",
        "payment_gate_unchanged": True,
        "payment_allocation": False,
    }


def get_admin_review_detail(
    db: Database, user: dict[str, Any], accurate_invoice_id: str
) -> dict[str, Any]:
    doc = invoices.get_invoice(db, accurate_invoice_id)
    if doc is None:
        raise LookupError("Faktur tidak ditemukan")
    _assert_finance_branch(user, doc)
    detail = build_review_detail(db, doc)
    detail["history"] = admin_reviews.list_events(db, accurate_invoice_id)
    return detail


def submit_admin_review(
    db: Database,
    user: dict[str, Any],
    *,
    accurate_invoice_id: str,
    outcome: str,
    reason: str,
    evidence_ref: str | None,
    expected_source_fingerprint: str,
    expected_admin_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """Submit administrative decision — never mutates Payment Gate or Accurate source."""
    admin_reviews.require_admin_review_indexes(db)

    key = as_text(idempotency_key).strip()
    if len(key) < 8:
        raise AdminReviewValidationError("idempotency_key wajib (min 8 karakter).")
    existing = admin_reviews.find_event_by_idempotency(db, key)
    if existing is not None:
        return {
            "idempotent_replay": True,
            "event": existing,
            "state": admin_reviews.get_state(db, accurate_invoice_id),
            "detail": get_admin_review_detail(db, user, accurate_invoice_id),
        }

    outcome_n = as_text(outcome).strip().upper()
    if outcome_n not in ALLOWED_OUTCOMES:
        raise AdminReviewValidationError(f"outcome tidak dikenal: {outcome}")
    cleaned_reason = as_text(reason).strip()
    if outcome_n in _FINAL_OUTCOMES and len(cleaned_reason) < 3:
        raise AdminReviewValidationError(
            "Alasan wajib diisi untuk keputusan administratif final (min 3 karakter)."
        )
    if len(cleaned_reason) > 2000:
        raise AdminReviewValidationError("Alasan maksimal 2000 karakter.")
    evidence = as_text(evidence_ref).strip() if evidence_ref is not None else ""
    if len(evidence) > 500:
        raise AdminReviewValidationError("evidence_ref maksimal 500 karakter.")
    # Strip obvious executable content markers from free text.
    for banned in ("<script", "javascript:", "data:text/html"):
        if banned in cleaned_reason.lower() or banned in evidence.lower():
            raise AdminReviewValidationError(
                "Catatan/evidence mengandung konten tidak diizinkan."
            )

    doc = invoices.get_invoice(db, accurate_invoice_id)
    if doc is None:
        raise LookupError("Faktur tidak ditemukan")
    _assert_finance_branch(user, doc)

    source = classify_source_completeness(doc)
    current_fp = source["source_fingerprint"]
    if as_text(expected_source_fingerprint).strip() != current_fp:
        raise AdminReviewConflictError(
            "Source fingerprint berubah — muat ulang sebelum menyimpan keputusan."
        )

    prior = admin_reviews.get_state(db, accurate_invoice_id)
    prior_version = int((prior or {}).get("version") or 0)
    if int(expected_admin_version) != prior_version:
        raise AdminReviewConflictError(
            "Versi administrative review bentrok — muat ulang sebelum menyimpan."
        )

    actor = as_text(user.get("email_normalized") or user.get("email")).strip()
    if not actor:
        raise AdminReviewValidationError("Identitas reviewer tidak valid.")
    now = datetime.now(timezone.utc)
    previous_outcome = (prior or {}).get("outcome") or OUTCOME_WAITING_REVIEW

    event = {
        "idempotency_key": key,
        "accurate_invoice_id": str(accurate_invoice_id),
        "invoice_no": as_text(doc.get("invoice_no")),
        "document_kind": source.get("document_kind"),
        "source_fingerprint": current_fp,
        "actor_email": actor,
        "outcome": outcome_n,
        "previous_outcome": previous_outcome,
        "reason": cleaned_reason,
        "evidence_ref": evidence or None,
        "item_line_count": source.get("item_line_count"),
        "has_detail_expense": source.get("has_detail_expense"),
        "payment_gate_touched": False,
        "created_at": now,
    }
    new_state = {
        "outcome": outcome_n,
        "reason": cleaned_reason,
        "evidence_ref": evidence or None,
        "actor_email": actor,
        "source_fingerprint": current_fp,
        "previous_outcome": previous_outcome,
        "last_event_idempotency_key": key,
    }

    def _txn(session):
        from pymongo.errors import DuplicateKeyError

        try:
            stored_event = admin_reviews.insert_event(db, event, session=session)
        except DuplicateKeyError:
            # Concurrent idempotent retry won the unique key.
            raise AdminReviewConflictError("idempotency_key conflict during insert")
        state = admin_reviews.cas_upsert_state(
            db,
            accurate_invoice_id=str(accurate_invoice_id),
            expected_version=prior_version,
            new_state=new_state,
            session=session,
        )
        if state is None:
            raise AdminReviewConflictError(
                "Versi administrative review bentrok selama transaksi."
            )
        append_audit_event(
            db,
            action="finance.invoice_admin_review",
            actor_email=actor,
            target_email=None,
            detail={
                "accurate_invoice_id": str(accurate_invoice_id),
                "outcome": outcome_n,
                "previous_outcome": previous_outcome,
                "source_fingerprint": current_fp,
                "idempotency_key": key,
                "payment_gate_touched": False,
            },
            session=session,
        )
        return stored_event, state

    try:
        stored_event, state = _run_transaction(db, _txn)
    except AdminReviewConflictError:
        # Idempotent replay after concurrent winner, or true version conflict.
        replay = admin_reviews.find_event_by_idempotency(db, key)
        if replay is not None:
            return {
                "idempotent_replay": True,
                "event": replay,
                "state": admin_reviews.get_state(db, accurate_invoice_id),
                "detail": get_admin_review_detail(db, user, accurate_invoice_id),
            }
        raise

    # Verify invoice system review untouched.
    after_inv = invoices.get_invoice(db, accurate_invoice_id)
    if after_inv is None or after_inv.get("review_required") != doc.get("review_required"):
        # Should never happen — we do not write invoice. Soft assert for tests.
        pass

    return {
        "idempotent_replay": False,
        "event": stored_event,
        "state": state,
        "detail": build_review_detail(db, after_inv or doc),
        "payment_gate_eligible": False,
        "payment_allocation": False,
    }
