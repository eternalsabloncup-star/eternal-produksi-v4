"""Policy A — reversible soft-archive for historical non-accepted Loading SJ.

APPLY against production requires separate Owner authorization.
No hard-delete. No migration/index change — metadata lives in task.context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database
from pymongo import ReturnDocument

from app.repositories import delivery_repository as deliveries
from app.repositories import task_repository as tasks
from app.repositories.po_repository import get_by_accurate_id
from app.services.audit_log import append_audit_event
from app.services.delivery_service import LOADING_SJ_TASK
from app.services.po_acceptance import is_po_accepted

ARCHIVE_CTX_KEY = "loading_sj_archive"

CLASS_ELIGIBLE = "ELIGIBLE_ARCHIVE"
CLASS_ACTIVE_OR_COMPLETED = "EXCLUDE_ACTIVE_OR_COMPLETED"
CLASS_PARENT_INCONSISTENT = "EXCLUDE_PARENT_INCONSISTENT"
CLASS_EVIDENCE_CONFLICT = "EXCLUDE_EVIDENCE_CONFLICT"
CLASS_AMBIGUOUS = "EXCLUDE_AMBIGUOUS_RELATION"
CLASS_ALREADY = "ALREADY_ARCHIVED"


def _archive_block(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(task, dict):
        return None
    ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
    block = ctx.get(ARCHIVE_CTX_KEY)
    return block if isinstance(block, dict) else None


def is_soft_archived(task: dict[str, Any] | None) -> bool:
    block = _archive_block(task)
    return bool(block and block.get("archived") is True)


def _sj_id_from_task(task: dict[str, Any]) -> str:
    ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
    return str(ref.get("accurate_delivery_note_id") or "").strip()


def _parent_ids(task: dict[str, Any], note: dict[str, Any] | None) -> list[str]:
    found: list[str] = []
    ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
    if ref.get("accurate_po_id"):
        found.append(str(ref["accurate_po_id"]).strip())
    ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
    for raw in ctx.get("accurate_po_ids") or []:
        text = str(raw or "").strip()
        if text:
            found.append(text)
    if note:
        for pid in deliveries.note_parent_po_ids(note):
            found.append(pid)
        single = str(note.get("accurate_po_id") or "").strip()
        if single:
            found.append(single)
    return list(dict.fromkeys(p for p in found if p))


def classify_loading_sj_archive_candidate(
    db: Database, task: dict[str, Any]
) -> dict[str, Any]:
    """Classify one loading-sj task for soft-archive eligibility (read-only)."""
    task_key = str(task.get("task_key") or "")
    sj_id = _sj_id_from_task(task)
    note = deliveries.get_delivery_note(db, sj_id) if sj_id else None
    reasons: list[str] = []

    if is_soft_archived(task):
        reasons.append(CLASS_ALREADY)

    if not sj_id or note is None:
        reasons.append(CLASS_AMBIGUOUS)

    parents = _parent_ids(task, note)
    accepted_flags: list[bool] = []
    for pid in parents:
        po = get_by_accurate_id(db, pid)
        accepted_flags.append(is_po_accepted(po))

    if accepted_flags and any(accepted_flags) and not all(accepted_flags):
        reasons.append(CLASS_PARENT_INCONSISTENT)
    if accepted_flags and all(accepted_flags):
        # Policy A targets historical *non-accepted* parents only.
        reasons.append(CLASS_PARENT_INCONSISTENT)

    status = str(task.get("status") or "")
    if status in (tasks.TASK_STATUS_DONE, "completed", "done"):
        reasons.append(CLASS_ACTIVE_OR_COMPLETED)

    if sj_id and (
        deliveries.has_successful_delivery_event(db, sj_id)
        or deliveries.get_active_loading_event(db, sj_id) is not None
        or db["loading_events"].count_documents({"accurate_sj_id": sj_id}) > 0
        or db["delivery_events"].count_documents({"accurate_sj_id": sj_id}) > 0
    ):
        reasons.append(CLASS_EVIDENCE_CONFLICT)

    if not reasons and (not accepted_flags or not any(accepted_flags)):
        classification = CLASS_ELIGIBLE
    else:
        classification = reasons[0]

    return {
        "task_key": task_key,
        "accurate_sj_id": sj_id,
        "parent_po_ids": parents,
        "task_status": status,
        "note_present": note is not None,
        "classification": classification,
        "exclude_reasons": reasons,
        "eligible": classification == CLASS_ELIGIBLE,
    }


def build_archive_manifest(db: Database) -> dict[str, Any]:
    """Read-only eligibility manifest for all loading-sj tasks."""
    cursor = tasks.tasks_collection(db).find(
        {"task_type": LOADING_SJ_TASK},
        {"_id": 0, "task_key": 1, "status": 1, "ref": 1, "context": 1},
    )
    rows = [classify_loading_sj_archive_candidate(db, t) for t in cursor]
    eligible = [r for r in rows if r["eligible"]]
    excluded = [r for r in rows if not r["eligible"]]
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["classification"]] = by_class.get(r["classification"], 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(rows),
        "eligible_count": len(eligible),
        "excluded_count": len(excluded),
        "by_classification": by_class,
        "eligible": eligible,
        "excluded": excluded,
        "apply_status": "NOT_EXECUTED",
    }


def soft_archive_loading_sj_task(
    db: Database,
    *,
    task_key: str,
    actor_email: str,
    batch_id: str,
    reason: str = "POLICY_A_NON_ACCEPTED_PARENT",
    force: bool = False,
) -> dict[str, Any]:
    """Mark one task soft-archived. Idempotent. No hard-delete.

    Production APPLY must be Owner-authorized; callers should dry-run first.
    """
    task = tasks.get_task(db, task_key)
    if task is None:
        raise LookupError(f"task not found: {task_key}")
    if str(task.get("task_type") or "") != LOADING_SJ_TASK:
        raise ValueError("soft-archive only for loading-sj")

    if is_soft_archived(task):
        return {"task_key": task_key, "duplicate": True, "task": task}

    verdict = classify_loading_sj_archive_candidate(db, task)
    if not verdict["eligible"] and not force:
        raise ValueError(
            f"not eligible for archive: {verdict['classification']} "
            f"({','.join(verdict['exclude_reasons'])})"
        )

    now = datetime.now(timezone.utc)
    archive = {
        "archived": True,
        "reason": reason,
        "batch_id": batch_id,
        "archived_at": now,
        "archived_by": str(actor_email or "").strip().lower(),
        "reversible": True,
        "original_status": task.get("status"),
    }
    ctx = dict(task.get("context") or {})
    ctx[ARCHIVE_CTX_KEY] = archive
    updated = tasks.tasks_collection(db).find_one_and_update(
        {"task_key": task_key},
        {"$set": {"context": ctx, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    append_audit_event(
        db,
        action="loading_sj.soft_archive",
        actor_email=str(actor_email or ""),
        target_email=None,
        detail={
            "task_key": task_key,
            "batch_id": batch_id,
            "reason": reason,
            "accurate_sj_id": verdict.get("accurate_sj_id"),
            "classification": verdict.get("classification"),
        },
    )
    return {"task_key": task_key, "duplicate": False, "task": updated}


def restore_loading_sj_task(
    db: Database,
    *,
    task_key: str,
    actor_email: str,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Restore a soft-archived loading-sj task. Idempotent if already active."""
    task = tasks.get_task(db, task_key)
    if task is None:
        raise LookupError(f"task not found: {task_key}")
    if not is_soft_archived(task):
        return {"task_key": task_key, "duplicate": True, "task": task}

    now = datetime.now(timezone.utc)
    ctx = dict(task.get("context") or {})
    prev = dict(ctx.get(ARCHIVE_CTX_KEY) or {})
    ctx[ARCHIVE_CTX_KEY] = {
        **prev,
        "archived": False,
        "restored_at": now,
        "restored_by": str(actor_email or "").strip().lower(),
        "restore_batch_id": batch_id or prev.get("batch_id"),
    }
    updated = tasks.tasks_collection(db).find_one_and_update(
        {"task_key": task_key},
        {"$set": {"context": ctx, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    append_audit_event(
        db,
        action="loading_sj.soft_archive.restore",
        actor_email=str(actor_email or ""),
        target_email=None,
        detail={
            "task_key": task_key,
            "batch_id": batch_id or prev.get("batch_id"),
            "accurate_sj_id": _sj_id_from_task(task),
        },
    )
    return {"task_key": task_key, "duplicate": False, "task": updated}


def soft_archive_batch_dry_run(db: Database, *, batch_id: str) -> dict[str, Any]:
    """Dry-run only — never writes. Returns eligible task_keys for a batch plan."""
    manifest = build_archive_manifest(db)
    return {
        "batch_id": batch_id,
        "mode": "DRY_RUN",
        "eligible_count": manifest["eligible_count"],
        "excluded_count": manifest["excluded_count"],
        "by_classification": manifest["by_classification"],
        "task_keys": [r["task_key"] for r in manifest["eligible"]],
        "apply_status": "NOT_EXECUTED",
    }
