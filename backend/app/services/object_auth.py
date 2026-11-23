"""Object-level authorization — satu kontrak untuk mutasi & evidence.

Owner contract (PR #18):
- Master Permission (task action) is the authority for actions.
- ``operations.full`` = full operational access across all branches;
  bypasses operational assignment restrictions; does NOT grant user/role/
  settings admin. Payment bypass: YES (operations.full OR explicit grant).
- ``access.all_branches`` = cross-branch only; still needs action grants.
- Assignment metadata (operator_email / assigned_driver_email) is NOT an
  authorization gate — audit records the actual actor.
- Empty ``branch_ids`` = DENIED for branch-scoped resources unless elevated.
"""

from __future__ import annotations

from typing import Any, Final

from pymongo.database import Database

from app.auth.permissions import (
    PERM_ACCESS_ALL_BRANCHES,
    PERM_OPERATIONS_FULL,
    has_permission,
)
from app.auth.roles import is_super_admin
from app.models.user import normalize_email


class ObjectAuthError(PermissionError):
    """Aktor tidak berizin pada cakupan objek (cabang)."""

    def __init__(self, message: str = "Akses objek ditolak.") -> None:
        super().__init__(message)


# Alias agar evidence_access dapat memakai tipe yang sama.
ObjectAccessDenied = ObjectAuthError


def actor_email(user: dict[str, Any]) -> str:
    return normalize_email(str(user.get("email_normalized") or user.get("email") or ""))


def user_branch_ids(user: dict[str, Any]) -> set[str]:
    from app.services.branch_identity import normalize_branch_identity

    raw = user.get("branch_ids") or []
    out: set[str] = set()
    for item in raw:
        code = normalize_branch_identity(item)
        if code:
            out.add(code)
    return out


def object_branch_code(obj: dict[str, Any] | None) -> str | None:
    """Identitas cabang otoritatif untuk object scope.

    Prioritas (kontrak Accurate branchId kanonis):
    1. ``branch_id`` dokumen (kanonis) — menang atas meta Accurate lama
    2. Fallback Accurate meta / ``accurate_branch_id`` hanya jika kanonis kosong
       (lihat ``extract_accurate_branch_id``)
    3. Legacy ``branch_code`` (transisi) hanya jika 1–2 kosong
    4. Objek ``branch.{id|code|no}``
    ``branch_name`` saja tidak cukup. Meta Accurate tidak boleh menimpa
    ``branch_id`` yang sudah diedit.
    """
    if not isinstance(obj, dict):
        return None
    from app.services.branch_identity import (
        extract_accurate_branch_id,
        normalize_branch_identity,
    )

    accurate = extract_accurate_branch_id(obj)
    if accurate:
        return accurate
    legacy = normalize_branch_identity(obj.get("branch_code"))
    if legacy:
        return legacy
    branch = obj.get("branch")
    if isinstance(branch, dict):
        for key in ("id", "no", "code"):
            nid = normalize_branch_identity(branch.get(key))
            if nid:
                return nid
    elif branch is not None and str(branch).strip() and not str(branch).strip().startswith("{"):
        return normalize_branch_identity(branch)
    return None


def has_operations_full(user: dict[str, Any]) -> bool:
    """Effective ``operations.full`` from session user — never trust client body."""
    return has_permission(user, PERM_OPERATIONS_FULL)


def bypasses_assignment(user: dict[str, Any]) -> bool:
    """Assignment metadata is not an auth gate; kept for elevated evidence paths."""
    if is_super_admin(user.get("role")):
        return True
    return has_operations_full(user)


def bypasses_branch(user: dict[str, Any]) -> bool:
    """Super Admin, operations.full, or access.all_branches."""
    if is_super_admin(user.get("role")):
        return True
    if has_operations_full(user):
        return True
    return has_permission(user, PERM_ACCESS_ALL_BRANCHES)


def assert_branch_access(
    user: dict[str, Any],
    *,
    scope_object: dict[str, Any] | None,
) -> None:
    """Fail-closed for limited accounts; elevated ops skip branch enumeration."""
    if bypasses_branch(user):
        return
    branches = user_branch_ids(user)
    obj_branch = object_branch_code(scope_object)
    if not branches:
        raise ObjectAuthError(
            "Akun tanpa scope cabang yang valid — akses ditolak."
        )
    if obj_branch is None:
        raise ObjectAuthError(
            "Scope cabang objek tidak dapat divalidasi — akses ditolak."
        )
    if obj_branch not in branches:
        raise ObjectAuthError(
            "Objek di luar cabang yang diizinkan untuk akun ini."
        )


def authoritative_driver_email(note: dict[str, Any] | None) -> str:
    """Historical metadata only — not an authorization gate."""
    if not isinstance(note, dict):
        return ""
    return normalize_email(
        str(note.get("assigned_driver_email") or note.get("driver_email") or "")
    )


