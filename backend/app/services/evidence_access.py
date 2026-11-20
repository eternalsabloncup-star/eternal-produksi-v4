"""Object-level authorization for evidence content streams.

Delegates branch/assignment rules to ``object_auth`` (satu kontrak dengan mutasi).
Role ``view`` alone is not universal access.
"""

from __future__ import annotations

from typing import Any, Final

from pymongo.database import Database

from app.services.object_auth import (
    ObjectAuthError,
    assert_branch_access,
    bypasses_assignment,
)
from app.services.task_engine import can_act

SCOPE_DESIGN: Final = "design"
SCOPE_SAMPLE: Final = "sample"
SCOPE_DELIVERY: Final = "delivery"

# Backward-compatible alias.
EvidenceAccessDenied = ObjectAuthError


def assert_branch_scope(
    user: dict[str, Any],
    *,
    scope_object: dict[str, Any] | None,
) -> None:
    assert_branch_access(user, scope_object=scope_object)


def assert_design_evidence_access(
    db: Database,
    user: dict[str, Any],
    *,
    po_doc: dict[str, Any],
) -> None:
    if not (
        can_act(db, user, task_type="design", action="view")
        or can_act(db, user, task_type="design", action="manage_evidence")
        or can_act(db, user, task_type="design", action="confirm")
        or bypasses_assignment(user)
    ):
        raise EvidenceAccessDenied("Tidak berizin melihat bukti desain.")
    assert_branch_access(user, scope_object=po_doc)


def assert_sample_evidence_access(
    db: Database,
    user: dict[str, Any],
    *,
    allocation: dict[str, Any],
    po_doc: dict[str, Any] | None = None,
) -> None:
    """Operator hanya alokasi miliknya; KP/approval melihat terkait izinnya."""
    if not (
        can_act(db, user, task_type="konfirmasi-sample", action="view")
        or can_act(db, user, task_type="konfirmasi-sample", action="manage_evidence")
        or can_act(db, user, task_type="approval-sampel", action="view")
        or can_act(db, user, task_type="approval-sampel", action="approve")
        or bypasses_assignment(user)
    ):
        raise EvidenceAccessDenied("Tidak berizin melihat bukti sampel.")

    # Assignment metadata is not an auth gate (Owner contract).
    assert_branch_access(user, scope_object=po_doc or allocation)


def assert_delivery_evidence_access(
    db: Database,
    user: dict[str, Any],
    *,
    note: dict[str, Any],
) -> None:
    if not (
        can_act(db, user, task_type="pengiriman", action="view")
        or can_act(db, user, task_type="pengiriman", action="confirm")
        or can_act(db, user, task_type="pengiriman", action="manage_evidence")
        or can_act(db, user, task_type="pengiriman-gagal-koordinasi", action="view")
        or can_act(db, user, task_type="pengiriman-gagal-koordinasi", action="confirm")
        or bypasses_assignment(user)
    ):
        raise EvidenceAccessDenied("Tidak berizin melihat bukti pengiriman.")

    # No exclusive courier assignment (Owner contract).
    assert_branch_access(user, scope_object=note)
