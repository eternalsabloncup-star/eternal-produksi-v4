"""Auto-sync Penerimaan Penjualan — BLOCKED sampai kontrak Accurate terverifikasi.

Tidak ada PATH sales-receipt di AccurateReadClient. Job ini:
- Menghormati kill switch (OFF = zero request/write/lock/status).
- Saat ON tanpa kontrak: fail-closed, zero Accurate HTTP, status BLOCKED.
- Tidak membuka payment gate dengan aturan baru.
"""

from __future__ import annotations

import time
from typing import Any

from pymongo.database import Database

from app.config import get_settings
from app.services.accurate_auto_sync.locks import (
    AutoSyncLockError,
    acquire_job_lock,
    release_job_lock,
)
from app.services.accurate_auto_sync.status import (
    disabled_status_view,
    mark_running,
    record_job_result,
)

# Kontrak resmi belum ada di repo (tidak ada list/detail path tervalidasi).
RECEIPT_CONTRACT_STATUS = "unverified"
RECEIPT_CONTRACT_BLOCKER = (
    "Endpoint Accurate Penerimaan Penjualan (sales-receipt) belum terverifikasi "
    "di source V4. Jangan menebak path/field. Sediakan respons sanitasi Owner "
    "atau dokumentasi path resmi sebelum mengaktifkan parser."
)


def is_receipt_auto_sync_enabled() -> bool:
    return bool(get_settings().receipt_auto_sync_enabled)


def receipt_contract_ready() -> bool:
    return RECEIPT_CONTRACT_STATUS == "verified"


def run_receipt_auto_sync(db: Database) -> dict[str, Any]:
    if not is_receipt_auto_sync_enabled():
        # ZERO WRITE: tanpa lock, status mutation, Accurate HTTP.
        return {
            "ok": True,
            "job": "receipt",
            "enabled": False,
            "blocked": True,
            "accurate_requests": 0,
            "writes": 0,
            "gate_mutations": 0,
            "status": disabled_status_view(
                "receipt",
                blocked=True,
                detail={
                    "contract": RECEIPT_CONTRACT_STATUS,
                    "message": "Kill switch OFF — zero Accurate / zero write.",
                    "blocker": RECEIPT_CONTRACT_BLOCKER,
                },
            ),
        }

    mark_running(db, "receipt")
    started = time.monotonic()
    token: str | None = None
    try:
        token = acquire_job_lock(db, job="receipt")
        # Tetap fail-closed: tidak memanggil Accurate tanpa kontrak.
        if not receipt_contract_ready():
            status = record_job_result(
                db,
                "receipt",
                enabled=True,
                ok=False,
                blocked=True,
                error=RECEIPT_CONTRACT_BLOCKER,
                error_code="receipt_contract_unverified",
                duration_seconds=time.monotonic() - started,
                complete=False,
                detail={
                    "contract": RECEIPT_CONTRACT_STATUS,
                    "accurate_requests": 0,
                    "parser": None,
                    "payment_gate_auto_open": False,
                },
            )
            return {
                "ok": False,
                "job": "receipt",
                "enabled": True,
                "blocked": True,
                "accurate_requests": 0,
                "writes": 0,
                "gate_mutations": 0,
                "error": "receipt_contract_unverified",
                "status": status,
            }

        status = record_job_result(
            db,
            "receipt",
            enabled=True,
            ok=False,
            blocked=True,
            error="receipt_parser_not_implemented",
            error_code="receipt_parser_not_implemented",
            duration_seconds=time.monotonic() - started,
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
    except AutoSyncLockError as exc:
        status = record_job_result(
            db,
            "receipt",
            enabled=True,
            ok=False,
            blocked=True,
            error=str(exc),
            error_code=exc.code,
            duration_seconds=time.monotonic() - started,
            complete=False,
        )
        return {
            "ok": False,
            "job": "receipt",
            "enabled": True,
            "blocked": True,
            "accurate_requests": 0,
            "writes": 0,
            "error": exc.code,
            "status": status,
        }
    finally:
        if token:
            release_job_lock(db, job="receipt", owner_token=token)