def assert_operator_assignment(
    db: Database,
    user: dict[str, Any],
    *,
    allocation: dict[str, Any],
    allow_approver: bool = False,
) -> None:
    """No-op: Owner contract — task action + branch authorize; not operator_email."""
    _ = (db, user, allocation, allow_approver)
    return


def assert_driver_assignment(
    db: Database,
    user: dict[str, Any],
    *,
    note: dict[str, Any],
    allow_coordinator: bool = False,
) -> None:
    """No-op: Owner contract — no mandatory/exclusive courier assignment."""
    _ = (db, user, note, allow_coordinator)
    return


ALLOCATION_SCOPED_TASKS: Final[frozenset[str]] = frozenset(
    {
        "screen",
        "konfirmasi-sample",
        "approval-sampel",
        "cetak",
    }
)
DELIVERY_SCOPED_TASKS: Final[frozenset[str]] = frozenset(
    {
        "pengiriman",
        "qty-loading-sj-berbeda",
        "pengiriman-gagal-koordinasi",
    }
)


def loading_sj_visible_to_actor(user: dict[str, Any], task: dict[str, Any]) -> bool:
    """Visibility Loading-SJ untuk worklist/badge — selaras filterTasksForActor FE.

    Super Admin / operations.full / view_all (read): semua.
    Tanpa assignee: terlihat. Dengan assignee: hanya email yang ditugaskan.
    """
    from app.auth.permissions import has_view_all_tasks

    if str(task.get("task_type") or "") != "loading-sj":
        return True
    if (
        is_super_admin(user.get("role"))
        or has_operations_full(user)
        or has_view_all_tasks(user)
    ):
        return True
    ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
    assigned = str(ctx.get("assigned_user_email") or "").strip().lower()
    if not assigned:
        return True
    return actor_email(user) == assigned


def is_actionable_open_task(task: dict[str, Any] | None) -> bool:
    """Actionable = belum selesai dan bukan Loading yang dibatalkan."""
    from app.repositories.task_repository import TASK_STATUS_DONE

    if not isinstance(task, dict):
        return False
    if str(task.get("status") or "") == TASK_STATUS_DONE:
        return False
    ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
    if ctx.get("loading_cancelled") is True:
        return False
    # Preliminary: actionable sampai semua SJ terkait punya loading-sj.
    # SJ dokumen saja (tanpa loading-sj) tetap visible/actionable informatif.
    if str(task.get("task_type") or "") == "loading-menunggu-sj":
        if ctx.get("loading_task_ready") is True:
            return False
    return True


def resolve_task_source_po_ids(
    db: Database,
    task: dict[str, Any],
    *,
    sj_cache: dict[str, dict[str, Any] | None] | None = None,
) -> list[str]:
    """Kumpulkan PO sumber tugas (ref + context + parent SJ). Dedup urut."""
    from app.repositories import delivery_repository as deliveries

    ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
    ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
    found: list[str] = []

    po_id = str(ref.get("accurate_po_id") or "").strip()
    if po_id:
        found.append(po_id)

    ctx_ids = ctx.get("accurate_po_ids")
    if isinstance(ctx_ids, list):
        for raw in ctx_ids:
            text = str(raw or "").strip()
            if text:
                found.append(text)

    sj_id = str(ref.get("accurate_delivery_note_id") or "").strip()
    if sj_id:
        cache = sj_cache if sj_cache is not None else {}
        if sj_id not in cache:
            cache[sj_id] = deliveries.get_delivery_note(db, sj_id)
        for parent in deliveries.note_parent_po_ids(cache[sj_id]):
            found.append(parent)

    return list(dict.fromkeys(found))


def task_has_po_source_ref(task: dict[str, Any] | None) -> bool:
    """True bila tugas mengklaim sumber PO dan/atau SJ (bukan tugas tanpa dokumen)."""
    if not isinstance(task, dict):
        return False
    ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
    ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
    if str(ref.get("accurate_po_id") or "").strip():
        return True
    if str(ref.get("accurate_delivery_note_id") or "").strip():
        return True
    ctx_ids = ctx.get("accurate_po_ids")
    if isinstance(ctx_ids, list) and any(str(x or "").strip() for x in ctx_ids):
        return True
    return False


def task_source_po_accepted(
    db: Database,
    task: dict[str, Any],
    *,
    po_cache: dict[str, dict[str, Any] | None] | None = None,
    sj_cache: dict[str, dict[str, Any] | None] | None = None,
) -> bool:
    """Gate visibilitas: tugas berbasis PO/SJ hanya bila SEMUA parent PO accepted.

    Sumber otoritatif: ``operational.acceptance.status == accepted``.
    Keberadaan SJ tidak menyembunyikan tugas bila PO sudah accepted.
    Fail-closed: klaim sumber PO/SJ tetapi parent tidak ter-resolve → sembunyikan.
    Tugas tanpa ref PO/SJ → tidak digate (bukan PO-based).
    """
    from app.repositories.po_repository import get_by_accurate_id
    from app.services.po_acceptance import is_po_accepted

    if not task_has_po_source_ref(task):
        return True
    po_ids = resolve_task_source_po_ids(db, task, sj_cache=sj_cache)
    if not po_ids:
        return False
    cache = po_cache if po_cache is not None else {}
    for po_id in po_ids:
        if po_id not in cache:
            cache[po_id] = get_by_accurate_id(db, po_id)
        if not is_po_accepted(cache[po_id]):
            return False
    return True


