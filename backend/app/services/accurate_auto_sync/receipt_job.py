"""Auto-sync Penerimaan Penjualan Accurate (kill switch OFF default).

Kontrak diverifikasi live: /accurate/api/sales-receipt/list.do|detail.do
dengan detailInvoice[].invoiceId + paymentAmount.

Gate payment memakai evaluasi existing — tidak ada threshold baru.
Receipt ambigu / void / revisi → REVIEW + re-evaluate gate (fail-closed).
Idempotent: siklus identik = zero write / zero audit / zero gate history.
"""

from __future__ import annotations

import time
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.integrations.accurate.client import AccurateReadClient
from app.integrations.accurate.normalize_invoice_receipt import (
    normalize_sales_receipt,
    receipt_material_fingerprint,
)
from app.repositories import invoice_repository as invoices
from app.repositories import payment_repository as payments
from app.repositories.po_repository import get_by_accurate_id
from app.services.accurate_auto_sync.checkpoint import CONTRACT_OTHER
from app.services.accurate_auto_sync.discovery import run_discovery_cycle
from app.services.accurate_auto_sync.locks import (
    AutoSyncLockError,
    acquire_job_lock,
    release_job_lock,
    require_owns_job_lock,
)
from app.services.accurate_auto_sync.po_checkpoint import SORT_BACKFILL, SORT_INCREMENTAL
from app.services.accurate_auto_sync.status import (
    disabled_status_view,
    mark_running,
    record_job_result,
)
from app.services import payment_service

# Path/shape verified live earlier; sort params pending Owner live confirm.
# Keep "verified" prefix so existing kill-switch/contract-ready checks stay green.
RECEIPT_CONTRACT_STATUS = "verified_live_read_only_path;sort_pending_live"
RECEIPT_CONTRACT_BLOCKER = ""
RECEIPT_SORT_CONTRACT = CONTRACT_OTHER

DEFAULT_PAGE_SIZE = 20
DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_ROWS = 100
DEFAULT_TIMEOUT_SECONDS = 50.0

_SYSTEM_USER = {
    "email_normalized": "system:accurate-auto-sync",
    "role": "super_admin",
}


def is_receipt_auto_sync_enabled() -> bool:
    return bool(get_settings().receipt_auto_sync_enabled)


def receipt_contract_ready() -> bool:
    return RECEIPT_CONTRACT_STATUS.startswith("verified")


def _allocation_amount(normalized: dict[str, Any]) -> float | None:
    """Ambil nominal alokasi tunggal — tanpa fallback total_payment."""
    allocs = normalized.get("allocations") or []
    if len(allocs) != 1:
        return None
    return allocs[0].get("payment_amount")


def _resolve_po_for_receipt(
    db: Database, normalized: dict[str, Any]
) -> dict[str, Any]:
    """Petakan receipt → invoice store → SO id. Fail-closed bila ambigu."""
    amount = _allocation_amount(normalized)

    if normalized.get("review_required"):
        return {
            "ok": False,
            "review": True,
            "reason": normalized.get("review_reason"),
            "accurate_po_id": "",
            "invoice_no": "",
            "amount": amount,
        }

    invoice_ids = list(normalized.get("invoice_ids") or [])
    if len(invoice_ids) != 1:
        return {
            "ok": False,
            "review": True,
            "reason": "Receipt tidak punya tepat satu invoiceId.",
            "accurate_po_id": "",
            "invoice_no": "",
            "amount": amount,
        }

    invoice_id = invoice_ids[0]
    inv = invoices.get_invoice(db, invoice_id)
    if inv is None:
        return {
            "ok": False,
            "review": True,
            "reason": (
                f"Invoice {invoice_id} belum ada di V4 — jalankan invoice sync lebih dulu "
                "atau tandai REVIEW."
            ),
            "accurate_po_id": "",
            "invoice_no": "",
            "amount": amount,
        }

    if inv.get("review_required") or not inv.get("primary_accurate_po_id"):
        return {
            "ok": False,
            "review": True,
            "reason": inv.get("review_reason")
            or "Invoice terkait tidak punya primary PO yang sah.",
            "accurate_po_id": "",
            "invoice_no": inv.get("invoice_no") or "",
            "amount": amount,
        }

    if amount is None:
        return {
            "ok": False,
            "review": True,
            "reason": "Nominal alokasi tidak valid — tidak memakai fallback totalPayment.",
            "accurate_po_id": str(inv.get("primary_accurate_po_id") or ""),
            "invoice_no": inv.get("invoice_no") or "",
            "amount": None,
        }

    po_id = str(inv["primary_accurate_po_id"])
    if get_by_accurate_id(db, po_id) is None:
        return {
            "ok": False,
            "review": True,
            "reason": f"PO {po_id} belum terdaftar di Inbox V4.",
            "accurate_po_id": po_id,
            "invoice_no": inv.get("invoice_no") or "",
            "amount": amount,
        }

    alloc = (normalized.get("allocations") or [None])[0] or {}
    return {
        "ok": True,
        "review": False,
        "accurate_po_id": po_id,
        "invoice_no": inv.get("invoice_no") or alloc.get("invoice_no") or "",
        "amount": amount,
        "po_no": str((get_by_accurate_id(db, po_id) or {}).get("po_no") or ""),
    }


