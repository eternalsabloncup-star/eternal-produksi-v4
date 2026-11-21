"""Durable SJ sync quarantine / deferred review (bukan ingest Surat Jalan).

Kontrak:
- MULTI_SO_NOT_SUPPORTED → review terbuka, tidak menulis ``accurate_delivery_notes``.
- po_not_registered → deferred retryable setelah PO terdaftar.
- Upsert idempotent per ``accurate_sj_id`` (``_id`` stabil).
- Tanpa createIndex di request path — identitas unik lewat ``_id``.
- Payload mentah / PII pelanggan tidak disimpan.
- Fair budget: discovery mendapat jatah minimum; replay bounded + eligibility.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.database import Database

from app.integrations.accurate.fields import as_text

COLLECTION = "accurate_sj_sync_reviews"

REASON_MULTI_SO = "MULTI_SO_NOT_SUPPORTED"
REASON_PO_NOT_REGISTERED = "po_not_registered"

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"

# Backoff detik (bounded). Legacy review tanpa field tetap eligible sekali.
_PO_NOT_REGISTERED_BACKOFFS = (300, 900, 1800, 3600, 7200, 14400)
_MULTI_SO_BACKOFF_SECONDS = 6 * 3600
_DISCOVERY_MIN_RATIO_DEFAULT = 0.70
_DISCOVERY_MIN_RATIO_LO = 0.50
_DISCOVERY_MIN_RATIO_HI = 0.90


def review_doc_id(accurate_sj_id: str) -> str:
    return f"sj:{as_text(accurate_sj_id)}"


def collect_line_sales_order_ids(payload: dict[str, Any]) -> list[str]:
    """Kumpulkan SO id dari baris DO — tanpa menebak / memilih PO tunggal."""
    found: set[str] = set()
    raw_items = payload.get("detailItem") or payload.get("items") or []
    if not isinstance(raw_items, list):
        return []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        so = raw.get("salesOrder")
        so_id_from_obj = as_text(so.get("id")) if isinstance(so, dict) else ""
        so_id_flat = as_text(raw.get("salesOrderId"))
        so_id = so_id_from_obj or so_id_flat
        if so_id:
            found.add(so_id)
    return sorted(found)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def discovery_min_ratio() -> float:
    raw = (os.environ.get("V4_SJ_DISCOVERY_MIN_RATIO") or "").strip()
    if not raw:
        return _DISCOVERY_MIN_RATIO_DEFAULT
    try:
        val = float(raw)
    except ValueError:
        return _DISCOVERY_MIN_RATIO_DEFAULT
    return max(_DISCOVERY_MIN_RATIO_LO, min(_DISCOVERY_MIN_RATIO_HI, val))


def allocate_sj_cycle_budgets(max_rows: int) -> dict[str, int]:
    """Bagi budget siklus: discovery dijamin minimum, replay bounded."""
    total = max(1, int(max_rows))
    if total == 1:
        return {"total": 1, "discovery": 1, "replay": 0}
    ratio = discovery_min_ratio()
    discovery = max(1, int(total * ratio))
    if discovery >= total:
        discovery = total - 1
    replay = total - discovery
    return {"total": total, "discovery": discovery, "replay": replay}


def _backoff_seconds(reason: str, attempt_count: int) -> int:
    attempts = max(0, int(attempt_count))
    if reason == REASON_MULTI_SO:
        # Multi-SO: jangan retry tiap 5 menit; interval panjang bounded.
        return int(_MULTI_SO_BACKOFF_SECONDS)
    idx = min(attempts, len(_PO_NOT_REGISTERED_BACKOFFS) - 1)
    return int(_PO_NOT_REGISTERED_BACKOFFS[idx])


def compute_next_retry_at(
    *, reason: str, attempt_count: int, now: datetime | None = None
) -> datetime:
    base = now or _now()
    return base + timedelta(seconds=_backoff_seconds(reason, attempt_count))


def upsert_sj_sync_review(
    db: Database,
    *,
    accurate_sj_id: str,
    reason: str,
    source_so_ids: list[str] | None = None,
    accurate_po_id: str | None = None,
    sj_no: str | None = None,
    retryable: bool = False,
    last_error_code: str | None = None,
    schedule_retry: bool = True,
) -> dict[str, Any]:
    """Idempotent upsert review. Tidak menulis delivery note."""
    sj_id = as_text(accurate_sj_id)
    if not sj_id:
        raise ValueError("accurate_sj_id wajib untuk SJ sync review")
    now = _now()
    doc_id = review_doc_id(sj_id)
    so_ids = [as_text(x) for x in (source_so_ids or []) if as_text(x)]
    po_id = as_text(accurate_po_id) or None
    number = as_text(sj_no) or None
    code = as_text(last_error_code) or reason

    existing = db[COLLECTION].find_one({"_id": doc_id})
    if existing is None:
        attempt = 0
        doc = {
            "_id": doc_id,
            "kind": "sj_sync_review",
            "accurate_sj_id": sj_id,
            "sj_no": number,
            "reason": reason,
            "source_so_ids": so_ids,
            "accurate_po_id": po_id,
            "retryable": bool(retryable),
            "status": STATUS_OPEN,
            "disposition": None,
            "first_seen_at": now,
            "updated_at": now,
            "last_error_code": code,
            "seen_count": 1,
            "attempt_count": attempt,
            "last_attempt_at": None,
            "next_retry_at": (
                compute_next_retry_at(reason=reason, attempt_count=attempt, now=now)
                if schedule_retry and retryable
                else now
            ),
        }
        # MULTI_SO: cooldown segera agar tidak direplay siklus berikutnya.
        # po_not_registered: next_retry_at=now → eligible; gate PO lokal menahan Accurate.
        if reason == REASON_PO_NOT_REGISTERED and retryable:
            doc["next_retry_at"] = now
        db[COLLECTION].insert_one(doc)
        return doc

    updates: dict[str, Any] = {
        "updated_at": now,
        "last_error_code": code,
        "reason": reason,
        "retryable": bool(retryable),
        "seen_count": int(existing.get("seen_count") or 0) + 1,
    }
    if so_ids:
        updates["source_so_ids"] = so_ids
    if po_id:
        updates["accurate_po_id"] = po_id
    if number:
        updates["sj_no"] = number
    if existing.get("status") != STATUS_RESOLVED or existing.get("reason") != reason:
        updates["status"] = STATUS_OPEN
        updates["disposition"] = None
    # Jangan hapus attempt_count / next_retry_at legacy-compatible; set default bila absen.
    if "attempt_count" not in existing:
        updates["attempt_count"] = int(existing.get("attempt_count") or 0)
    if schedule_retry and retryable and reason == REASON_MULTI_SO:
        # Re-upsert multi-SO dari discovery: perpanjang cooldown, jangan flood.
        attempts = int(existing.get("attempt_count") or 0)
        updates["next_retry_at"] = compute_next_retry_at(
            reason=reason, attempt_count=attempts, now=now
        )

    db[COLLECTION].update_one({"_id": doc_id}, {"$set": updates})
    merged = dict(existing)
    merged.update(updates)
    return merged


def mark_sj_sync_review_resolved(
    db: Database,
    *,
    accurate_sj_id: str,
    disposition: str,
) -> None:
    sj_id = as_text(accurate_sj_id)
    if not sj_id:
        return
    now = _now()
    db[COLLECTION].update_one(
        {"_id": review_doc_id(sj_id)},
        {
            "$set": {
                "status": STATUS_RESOLVED,
                "disposition": disposition,
                "updated_at": now,
                "resolved_at": now,
                "retryable": False,
                "next_retry_at": None,
            }
        },
    )


def mark_sj_review_attempt(
    db: Database,
    *,
    accurate_sj_id: str,
    reason: str,
    defer_without_accurate: bool = False,
) -> dict[str, Any] | None:
    """Catat percobaan replay + jadwalkan next_retry_at (legacy-safe)."""
    sj_id = as_text(accurate_sj_id)
    if not sj_id:
        return None
    now = _now()
    doc = db[COLLECTION].find_one({"_id": review_doc_id(sj_id)})
    if not doc:
        return None
    attempts = int(doc.get("attempt_count") or 0) + 1
    next_at = compute_next_retry_at(reason=reason, attempt_count=attempts, now=now)
    if defer_without_accurate and reason == REASON_PO_NOT_REGISTERED:
        next_at = compute_next_retry_at(
            reason=REASON_PO_NOT_REGISTERED, attempt_count=attempts, now=now
        )
    updates = {
        "attempt_count": attempts,
        "last_attempt_at": now,
        "next_retry_at": next_at,
        "updated_at": now,
    }
    db[COLLECTION].update_one({"_id": review_doc_id(sj_id)}, {"$set": updates})
    merged = dict(doc)
    merged.update(updates)
    return merged


def review_is_eligible(doc: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Legacy tanpa next_retry_at → eligible. Multi-SO / deferred hormati cooldown."""
    if doc.get("status") != STATUS_OPEN:
        return False
    if not doc.get("retryable"):
        return False
    current = now or _now()
    nxt = _as_utc(doc.get("next_retry_at"))
    if nxt is None:
        return True
    return nxt <= current


def list_open_retryable_reviews(
    db: Database, *, limit: int = 50, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Ambil review eligible (status open + retryable + next_retry_at due).

    Filter next_retry_at di aplikasi agar dokumen legacy tanpa field tetap masuk.
    """
    current = now or _now()
    cur = (
        db[COLLECTION]
        .find({"status": STATUS_OPEN, "retryable": True})
        .sort([("next_retry_at", 1), ("updated_at", 1)])
        .limit(max(1, min(int(limit) * 5, 1000)))
    )
    out: list[dict[str, Any]] = []
    for doc in cur:
        if review_is_eligible(doc, now=current):
            out.append(doc)
        if len(out) >= max(1, min(int(limit), 200)):
            break
    return out


def get_sj_sync_review(db: Database, accurate_sj_id: str) -> dict[str, Any] | None:
    return db[COLLECTION].find_one({"_id": review_doc_id(as_text(accurate_sj_id))})
