"""Durable Mongo audit untuk Controlled SJ Replay.

Collections:
- controlled_sj_replay_runs
- controlled_sj_replay_items
- controlled_sj_replay_events (append-only)

Tidak menyimpan token mentah, credential, atau payload pelanggan.
Index bootstrap hanya untuk test — production APPLY fail-closed jika belum ada.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

RUNS = "controlled_sj_replay_runs"
ITEMS = "controlled_sj_replay_items"
EVENTS = "controlled_sj_replay_events"

ITEM_OUTCOMES = frozenset(
    {
        "pending",
        "processing",
        "created",
        "unchanged",
        "already_present",
        "conflict",
        "failed",
        "ineligible",
        "reconciliation_required",
    }
)

REQUIRED_INDEX_SPECS: list[dict[str, Any]] = [
    {
        "collection": RUNS,
        "keys": [("run_id", ASCENDING)],
        "name": "uniq_ctrl_sj_replay_run_id",
        "unique": True,
    },
    {
        "collection": RUNS,
        "keys": [("batch_id", ASCENDING), ("started_at", ASCENDING)],
        "name": "idx_ctrl_sj_replay_runs_batch_started",
        "unique": False,
    },
    {
        "collection": ITEMS,
        "keys": [("run_id", ASCENDING), ("accurate_sj_id", ASCENDING)],
        "name": "uniq_ctrl_sj_replay_item_run_sj",
        "unique": True,
    },
    {
        "collection": ITEMS,
        "keys": [("batch_id", ASCENDING), ("accurate_sj_id", ASCENDING)],
        "name": "idx_ctrl_sj_replay_items_batch_sj",
        "unique": False,
    },
    {
        "collection": EVENTS,
        "keys": [("run_id", ASCENDING), ("at", ASCENDING)],
        "name": "idx_ctrl_sj_replay_events_run_at",
        "unique": False,
    },
]


def ensure_controlled_sj_replay_audit_indexes(db: Database) -> list[str]:
    """Bootstrap/test only — jangan panggil dari production APPLY path."""
    created: list[str] = []
    for spec in REQUIRED_INDEX_SPECS:
        name = db[spec["collection"]].create_index(
            spec["keys"],
            name=spec["name"],
            unique=bool(spec.get("unique")),
        )
        created.append(name)
    from app.services.index_preflight import mark_index_specs_verified

    mark_index_specs_verified([s["name"] for s in REQUIRED_INDEX_SPECS], db=db)
    return created


def require_controlled_sj_replay_audit_indexes(db: Database) -> None:
    from app.services.index_preflight import require_index_specs

    require_index_specs(db, REQUIRED_INDEX_SPECS)


def new_run_id() -> str:
    return secrets.token_hex(16)


def lease_fingerprint(owner_token: str) -> str:
    digest = hashlib.sha256(str(owner_token or "").encode("utf-8")).hexdigest()
    return digest[:24]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def append_event(
    db: Database,
    *,
    batch_id: str,
    run_id: str,
    event: str,
    accurate_sj_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_controlled_sj_replay_audit_indexes(db)
    safe = dict(detail or {})
    for banned in (
        "password",
        "token",
        "secret",
        "cookie",
        "api_key",
        "payload",
        "customer",
        "owner_token",
    ):
        safe.pop(banned, None)
    doc = {
        "batch_id": batch_id,
        "run_id": run_id,
        "accurate_sj_id": accurate_sj_id,
        "event": event,
        "detail": safe,
        "at": _now(),
    }
    db[EVENTS].insert_one(doc)
    return doc


def start_run(
    db: Database,
    *,
    batch_id: str,
    manifest_sha256: str,
    id_list_sha256: str,
    live_sha: str | None,
    mode: str,
    sj_count: int,
    lease_fp: str | None,
    fence_epoch: int | None,
) -> dict[str, Any]:
    require_controlled_sj_replay_audit_indexes(db)
    run_id = new_run_id()
    doc = {
        "run_id": run_id,
        "batch_id": batch_id,
        "manifest_sha256": manifest_sha256,
        "id_list_sha256": id_list_sha256,
        "live_sha": live_sha,
        "mode": mode,
        "status": "running",
        "sj_count": int(sj_count),
        "started_at": _now(),
        "completed_at": None,
        "lease_fingerprint": lease_fp,
        "fence_epoch": fence_epoch,
        "summary": {},
        "stopped": False,
        "stop_reason": None,
    }
    db[RUNS].insert_one(doc)
    append_event(
        db,
        batch_id=batch_id,
        run_id=run_id,
        event="run_started",
        detail={
            "mode": mode,
            "sj_count": sj_count,
            "live_sha": live_sha,
            "id_list_sha256": id_list_sha256,
        },
    )
    return doc


def finish_run(
    db: Database,
    *,
    run_id: str,
    status: str,
    summary: dict[str, Any],
    stopped: bool = False,
    stop_reason: str | None = None,
) -> dict[str, Any] | None:
    require_controlled_sj_replay_audit_indexes(db)
    doc = db[RUNS].find_one_and_update(
        {"run_id": run_id},
        {
            "$set": {
                "status": status,
                "summary": summary,
                "stopped": bool(stopped),
                "stop_reason": stop_reason,
                "completed_at": _now(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc is not None:
        append_event(
            db,
            batch_id=str(doc.get("batch_id") or ""),
            run_id=run_id,
            event="run_finished",
            detail={
                "status": status,
                "stopped": stopped,
                "stop_reason": stop_reason,
                "summary": summary,
            },
        )
    return doc


def mark_item_processing(
    db: Database,
    *,
    batch_id: str,
    run_id: str,
    accurate_sj_id: str,
    expected_so_id: str,
    expected_branch_id: str,
    lease_fp: str | None,
    attempt_number: int,
    sj_present_before: bool,
) -> dict[str, Any]:
    require_controlled_sj_replay_audit_indexes(db)
    now = _now()
    doc = {
        "batch_id": batch_id,
        "run_id": run_id,
        "accurate_sj_id": str(accurate_sj_id),
        "expected_so_id": str(expected_so_id),
        "expected_branch_id": str(expected_branch_id),
        "outcome": "processing",
        "writes": 0,
        "error_code": None,
        "attempt_number": int(attempt_number),
        "lease_fingerprint": lease_fp,
        "reconciliation_status": "none",
        "sj_present_before": bool(sj_present_before),
        "sj_present_after": None,
        "review_resolution": None,
        "loading_task_outcome": None,
        "started_at": now,
        "completed_at": None,
    }
    try:
        db[ITEMS].insert_one(doc)
    except DuplicateKeyError:
        existing = db[ITEMS].find_one(
            {"run_id": run_id, "accurate_sj_id": str(accurate_sj_id)}
        )
        if existing and existing.get("outcome") in {
            "created",
            "already_present",
            "unchanged",
        }:
            return existing
        doc = db[ITEMS].find_one_and_update(
            {"run_id": run_id, "accurate_sj_id": str(accurate_sj_id)},
            {
                "$set": {
                    "outcome": "processing",
                    "writes": 0,
                    "error_code": None,
                    "attempt_number": int(attempt_number),
                    "lease_fingerprint": lease_fp,
                    "reconciliation_status": "none",
                    "sj_present_before": bool(sj_present_before),
                    "sj_present_after": None,
                    "review_resolution": None,
                    "loading_task_outcome": None,
                    "started_at": now,
                    "completed_at": None,
                    "expected_so_id": str(expected_so_id),
                    "expected_branch_id": str(expected_branch_id),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        assert doc is not None
    append_event(
        db,
        batch_id=batch_id,
        run_id=run_id,
        accurate_sj_id=str(accurate_sj_id),
        event="item_processing",
        detail={"attempt_number": attempt_number},
    )
    return doc


def complete_item(
    db: Database,
    *,
    run_id: str,
    accurate_sj_id: str,
    outcome: str,
    writes: int,
    error_code: str | None = None,
    reconciliation_status: str = "none",
    sj_present_after: bool | None = None,
    review_resolution: str | None = None,
    loading_task_outcome: str | None = None,
) -> dict[str, Any] | None:
    if outcome not in ITEM_OUTCOMES:
        raise ValueError(f"outcome tidak dikenal: {outcome}")
    require_controlled_sj_replay_audit_indexes(db)
    doc = db[ITEMS].find_one_and_update(
        {"run_id": run_id, "accurate_sj_id": str(accurate_sj_id)},
        {
            "$set": {
                "outcome": outcome,
                "writes": int(writes),
                "error_code": error_code,
                "reconciliation_status": reconciliation_status,
                "sj_present_after": sj_present_after,
                "review_resolution": review_resolution,
                "loading_task_outcome": loading_task_outcome,
                "completed_at": _now(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if doc is not None:
        append_event(
            db,
            batch_id=str(doc.get("batch_id") or ""),
            run_id=run_id,
            accurate_sj_id=str(accurate_sj_id),
            event="item_completed",
            detail={
                "outcome": outcome,
                "writes": writes,
                "error_code": error_code,
                "reconciliation_status": reconciliation_status,
                "review_resolution": review_resolution,
                "loading_task_outcome": loading_task_outcome,
            },
        )
    return doc


def mark_reconciliation_required(
    db: Database,
    *,
    run_id: str,
    accurate_sj_id: str,
    error_code: str,
    sj_present_after: bool,
    writes: int = 0,
) -> dict[str, Any] | None:
    return complete_item(
        db,
        run_id=run_id,
        accurate_sj_id=accurate_sj_id,
        outcome="reconciliation_required",
        writes=writes if writes else (1 if sj_present_after else 0),
        error_code=error_code,
        reconciliation_status="required",
        sj_present_after=sj_present_after,
    )