def _candidate_doc(
    *,
    normalized: dict[str, Any],
    accurate_po_id: str,
    po_no: str,
    invoice_no: str | None,
    amount: float | None,
    review: bool,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "accurate_receipt_id": normalized["accurate_receipt_id"],
        "accurate_po_id": accurate_po_id,
        "po_no": po_no,
        "invoice_no": invoice_no,
        "amount": amount,
        "receipt_date": normalized.get("trans_date"),
        "raw_ref": {
            "source": "accurate_auto_sync",
            "review_required": review,
            "review_reason": reason,
            "receipt_no": normalized.get("receipt_no"),
            "invoice_ids": normalized.get("invoice_ids") or [],
            "void_cheq": normalized.get("void_cheq") is True,
            "allocations": normalized.get("allocations") or [],
            "gate_mutation": False,
        },
    }


def sync_one_receipt(
    db: Database,
    accurate_id: str,
    *,
    client: AccurateReadClient,
    owner_token: str,
) -> dict[str, Any]:
    require_owns_job_lock(db, job="receipt", owner_token=owner_token)
    payload = client.fetch_sales_receipt_detail(accurate_id)
    require_owns_job_lock(db, job="receipt", owner_token=owner_token)
    normalized = normalize_sales_receipt(payload)
    mapping = _resolve_po_for_receipt(db, normalized)

    payments.require_payment_indexes(db)
    before = payments.receipts_collection(db).find_one(
        {"accurate_receipt_id": normalized["accurate_receipt_id"]}, {"_id": 0}
    )
    previous_po = str((before or {}).get("accurate_po_id") or "")

    if not mapping["ok"]:
        candidate = _candidate_doc(
            normalized=normalized,
            accurate_po_id=str(mapping.get("accurate_po_id") or previous_po or "UNMAPPED"),
            po_no="",
            invoice_no=mapping.get("invoice_no") or None,
            amount=mapping.get("amount"),
            review=True,
            reason=str(mapping.get("reason") or "review"),
        )
        if before is not None and receipt_material_fingerprint(
            before
        ) == receipt_material_fingerprint(candidate):
            return {
                "outcome": "unchanged",
                "accurate_receipt_id": normalized["accurate_receipt_id"],
                "gate_mutations": 0,
                "writes": 0,
                "audited": False,
            }

        payments.upsert_receipt(
            db,
            accurate_receipt_id=candidate["accurate_receipt_id"],
            accurate_po_id=candidate["accurate_po_id"],
            po_no="",
            invoice_no=candidate.get("invoice_no"),
            amount=candidate.get("amount"),
            receipt_date=candidate.get("receipt_date"),
            raw_ref=candidate["raw_ref"],
        )
        # Re-evaluate gate pada PO lama/baru agar open tidak tertahan setelah void/revisi.
        gate_mutations = 0
        for po_id in {previous_po, candidate["accurate_po_id"]} - {"", "UNMAPPED"}:
            _gate, mutated = payment_service.evaluate_payment_gate(
                db, po_id, only_if_changed=True
            )
            if mutated:
                gate_mutations += 1
        return {
            "outcome": "review_required",
            "accurate_receipt_id": normalized["accurate_receipt_id"],
            "gate_mutations": gate_mutations,
            "writes": 1,
            "audited": False,
        }

    result = payment_service.ingest_receipts(
        db,
        _SYSTEM_USER,
        accurate_po_id=mapping["accurate_po_id"],
        po_no=mapping.get("po_no") or "",
        receipts=[
            {
                "id": normalized["accurate_receipt_id"],
                "invoiceNo": mapping.get("invoice_no"),
                "amount": mapping.get("amount"),
                "transDate": normalized.get("trans_date"),
            }
        ],
        skip_unchanged=True,
        raw_ref_extra={
            "source": "accurate_auto_sync",
            "review_required": False,
            "receipt_no": normalized.get("receipt_no"),
            "invoice_ids": normalized.get("invoice_ids") or [],
            "void_cheq": normalized.get("void_cheq") is True,
            "allocations": normalized.get("allocations") or [],
        },
    )
    written = int(result.get("written") or 0)
    gate_mutations = 1 if result.get("gate_mutated") else 0
    # Jika PO berubah, re-evaluate PO lama.
    if previous_po and previous_po != mapping["accurate_po_id"] and previous_po != "UNMAPPED":
        _g, mutated = payment_service.evaluate_payment_gate(
            db, previous_po, only_if_changed=True
        )
        if mutated:
            gate_mutations += 1

    if written == 0:
        outcome = "unchanged"
    elif before is None:
        outcome = "created"
    else:
        outcome = "updated"
    return {
        "outcome": outcome,
        "accurate_receipt_id": normalized["accurate_receipt_id"],
        "accurate_po_id": mapping["accurate_po_id"],
        "gate_mutations": gate_mutations,
        "writes": written,
        "audited": bool(result.get("audited")),
    }


