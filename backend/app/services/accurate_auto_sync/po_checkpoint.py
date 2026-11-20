"""Checkpoint discovery PO — kontrak Accurate terverifikasi (read-only live).

Verified (GET /accurate/api/sales-order/list.do):
- ``sp.sort=id|asc`` → urutan id naik, page contiguous, tanpa overlap.
- ``sp.sort=id|desc`` / ``lastUpdate|desc`` diterima API.
- ``filter.id.op=GREATER_*`` **diabaikan** Accurate (hasil tetap dari awal) —
  jangan dipakai sebagai cursor.
- Default list tanpa sort **tidak** monotonic by id.

Checkpoint disimpan di ``accurate_auto_sync_status.checkpoint`` (koleksi
berindeks job sudah ada) dan hanya dimutasi di bawah lease ``sync_locks``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from app.services.accurate_auto_sync.locks import require_owns_job_lock
from app.services.accurate_auto_sync.status import STATUS_COLLECTION

# Kontrak terbukti live — jangan diganti spekulatif.
SORT_BACKFILL = "id|asc"
SORT_INCREMENTAL = "lastUpdate|desc"

CHECKPOINT_VERSION = 1


def default_po_checkpoint() -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "sort_backfill": SORT_BACKFILL,
        "sort_incremental": SORT_INCREMENTAL,
        "next_page": 1,
        "backfill_complete": False,
        "last_completed_id": None,
        "last_completed_page": 0,
        "phase": "backfill",
        "updated_at": None,
    }


def load_po_checkpoint(db: Database) -> dict[str, Any]:
    doc = db[STATUS_COLLECTION].find_one({"job": "po"}, {"checkpoint": 1}) or {}
    raw = doc.get("checkpoint") if isinstance(doc.get("checkpoint"), dict) else {}
    base = default_po_checkpoint()
    base.update({k: v for k, v in raw.items() if k in base or k == "updated_at"})
    # Normalisasi tipe aman.
    base["next_page"] = max(1, int(base.get("next_page") or 1))
    base["last_completed_page"] = max(0, int(base.get("last_completed_page") or 0))
    base["backfill_complete"] = bool(base.get("backfill_complete"))
    if base["backfill_complete"]:
        base["phase"] = "incremental"
    else:
        base["phase"] = "backfill"
    base["sort_backfill"] = SORT_BACKFILL
    base["sort_incremental"] = SORT_INCREMENTAL
    return base


def save_po_checkpoint(
    db: Database,
    checkpoint: dict[str, Any],
    *,
    owner_token: str,
) -> dict[str, Any]:
    """Persist checkpoint hanya jika lease PO masih milik worker ini."""
    require_owns_job_lock(db, job="po", owner_token=owner_token)
    now = datetime.now(timezone.utc)
    payload = {**default_po_checkpoint(), **checkpoint, "updated_at": now}
    payload["sort_backfill"] = SORT_BACKFILL
    payload["sort_incremental"] = SORT_INCREMENTAL
    payload["version"] = CHECKPOINT_VERSION
    db[STATUS_COLLECTION].update_one(
        {"job": "po"},
        {
            "$set": {"checkpoint": payload, "updated_at": now},
            "$setOnInsert": {"job": "po", "created_at": now},
        },
        upsert=True,
    )
    return payload
