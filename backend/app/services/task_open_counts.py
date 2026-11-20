"""Hitung badge Tugas Tim — total actionable authoritative tanpa cap list 1000."""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from app.auth.roles import is_super_admin
from app.domain.operational import OPERATIONAL_ROLES
from app.repositories.task_repository import TASK_STATUS_DONE, tasks_collection
from app.services.object_auth import (
    ObjectAuthError,
    assert_branch_access,
    bypasses_branch,
    has_operations_full,
    is_actionable_open_task,
    loading_sj_visible_to_actor,
)


def empty_role_counts() -> dict[str, int]:
    return {role: 0 for role in OPERATIONAL_ROLES}


def count_actionable_open_by_role(
    db: Database,
    user: dict[str, Any],
) -> dict[str, int]:
    """Hitung tugas terbuka actionable per ``assigned_role``.

    Kontrak sama daftar tugas:
    - status ≠ selesai
    - bukan ``context.loading_cancelled``
    - cabang (fail-closed)
    - Loading-SJ: hanya assignee (kecuali SA / operations.full)
    - dedupe ``task_key``
    Tidak memakai limit 1000 list API.
    """
    from app.repositories import delivery_repository as deliveries
    from app.repositories.po_repository import get_by_accurate_id

    counts = empty_role_counts()
    known = set(OPERATIONAL_ROLES)
    seen: set[str] = set()
    elevated = is_super_admin(user.get("role")) or has_operations_full(user)
    all_branches = bypasses_branch(user)

    # Cache scope dokumen agar tidak N+1 berulang untuk PO/SJ sama.
    po_cache: dict[str, dict[str, Any] | None] = {}
    sj_cache: dict[str, dict[str, Any] | None] = {}

    cursor = tasks_collection(db).find(
        {"status": {"$ne": TASK_STATUS_DONE}},
        {
            "_id": 0,
            "task_key": 1,
            "task_type": 1,
            "assigned_role": 1,
            "status": 1,
            "ref": 1,
            "context": 1,
        },
    )

    for task in cursor:
        if not is_actionable_open_task(task):
            continue
        if not loading_sj_visible_to_actor(user, task):
            continue

        key = str(task.get("task_key") or "")
        if key:
            if key in seen:
                continue
            seen.add(key)

        if not elevated and not all_branches:
            ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
            scope_obj: dict[str, Any] | None = None
            sj_id = str(ref.get("accurate_delivery_note_id") or "").strip()
            po_id = str(ref.get("accurate_po_id") or "").strip()
            if sj_id:
                if sj_id not in sj_cache:
                    sj_cache[sj_id] = deliveries.get_delivery_note(db, sj_id)
                scope_obj = sj_cache[sj_id]
            elif po_id:
                if po_id not in po_cache:
                    po_cache[po_id] = get_by_accurate_id(db, po_id)
                scope_obj = po_cache[po_id]
            try:
                assert_branch_access(user, scope_object=scope_obj)
            except ObjectAuthError:
                continue

        role = str(task.get("assigned_role") or "")
        if role in known:
            counts[role] += 1

    return counts