def run_receipt_auto_sync(
    db: Database,
    *,
    client: AccurateReadClient | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not is_receipt_auto_sync_enabled():
        return {
            "ok": True,
            "job": "receipt",
            "enabled": False,
            "blocked": False,
            "accurate_requests": 0,
            "writes": 0,
            "gate_mutations": 0,
            "status": disabled_status_view(
                "receipt",
                detail={
                    "contract": RECEIPT_CONTRACT_STATUS,
                    "message": "Kill switch OFF — zero Accurate / zero write.",
                },
            ),
        }

    if not receipt_contract_ready():
        mark_running(db, "receipt")
        status = record_job_result(
            db,
            "receipt",
            enabled=True,
            ok=False,
            blocked=True,
            error="receipt_contract_unverified",
            error_code="receipt_contract_unverified",
            complete=False,
        )
        return {
            "ok": False,
            "job": "receipt",
            "enabled": True,
            "blocked": True,
            "accurate_requests": 0,
            "writes": 0,
            "gate_mutations": 0,
            "status": status,
        }

    mark_running(db, "receipt")
    started = time.monotonic()
    token: str | None = None
    counters = {
        "listed": 0,
        "fetched": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "review_required": 0,
        "failed": 0,
    }
    failures: list[dict[str, Any]] = []
    gate_mutations = 0
    writes = 0
    reader = client or AccurateReadClient.from_settings()
    accurate_requests = 0

    try:
        token = acquire_job_lock(db, job="receipt")
        page_size = max(1, min(int(page_size), 100))
        max_pages = max(1, min(int(max_pages), 50))
        max_rows = max(1, min(int(max_rows), 500))
        timeout_seconds = max(0.001, float(timeout_seconds))

        def _fetch_list(params: dict[str, Any]) -> list[dict[str, Any]]:
            return reader.fetch_sales_receipt_list(params=params)

        def _sync_one(accurate_id: str) -> dict[str, Any]:
            nonlocal gate_mutations, writes
            result = sync_one_receipt(
                db, accurate_id, client=reader, owner_token=token
            )
            gate_mutations += int(result.get("gate_mutations") or 0)
            writes += int(result.get("writes") or 0)
            return result

        disc = run_discovery_cycle(
            db,
            job="receipt",
            owner_token=token,
            fetch_list=_fetch_list,
            sync_one=_sync_one,
            counters=counters,
            failures=failures,
            page_size=page_size,
            max_pages=max_pages,
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
            started=started,
            contract_status=RECEIPT_SORT_CONTRACT,
        )
        accurate_requests = int(disc["accurate_requests"])
        checkpoint = disc["checkpoint"]
        stopped_reason = disc.get("stopped_reason")
        ok = bool(disc["ok"])
        partial = bool(disc["partial"])
        complete = bool(disc["complete"])

        status = record_job_result(
            db,
            "receipt",
            enabled=True,
            ok=ok,
            counters=counters,
            error=None if ok and complete else (stopped_reason or "incomplete"),
            error_code=stopped_reason,
            duration_seconds=time.monotonic() - started,
            partial=partial,
            complete=complete,
            blocked=False,
            detail={
                "contract": RECEIPT_CONTRACT_STATUS,
                "stopped_reason": stopped_reason,
                "failures": failures[:30],
                "accurate_requests": accurate_requests,
                "gate_mutations": gate_mutations,
                "writes": writes,
                "sort_warnings": disc.get("sort_warnings") or [],
                "checkpoint": {
                    "next_page": checkpoint.get("next_page"),
                    "backfill_complete": checkpoint.get("backfill_complete"),
                    "incremental_next_page": checkpoint.get("incremental_next_page"),
                    "sort_backfill": SORT_BACKFILL,
                    "sort_incremental": SORT_INCREMENTAL,
                },
                "discovery_complete": complete,
                "payment_threshold_changed": False,
            },
        )
        return {
            "ok": ok,
            "job": "receipt",
            "enabled": True,
            "blocked": False,
            "partial": partial,
            "complete": complete,
            "accurate_requests": accurate_requests,
            "writes": writes,
            "gate_mutations": gate_mutations,
            "counters": counters,
            "stopped_reason": stopped_reason,
            "failures": failures[:30],
            "checkpoint": checkpoint,
            "status": status,
        }
    except AutoSyncLockError as exc:
        status = record_job_result(
            db,
            "receipt",
            enabled=True,
            ok=False,
            error=str(exc),
            error_code=exc.code,
            duration_seconds=time.monotonic() - started,
            complete=False,
        )
        return {
            "ok": False,
            "job": "receipt",
            "enabled": True,
            "blocked": False,
            "accurate_requests": accurate_requests,
            "writes": 0,
            "gate_mutations": 0,
            "error": exc.code,
            "status": status,
        }
    finally:
        if token:
            release_job_lock(db, job="receipt", owner_token=token)