def filter_tasks_for_actor(
    db: Database,
    user: dict[str, Any],
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter PO-accepted + cabang + visibility Loading-SJ assignee."""
    from app.repositories import delivery_repository as deliveries
    from app.repositories.po_repository import get_by_accurate_id

    elevated = is_super_admin(user.get("role")) or has_operations_full(user)
    out: list[dict[str, Any]] = []
    all_branches = bypasses_branch(user)
    po_cache: dict[str, dict[str, Any] | None] = {}
    sj_cache: dict[str, dict[str, Any] | None] = {}

    for task in items:
        if not task_source_po_accepted(
            db, task, po_cache=po_cache, sj_cache=sj_cache
        ):
            continue
        if not loading_sj_visible_to_actor(user, task):
            continue
        if elevated or all_branches:
            out.append(task)
            continue
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
        out.append(task)
    return out


def filter_delivery_notes_for_actor(
    user: dict[str, Any], notes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Saring daftar SJ agar akun terbatas tidak melihat cabang lain."""
    if bypasses_branch(user):
        return notes
    out: list[dict[str, Any]] = []
    for note in notes:
        try:
            assert_branch_access(user, scope_object=note)
        except ObjectAuthError:
            continue
        out.append(note)
    return out


def require_po_branch_access(
    db: Database, user: dict[str, Any], accurate_po_id: str
) -> dict[str, Any]:
    """GET/detail PO operasional: fail-closed bila cabang tidak terbukti."""
    from app.repositories.po_repository import get_by_accurate_id

    po_doc = get_by_accurate_id(db, str(accurate_po_id))
    if po_doc is None:
        raise LookupError(f"PO tidak ditemukan: {accurate_po_id}")
    assert_branch_access(user, scope_object=po_doc)
    return po_doc


def require_sj_branch_access(
    db: Database, user: dict[str, Any], accurate_sj_id: str
) -> dict[str, Any]:
    from app.repositories import delivery_repository as deliveries

    note = deliveries.get_delivery_note(db, str(accurate_sj_id))
    if note is None:
        raise LookupError(f"Surat Jalan tidak ditemukan: {accurate_sj_id}")
    assert_branch_access(user, scope_object=note)
    return note


def require_allocation_branch_access(
    db: Database, user: dict[str, Any], allocation_id: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from app.repositories import schedule_repository as schedules
    from app.repositories.po_repository import get_by_accurate_id

    alloc = schedules.get_allocation(db, str(allocation_id))
    if alloc is None:
        raise LookupError(f"Alokasi tidak ditemukan: {allocation_id}")
    po_doc = get_by_accurate_id(db, str(alloc.get("accurate_po_id") or ""))
    scope = po_doc if object_branch_code(po_doc) else (
        alloc if object_branch_code(alloc) else po_doc or alloc
    )
    assert_branch_access(user, scope_object=scope)
    return alloc, po_doc


def require_task_branch_access(
    db: Database, user: dict[str, Any], task: dict[str, Any]
) -> None:
    scope = resolve_scope_object_for_task(db, task)
    assert_branch_access(user, scope_object=scope)


def resolve_scope_object_for_task(
    db: Database, task: dict[str, Any]
) -> dict[str, Any] | None:
    """Objek otoritatif untuk cek cabang — utamakan PO/SJ ber-branch_code."""
    from app.repositories import delivery_repository as deliveries
    from app.repositories import schedule_repository as schedules
    from app.repositories.po_repository import get_by_accurate_id

    ref = task.get("ref") if isinstance(task.get("ref"), dict) else {}
    tt = str(task.get("task_type") or "")
    if tt in DELIVERY_SCOPED_TASKS:
        sj_id = ref.get("accurate_delivery_note_id")
        return deliveries.get_delivery_note(db, str(sj_id)) if sj_id else None
    po_id = ref.get("accurate_po_id")
    po_doc = get_by_accurate_id(db, str(po_id)) if po_id else None
    if tt in ALLOCATION_SCOPED_TASKS:
        alloc_id = ref.get("allocation_id")
        alloc = schedules.get_allocation(db, str(alloc_id)) if alloc_id else None
        if po_doc and object_branch_code(po_doc):
            return po_doc
        if alloc and object_branch_code(alloc):
            return alloc
        return po_doc or alloc
    return po_doc
