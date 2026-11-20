"""Per-job discovery checkpoint (PO / SJ / invoice / receipt).

Schema v2 — compatible with PO v1 (missing incremental_* fields defaulted).

Checkpoint lives on ``accurate_auto_sync_status.checkpoint`` keyed by ``job``,
mutated only under the matching ``sync_locks`` lease.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from app.services.accurate_auto_sync.locks import require_owns_job_lock
from app.services.accurate_auto_sync.status import STATUS_COLLECTION

SORT_BACKFILL = "id|asc"
SORT_INCREMENTAL = "lastUpdate|desc"

CHECKPOINT_VERSION = 2

# SO sort proven live. DO/SI/SR list paths proven; sort params pending live confirm.
CONTRACT_SO = "verified_live_read_only_sort"
CONTRACT_OTHER = "list_path_verified_sort_pending_live"


def default_checkpoint(*, contract_status: str = CONTRACT_OTHER) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "sort_backfill": SORT_BACKFILL,
        "sort_incremental": SORT_INCREMENTAL,
        "next_page": 1,
        "backfill_complete": False,
        "last_completed_id": None,
        "last_completed_page": 0,
        "incremental_next_page": 1,
        "incremental_cycles_completed": 0,
        "phase": "backfill",
        "contract_status": contract_status,
        "updated_at": None,
    }


def load_job_checkpoint(
    db: Database,
    job: str,
    *,
    contract_status: str = CONTRACT_OTHER,
) -> dict[str, Any]:
    doc = db[STATUS_COLLECTION].find_one({"job": job}, {"checkpoint": 1}) or {}
    raw = doc.get("checkpoint") if isinstance(doc.get("checkpoint"), dict) else {}
    base = default_checkpoint(contract_status=contract_status)
    allowed = set(base.keys()) | {"updated_at"}
    base.update({k: v for k, v in raw.items() if k in allowed})
    base["next_page"] = max(1, int(base.get("next_page") or 1))
    base["last_completed_page"] = max(0, int(base.get("last_completed_page") or 0))
    base["incremental_next_page"] = max(1, int(base.get("incremental_next_page") or 1))
    base["incremental_cycles_completed"] = max(
        0, int(base.get("incremental_cycles_completed") or 0)
    )
    base["backfill_complete"] = bool(base.get("backfill_complete"))
    base["phase"] = "incremental" if base["backfill_complete"] else "backfill"
    base["sort_backfill"] = SORT_BACKFILL
    base["sort_incremental"] = SORT_INCREMENTAL
    base["version"] = CHECKPOINT_VERSION
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
) -> dict[str, Any]:
    require_owns_job_lock(db, job=job, owner_token=owner_token)
    now = datetime.now(timezone.utc)
    payload = {
        **default_checkpoint(contract_status=contract_status),
        **checkpoint,
        "updated_at": now,
    }
    payload["sort_backfill"] = SORT_BACKFILL
    payload["sort_incremental"] = SORT_INCREMENTAL
    payload["version"] = CHECKPOINT_VERSION
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
