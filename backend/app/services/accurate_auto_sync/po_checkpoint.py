"""PO checkpoint — thin wrapper over shared job checkpoint (compat)."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.services.accurate_auto_sync.checkpoint import (
    CHECKPOINT_VERSION,
    CONTRACT_SO,
    SORT_BACKFILL,
    SORT_INCREMENTAL,
    default_checkpoint,
    load_job_checkpoint,
    save_job_checkpoint,
)

# Re-export constants used by tests / docs.
__all__ = [
    "CHECKPOINT_VERSION",
    "SORT_BACKFILL",
    "SORT_INCREMENTAL",
    "default_po_checkpoint",
    "load_po_checkpoint",
    "save_po_checkpoint",
]


def default_po_checkpoint() -> dict[str, Any]:
    return default_checkpoint(contract_status=CONTRACT_SO)


def load_po_checkpoint(db: Database) -> dict[str, Any]:
    return load_job_checkpoint(db, "po", contract_status=CONTRACT_SO)


def save_po_checkpoint(
    db: Database,
    checkpoint: dict[str, Any],
    *,
    owner_token: str,
) -> dict[str, Any]:
    return save_job_checkpoint(
        db, "po", checkpoint, owner_token=owner_token, contract_status=CONTRACT_SO
    )
