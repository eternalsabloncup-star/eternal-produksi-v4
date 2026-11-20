"""Per-job discovery checkpoint (PO / SJ / invoice / receipt).

Schema v3 — menambah ``date_floor`` / ``date_floor_scope`` agar pagination
dataset historis (tanpa filter) tidak dipakai ulang setelah Owner menerapkan
ACCURATE_SYNC_START_DATE.

Compatible load: checkpoint lama tanpa date_floor → dianggap scope mismatch →
pagination di-reset ke default (status/audit dokumen tidak dihapus).

Checkpoint lives on ``accurate_auto_sync_status.checkpoint`` keyed by ``job``,
mutated only under the matching ``sync_locks`` lease.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pymongo.database import Database

from app.services.accurate_auto_sync.date_floor import (
    configured_date_floor,
    date_floor_scope_id,
)
from app.services.accurate_auto_sync.locks import require_owns_job_lock
from app.services.accurate_auto_sync.status import STATUS_COLLECTION

SORT_BACKFILL = "id|asc"
SORT_INCREMENTAL = "lastUpdate|desc"

CHECKPOINT_VERSION = 3

# Live GET 2026-11-20: SO/DO/SI/SR all accept id|asc (contiguous) + lastUpdate|desc
# (timestamps require fields=id,lastUpdate on list rows) + transDate BETWEEN floor.
CONTRACT_SO = "verified_live_read_only_sort_date_floor"
CONTRACT_DO = "verified_live_read_only_sort_date_floor"
CONTRACT_SI = "verified_live_read_only_sort_date_floor"
CONTRACT_SR = "verified_live_read_only_sort_date_floor"
CONTRACT_OTHER = CONTRACT_SI  # compat alias


def default_checkpoint(
    *,
    contract_status: str = CONTRACT_OTHER,
    date_floor: date | None = None,
) -> dict[str, Any]:
    floor = date_floor or configured_date_floor()
    return {
        "version": CHECKPOINT_VERSION,
        "sort_backfill": SORT_BACKFILL,
        "sort_incremental": SORT_INCREMENTAL,
        "date_floor": floor.isoformat(),
        "date_floor_scope": date_floor_scope_id(floor),
        "next_page": 1,
        "backfill_complete": False,
        "last_completed_id": None,
        "last_completed_page": 0,
        "incremental_next_page": 1,
        "incremental_cycles_completed": 0,
        # True after ≥1 full lastUpdate|desc pass (short/empty page reset).
        "incremental_crawl_complete": False,
        "phase": "backfill",
        "contract_status": contract_status,
        "updated_at": None,
    }


def load_job_checkpoint(
    db: Database,
    job: str,
    *,
    contract_status: str = CONTRACT_OTHER,
    date_floor: date | None = None,
) -> dict[str, Any]:
    floor = date_floor or configured_date_floor()
    scope = date_floor_scope_id(floor)
    doc = db[STATUS_COLLECTION].find_one({"job": job}, {"checkpoint": 1}) or {}
    raw = doc.get("checkpoint") if isinstance(doc.get("checkpoint"), dict) else {}
    raw_scope = str(raw.get("date_floor_scope") or "")
    raw_floor = str(raw.get("date_floor") or "")
    # Scope mismatch (termasuk checkpoint historis tanpa date_floor): jangan
    # memakai next_page lama — dataset pagination berubah.
    if raw_scope != scope or raw_floor != floor.isoformat():
        return default_checkpoint(contract_status=contract_status, date_floor=floor)

    base = default_checkpoint(contract_status=contract_status, date_floor=floor)
    allowed = set(base.keys()) | {"updated_at"}
    base.update({k: v for k, v in raw.items() if k in allowed})
    base["next_page"] = max(1, int(base.get("next_page") or 1))
    base["last_completed_page"] = max(0, int(base.get("last_completed_page") or 0))
    base["incremental_next_page"] = max(1, int(base.get("incremental_next_page") or 1))
    base["incremental_cycles_completed"] = max(
        0, int(base.get("incremental_cycles_completed") or 0)
    )
    base["backfill_complete"] = bool(base.get("backfill_complete"))
    base["incremental_crawl_complete"] = bool(
        base.get("incremental_crawl_complete")
    ) or base["incremental_cycles_completed"] > 0
    base["phase"] = "incremental" if base["backfill_complete"] else "backfill"
    base["sort_backfill"] = SORT_BACKFILL
    base["sort_incremental"] = SORT_INCREMENTAL
    base["version"] = CHECKPOINT_VERSION
    base["date_floor"] = floor.isoformat()
    base["date_floor_scope"] = scope
    if not base.get("contract_status"):
        base["contract_status"] = contract_status
    return base


def save_job_checkpoint(
    db: Database,
    job: str,
    checkpoint: dict[str, Any],
    *,
    owner_token: str,
    contract_status: str = CONTRACT_OTHER,
    date_floor: date | None = None,
) -> dict[str, Any]:
    require_owns_job_lock(db, job=job, owner_token=owner_token)
    floor = date_floor or configured_date_floor()
    now = datetime.now(timezone.utc)
    payload = {
        **default_checkpoint(contract_status=contract_status, date_floor=floor),
        **checkpoint,
        "updated_at": now,
    }
    payload["sort_backfill"] = SORT_BACKFILL
    payload["sort_incremental"] = SORT_INCREMENTAL
    payload["version"] = CHECKPOINT_VERSION
    payload["date_floor"] = floor.isoformat()
    payload["date_floor_scope"] = date_floor_scope_id(floor)
    if not payload.get("contract_status"):
        payload["contract_status"] = contract_status
    db[STATUS_COLLECTION].update_one(
        {"job": job},
        {
            "$set": {"checkpoint": payload, "updated_at": now},
            "$setOnInsert": {"job": job, "created_at": now},
        },
        upsert=True,
    )
    return payload
