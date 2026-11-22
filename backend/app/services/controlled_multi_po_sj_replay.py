"""Controlled native multi-PO SJ replay — sealed 6-ID P10 batch.

Kontrak:
- Batch terpisah dari P7D ``controlled_sj_replay_20261122``.
- Exact allowlist 6 SJ; 120851 excluded forever.
- Satu Accurate SJ ID = satu dokumen lokal kanonis + N parent.
- DRY_RUN default (0 Mongo write termasuk audit).
- APPLY: exclusive po_repair_maintenance + durable audit (collections P7D, batch_id beda).
- Tidak Loading/Delivery confirmation; tidak Accepted/Payment/Inventory.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from pymongo.database import Database

from app.integrations.accurate.fields import as_text
from app.repositories import delivery_repository as deliveries
from app.repositories.po_repository import get_by_accurate_id
from app.db_guard import (
    assert_safe_mongo_production_sync_target,
    assert_safe_mongo_sync_target,
)
from app.services.branch_identity import normalize_branch_identity
from app.services.delivery_sync import MongoGuardMode

BATCH_ID = "controlled_multi_po_sj_replay_p10_20261123"
DEFAULT_MANIFEST_NAME = "controlled_multi_po_sj_replay_p10_20261123.json"
EXCLUDED_FOREVER = frozenset({"120851"})
ALLOWED_BRANCHES = frozenset({"50", "150"})
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
MAINTENANCE_ACTOR = "controlled_multi_po_sj_replay_p10_apply"
DEFAULT_MAINTENANCE_TTL_SECONDS = 1800

SYSTEM_USER = {
    "email_normalized": "system:controlled-multi-po-sj-replay",
    "role": "super_admin",
}

Outcome = Literal[
    "eligible",
    "ineligible",
    "already_present",
    "created",
    "unchanged",
    "conflict",
    "failed",
    "reconciliation_required",
]


class ControlledMultiPoSjReplayError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManifestEntry:
    accurate_sj_id: str
    parent_po_ids: tuple[str, ...]
    branch_id: str
    customer_id: str = ""
    detail_attr_sha256: str = ""
    item_count: int = 0


@dataclass(frozen=True)
class SealedManifest:
    label: str
    version: str
    batch_id: str
    audit_date: str
    audit_source: str
    sj_count: int
    id_list_sha256: str
    manifest_sha256: str
    entries: tuple[ManifestEntry, ...]
    path: Path

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(e.accurate_sj_id for e in self.entries)

    def entry(self, accurate_sj_id: str) -> ManifestEntry | None:
        aid = str(accurate_sj_id)
        for e in self.entries:
            if e.accurate_sj_id == aid:
                return e
        return None


@dataclass(frozen=True)
class ReplayApproval:
    mode: Literal["dry_run", "apply"]
    expect_manifest_sha256: str
    expect_id_list_sha256: str
    expect_sj_count: int
    expect_batch_id: str
    execute: bool = False
    i_understand_this_writes_production: bool = False
    expect_live_sha: str | None = None
    confirm_batch_id: str | None = None


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "manifests" / DEFAULT_MANIFEST_NAME


def compute_id_list_sha256(sj_ids: list[str]) -> str:
    blob = "\n".join(sj_ids) + ("\n" if sj_ids else "")
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_manifest_sha256(body: Mapping[str, Any]) -> str:
    payload = {k: v for k, v in body.items() if k != "manifest_sha256"}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_sealed_manifest(path: Path | None = None) -> SealedManifest:
    manifest_path = path or default_manifest_path()
    if not manifest_path.is_file():
        raise ControlledMultiPoSjReplayError(
            f"manifest tidak ditemukan: {manifest_path}",
            code="manifest_missing",
        )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ControlledMultiPoSjReplayError(
            "manifest bukan object", code="manifest_invalid"
        )

    expected_meta = compute_manifest_sha256(raw)
    got_meta = str(raw.get("manifest_sha256") or "")
    if got_meta != expected_meta:
        raise ControlledMultiPoSjReplayError(
            "manifest_sha256 mismatch — tampering/STOP",
            code="manifest_checksum_mismatch",
        )

    batch_id = str(raw.get("batch_id") or "")
    label = str(raw.get("label") or "")
    if batch_id != BATCH_ID or label != BATCH_ID:
        raise ControlledMultiPoSjReplayError(
            f"batch_id tidak dikenal: {batch_id}",
            code="manifest_batch_invalid",
        )

    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ControlledMultiPoSjReplayError("entries kosong", code="manifest_empty")

    sj_count = int(raw.get("sj_count") or 0)
    if sj_count != 6 or len(entries_raw) != 6:
        raise ControlledMultiPoSjReplayError(
            "sj_count/entries wajib tepat 6",
            code="manifest_count_mismatch",
        )

    excluded = {
        str(x).strip() for x in (raw.get("excluded_sj_ids") or []) if str(x).strip()
    }
    if not EXCLUDED_FOREVER.issubset(excluded):
        raise ControlledMultiPoSjReplayError(
            "excluded_sj_ids harus mencakup 120851",
            code="manifest_exclusion_missing",
        )

    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    for row in entries_raw:
        if not isinstance(row, dict):
            raise ControlledMultiPoSjReplayError(
                "entry bukan object", code="manifest_entry_invalid"
            )
        sj_id = as_text(row.get("accurate_sj_id"))
        if not sj_id or not sj_id.isdigit():
            raise ControlledMultiPoSjReplayError(
                "accurate_sj_id invalid", code="manifest_entry_invalid"
            )
        if sj_id in EXCLUDED_FOREVER:
            raise ControlledMultiPoSjReplayError(
                f"SJ {sj_id} excluded forever",
                code="manifest_excluded_id",
            )
        if sj_id in seen:
            raise ControlledMultiPoSjReplayError(
                f"duplikat SJ {sj_id}", code="manifest_duplicate_id"
            )
        seen.add(sj_id)
        parents_raw = row.get("parent_po_ids") or []
        if not isinstance(parents_raw, list) or len(parents_raw) < 2:
            raise ControlledMultiPoSjReplayError(
                f"parent_po_ids multi wajib untuk {sj_id}",
                code="manifest_parents_invalid",
            )
        parents = tuple(
            sorted(
                (as_text(x) for x in parents_raw if as_text(x).isdigit()),
                key=lambda x: int(x),
            )
        )
        if len(parents) < 2 or len(parents) != len(set(parents)):
            raise ControlledMultiPoSjReplayError(
                f"parent_po_ids invalid untuk {sj_id}",
                code="manifest_parents_invalid",
            )
        branch = normalize_branch_identity(row.get("branch_id")) or as_text(
            row.get("branch_id")
        )
        if branch not in ALLOWED_BRANCHES:
            raise ControlledMultiPoSjReplayError(
                f"branch out of scope: {branch}",
                code="manifest_branch_out_of_scope",
            )
        entries.append(
            ManifestEntry(
                accurate_sj_id=sj_id,
                parent_po_ids=parents,
                branch_id=branch or "",
                customer_id=as_text(row.get("customer_id")),
                detail_attr_sha256=as_text(row.get("detail_attr_sha256")).lower(),
                item_count=int(row.get("item_count") or 0),
            )
        )

    id_list = [e.accurate_sj_id for e in entries]
    computed_ids = compute_id_list_sha256(id_list)
    claimed_ids = str(raw.get("id_list_sha256") or "")
    if claimed_ids != computed_ids:
        raise ControlledMultiPoSjReplayError(
            "id_list_sha256 mismatch",
            code="manifest_id_list_mismatch",
        )

    return SealedManifest(
        label=label,
        version=str(raw.get("version") or ""),
        batch_id=batch_id,
        audit_date=str(raw.get("audit_date") or ""),
        audit_source=str(raw.get("audit_source") or ""),
        sj_count=sj_count,
        id_list_sha256=claimed_ids,
        manifest_sha256=got_meta,
        entries=tuple(entries),
        path=manifest_path,
    )


def assert_id_in_allowlist(manifest: SealedManifest, accurate_sj_id: str) -> ManifestEntry:
    aid = str(accurate_sj_id)
    if aid in EXCLUDED_FOREVER:
        raise ControlledMultiPoSjReplayError(
            f"SJ {aid} excluded forever",
            code="excluded_forever",
        )
    entry = manifest.entry(aid)
    if entry is None:
        raise ControlledMultiPoSjReplayError(
            f"SJ {aid} di luar allowlist sealed",
            code="not_in_allowlist",
        )
    return entry


def _normalize_sha40(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not SHA40_RE.fullmatch(text):
        return None
    return text


def assert_runtime_approval(
    approval: ReplayApproval,
    *,
    manifest: SealedManifest,
    app_env: str,
    live_sha: str | None = None,
) -> None:
    if approval.expect_batch_id != manifest.batch_id:
        raise ControlledMultiPoSjReplayError(
            "expect_batch_id mismatch", code="approval_batch_mismatch"
        )
    if approval.expect_manifest_sha256 != manifest.manifest_sha256:
        raise ControlledMultiPoSjReplayError(
            "expect_manifest_sha256 mismatch",
            code="approval_checksum_mismatch",
        )
    if approval.expect_id_list_sha256 != manifest.id_list_sha256:
        raise ControlledMultiPoSjReplayError(
            "expect_id_list_sha256 mismatch",
            code="approval_id_list_mismatch",
        )
    if int(approval.expect_sj_count) != manifest.sj_count:
        raise ControlledMultiPoSjReplayError(
            "expect_sj_count mismatch", code="approval_count_mismatch"
        )
    if approval.mode == "dry_run":
        return
    if approval.mode != "apply":
        raise ControlledMultiPoSjReplayError(
            "mode invalid", code="approval_mode_invalid"
        )
    if not approval.execute:
        raise ControlledMultiPoSjReplayError(
            "APPLY ditolak: execute=false", code="apply_execute_required"
        )
    if approval.confirm_batch_id != manifest.batch_id:
        raise ControlledMultiPoSjReplayError(
            "confirm_batch_id wajib exact", code="apply_confirm_batch_required"
        )
    env = (app_env or "").strip().lower()
    if env == "production":
        if not approval.i_understand_this_writes_production:
            raise ControlledMultiPoSjReplayError(
                "production ack wajib", code="apply_production_ack_required"
            )
        want = _normalize_sha40(approval.expect_live_sha)
        have = _normalize_sha40(live_sha)
        if want is None or have is None or want != have:
            raise ControlledMultiPoSjReplayError(
                "live SHA mismatch", code="apply_live_sha_mismatch"
            )
    elif env not in {"test", "development", "dev"}:
        raise ControlledMultiPoSjReplayError(
            f"APPLY ditolak app_env={app_env!r}", code="apply_env_refused"
        )


def _guard_mongo(db: Database, *, mongo_guard: MongoGuardMode) -> None:
    """Guard target Mongo tanpa mengaktifkan SJ timer flags."""
    import os

    from app.config import get_settings

    settings = get_settings()
    expected_auth = (
        os.environ.get("V4_MONGO_AUTH_SOURCE")
        or os.environ.get("MONGO_AUTH_SOURCE")
        or None
    )
    if mongo_guard == "production":
        assert_safe_mongo_production_sync_target(
            uri=settings.mongodb_uri,
            db_name=db.name,
            app_env=settings.app_env,
            expected_auth_source=expected_auth,
        )
    else:
        assert_safe_mongo_sync_target(
            uri=settings.mongodb_uri,
            db_name=db.name,
            app_env=settings.app_env,
            expected_auth_source=expected_auth,
        )


def _payload_branch_id(payload: Mapping[str, Any]) -> str:
    return normalize_branch_identity(payload.get("branchId")) or as_text(
        payload.get("branchId")
    )


def _is_cancelled_or_void(payload: Mapping[str, Any]) -> bool:
    for key in ("statusName", "status", "approvalStatus"):
        val = as_text(payload.get(key)).lower()
        if any(x in val for x in ("cancel", "void", "batal")):
            return True
    return bool(payload.get("cancelled") or payload.get("voided"))


def _detail_attr_fingerprint(payload: Mapping[str, Any]) -> str:
    items = payload.get("detailItem") or []
    details: list[dict[str, Any]] = []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            so = it.get("salesOrder")
            so_id = as_text(so.get("id")) if isinstance(so, dict) else ""
            so_id = so_id or as_text(it.get("salesOrderId"))
            details.append(
                {
                    "sj_line": as_text(it.get("id")),
                    "so_id": so_id,
                    "detail_id": as_text(it.get("salesOrderDetailId")),
                    "qty": it.get("quantity"),
                }
            )
    return hashlib.sha256(
        json.dumps(details, sort_keys=True, default=str).encode()
    ).hexdigest()


def _side_effect_snapshot(db: Database, accurate_sj_id: str) -> dict[str, Any]:
    note = deliveries.get_delivery_note(db, accurate_sj_id)
    loading_events = 0
    delivery_events = 0
    try:
        loading_events = int(
            db["loading_events"].count_documents({"accurate_sj_id": accurate_sj_id})
        )
        delivery_events = int(
            db["delivery_events"].count_documents({"accurate_sj_id": accurate_sj_id})
        )
    except Exception:  # noqa: BLE001
        pass
    qty_confirmed = 0
    try:
        from app.repositories import task_repository as tasks
        from app.services.delivery_service import loading_sj_task_key

        task = tasks.get_task(db, loading_sj_task_key(accurate_sj_id))
        if task:
            qty_confirmed = int(task.get("qty_confirmed") or 0)
    except Exception:  # noqa: BLE001
        pass
    return {
        "sj_exists": note is not None,
        "is_multi_po": bool(note and deliveries.note_is_multi_po(note)),
        "loading_events": loading_events,
        "delivery_events": delivery_events,
        "qty_confirmed": qty_confirmed,
        "delivered": bool(
            note
            and (
                note.get("delivery_confirmed")
                or as_text(note.get("status")).lower() == "delivered"
            )
        ),
    }


def evaluate_sj_candidate(
    db: Database,
    *,
    manifest: SealedManifest,
    accurate_sj_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    entry = assert_id_in_allowlist(manifest, accurate_sj_id)
    base = {
        "accurate_sj_id": entry.accurate_sj_id,
        "expected_parent_po_ids": list(entry.parent_po_ids),
        "expected_branch_id": entry.branch_id,
    }
    payload_id = as_text(payload.get("id") or payload.get("accurate_sj_id"))
    if payload_id != entry.accurate_sj_id:
        return {
            **base,
            "ok": False,
            "outcome": "conflict",
            "reason": "sj_id_mismatch",
            "stop_batch": True,
        }
    if _is_cancelled_or_void(payload):
        return {
            **base,
            "ok": False,
            "outcome": "ineligible",
            "reason": "cancelled_or_void",
        }
    try:
        note = deliveries.normalize_delivery_note(dict(payload))
    except deliveries.DeliveryNoteIdentityError as exc:
        code = as_text(getattr(exc, "code", None)) or "DELIVERY_NOTE_IDENTITY"
        return {
            **base,
            "ok": False,
            "outcome": "conflict",
            "reason": "identity_mismatch",
            "error_code": code,
            "stop_batch": True,
        }

    if not deliveries.note_is_multi_po(note):
        return {
            **base,
            "ok": False,
            "outcome": "conflict",
            "reason": "expected_multi_po",
            "stop_batch": True,
        }

    actual_parents = tuple(
        sorted(deliveries.note_parent_po_ids(note), key=lambda x: int(x))
    )
    if actual_parents != entry.parent_po_ids:
        return {
            **base,
            "ok": False,
            "outcome": "conflict",
            "reason": "parent_po_mismatch",
            "actual_parent_po_ids": list(actual_parents),
            "stop_batch": True,
        }

    branch = _payload_branch_id(payload)
    if branch and branch != entry.branch_id:
        return {
            **base,
            "ok": False,
            "outcome": "conflict",
            "reason": "branch_mismatch",
            "actual_branch_id": branch,
            "stop_batch": True,
        }

    if entry.detail_attr_sha256:
        fp = _detail_attr_fingerprint(payload)
        if fp != entry.detail_attr_sha256:
            return {
                **base,
                "ok": False,
                "outcome": "conflict",
                "reason": "detail_attr_changed",
                "stop_batch": True,
            }

    missing = [pid for pid in entry.parent_po_ids if get_by_accurate_id(db, pid) is None]
    if missing:
        return {
            **base,
            "ok": False,
            "outcome": "ineligible",
            "reason": "INELIGIBLE_PARENT_NOT_REGISTERED",
            "missing_po_ids": missing,
        }

    for pid in entry.parent_po_ids:
        po = get_by_accurate_id(db, pid)
        assert po is not None
        po_branch = normalize_branch_identity(po.get("branch_id")) or as_text(
            po.get("branch_id")
        )
        if po_branch and po_branch != entry.branch_id:
            return {
                **base,
                "ok": False,
                "outcome": "conflict",
                "reason": "po_branch_mismatch",
                "accurate_po_id": pid,
                "stop_batch": True,
            }

    existing = deliveries.get_delivery_note(db, entry.accurate_sj_id)
    if existing is not None:
        existing_parents = tuple(
            sorted(deliveries.note_parent_po_ids(existing), key=lambda x: int(x))
        )
        if existing_parents != entry.parent_po_ids:
            return {
                **base,
                "ok": False,
                "outcome": "conflict",
                "reason": "existing_sj_parent_conflict",
                "stop_batch": True,
            }
        if not deliveries.note_is_multi_po(existing):
            return {
                **base,
                "ok": False,
                "outcome": "conflict",
                "reason": "existing_sj_not_multi_po",
                "stop_batch": True,
            }
        return {
            **base,
            "ok": True,
            "outcome": "already_present",
            "reason": "sj_already_ingested",
            "normalized": note,
        }

    return {
        **base,
        "ok": True,
        "outcome": "eligible",
        "reason": "ready_multi_po",
        "normalized": note,
    }


def _assert_maintenance_alive(
    db: Database,
    *,
    owner_token: str,
    job_tokens: dict[str, str] | None,
) -> None:
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        refresh_po_repair_maintenance,
        require_maintenance_owner,
    )

    require_maintenance_owner(db, owner_token=owner_token)
    if not refresh_po_repair_maintenance(
        db,
        owner_token=owner_token,
        job_tokens=job_tokens,
        ttl_seconds=DEFAULT_MAINTENANCE_TTL_SECONDS,
    ):
        raise PoRepairMaintenanceError(
            "maintenance_lease_refresh_failed: STOP — lease hilang"
        )


def _ensure_loading_availability(
    db: Database, note: dict[str, Any]
) -> dict[str, Any]:
    from app.services.delivery_service import ensure_loading_sj_task

    before_key = None
    try:
        from app.services.delivery_service import loading_sj_task_key
        from app.repositories import task_repository as tasks

        key = loading_sj_task_key(str(note.get("accurate_sj_id") or ""))
        before = tasks.get_task(db, key)
        before_key = (before or {}).get("task_key")
    except Exception:  # noqa: BLE001
        before_key = None

    task = ensure_loading_sj_task(db, note)
    if task is None:
        return {"outcome": "skipped_or_disabled", "task_key": None}
    key = task.get("task_key")
    if before_key and before_key == key:
        return {"outcome": "already_present", "task_key": key}
    return {"outcome": "ensured", "task_key": key}


def _resolve_review_if_needed(
    db: Database, *, accurate_sj_id: str, disposition: str
) -> str:
    from app.services.accurate_auto_sync.sj_sync_review import (
        STATUS_RESOLVED,
        get_sj_sync_review,
        mark_sj_sync_review_resolved,
    )

    review = get_sj_sync_review(db, accurate_sj_id)
    if review is None:
        return "no_open_review"
    if str(review.get("status") or "") == STATUS_RESOLVED:
        return "already_resolved"
    mark_sj_sync_review_resolved(
        db, accurate_sj_id=accurate_sj_id, disposition=disposition
    )
    after = get_sj_sync_review(db, accurate_sj_id)
    if after is None or str(after.get("status") or "") != STATUS_RESOLVED:
        raise ControlledMultiPoSjReplayError(
            "review resolution tidak terverifikasi",
            code="review_resolve_unverified",
        )
    return "resolved"


def _parents_csv(entry: ManifestEntry) -> str:
    return ",".join(entry.parent_po_ids)


def replay_one_sj(
    db: Database,
    *,
    manifest: SealedManifest,
    accurate_sj_id: str,
    payload: Mapping[str, Any],
    mode: Literal["dry_run", "apply"],
    mongo_guard: MongoGuardMode = "test",
    run_id: str | None = None,
    lease_fp: str | None = None,
    maintenance_owner_token: str | None = None,
    maintenance_job_tokens: dict[str, str] | None = None,
    attempt_number: int = 1,
) -> dict[str, Any]:
    from app.services import controlled_sj_replay_audit as audit

    _guard_mongo(db, mongo_guard=mongo_guard)
    entry = assert_id_in_allowlist(manifest, accurate_sj_id)
    base = {
        "accurate_sj_id": entry.accurate_sj_id,
        "expected_parent_po_ids": list(entry.parent_po_ids),
        "expected_branch_id": entry.branch_id,
    }
    before_snap = _side_effect_snapshot(db, entry.accurate_sj_id)
    check = evaluate_sj_candidate(
        db, manifest=manifest, accurate_sj_id=accurate_sj_id, payload=payload
    )

    if not check.get("ok"):
        out = {
            **base,
            "ok": False,
            "outcome": check.get("outcome") or "failed",
            "reason": check.get("reason"),
            "writes": 0,
            "would_write": False,
            "stop_batch": bool(check.get("stop_batch")),
            "missing_po_ids": check.get("missing_po_ids"),
        }
        if mode == "apply" and run_id:
            audit.mark_item_processing(
                db,
                batch_id=manifest.batch_id,
                run_id=run_id,
                accurate_sj_id=entry.accurate_sj_id,
                expected_so_id=_parents_csv(entry),
                expected_branch_id=entry.branch_id,
                lease_fp=lease_fp,
                attempt_number=attempt_number,
                sj_present_before=bool(before_snap.get("sj_exists")),
            )
            audit.complete_item(
                db,
                run_id=run_id,
                accurate_sj_id=entry.accurate_sj_id,
                outcome=str(out["outcome"]),
                writes=0,
                error_code=str(out.get("reason") or "ineligible"),
                sj_present_after=bool(before_snap.get("sj_exists")),
            )
        return out

    if mode == "dry_run":
        return {
            **base,
            "ok": True,
            "outcome": check.get("outcome") or "eligible",
            "reason": check.get("reason") or "dry_run",
            "dry_run": True,
            "would_write": check.get("outcome") == "eligible",
            "writes": 0,
        }

    if not maintenance_owner_token or not run_id:
        return {
            **base,
            "ok": False,
            "outcome": "failed",
            "reason": "maintenance_required",
            "writes": 0,
            "stop_batch": True,
        }

    try:
        _assert_maintenance_alive(
            db,
            owner_token=maintenance_owner_token,
            job_tokens=maintenance_job_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            **base,
            "ok": False,
            "outcome": "failed",
            "reason": "lease_lost",
            "error_type": type(exc).__name__,
            "writes": 0,
            "stop_batch": True,
        }

    audit.mark_item_processing(
        db,
        batch_id=manifest.batch_id,
        run_id=run_id,
        accurate_sj_id=entry.accurate_sj_id,
        expected_so_id=_parents_csv(entry),
        expected_branch_id=entry.branch_id,
        lease_fp=lease_fp,
        attempt_number=attempt_number,
        sj_present_before=bool(before_snap.get("sj_exists")),
    )

    if check.get("outcome") == "already_present":
        note = deliveries.get_delivery_note(db, entry.accurate_sj_id)
        try:
            review_res = _resolve_review_if_needed(
                db,
                accurate_sj_id=entry.accurate_sj_id,
                disposition="ingested_already_present",
            )
            loading_res = _ensure_loading_availability(db, note or check["normalized"])
            audit.complete_item(
                db,
                run_id=run_id,
                accurate_sj_id=entry.accurate_sj_id,
                outcome="already_present",
                writes=0,
                sj_present_after=True,
                review_resolution=review_res,
                loading_task_outcome=str(loading_res.get("outcome")),
            )
        except Exception as exc:  # noqa: BLE001
            audit.mark_reconciliation_required(
                db,
                run_id=run_id,
                accurate_sj_id=entry.accurate_sj_id,
                error_code="post_present_side_effect_failed",
                sj_present_after=True,
            )
            return {
                **base,
                "ok": False,
                "outcome": "reconciliation_required",
                "reason": "post_present_side_effect_failed",
                "error_type": type(exc).__name__,
                "writes": 0,
                "stop_batch": True,
            }
        return {
            **base,
            "ok": True,
            "outcome": "already_present",
            "reason": "sj_already_ingested",
            "writes": 0,
            "review_resolution": review_res,
            "loading_task_outcome": loading_res.get("outcome"),
        }

    try:
        _assert_maintenance_alive(
            db,
            owner_token=maintenance_owner_token,
            job_tokens=maintenance_job_tokens,
        )
        from app.services.delivery_service import ingest_delivery_note

        result = ingest_delivery_note(db, SYSTEM_USER, dict(payload))
    except Exception as exc:  # noqa: BLE001
        present = deliveries.get_delivery_note(db, entry.accurate_sj_id) is not None
        if present:
            try:
                audit.mark_reconciliation_required(
                    db,
                    run_id=run_id,
                    accurate_sj_id=entry.accurate_sj_id,
                    error_code="ingest_exception_sj_present",
                    sj_present_after=True,
                )
            except Exception:  # noqa: BLE001
                pass
            return {
                **base,
                "ok": False,
                "outcome": "reconciliation_required",
                "reason": "ingest_exception_sj_present",
                "error_type": type(exc).__name__,
                "writes": 0,
                "stop_batch": True,
            }
        audit.complete_item(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            outcome="failed",
            writes=0,
            error_code="ingest_failed",
            sj_present_after=False,
        )
        return {
            **base,
            "ok": False,
            "outcome": "failed",
            "reason": "ingest_failed",
            "error_type": type(exc).__name__,
            "writes": 0,
            "stop_batch": True,
        }

    after = deliveries.get_delivery_note(db, entry.accurate_sj_id)
    after_snap = _side_effect_snapshot(db, entry.accurate_sj_id)
    if after is None:
        audit.complete_item(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            outcome="failed",
            writes=0,
            error_code="write_not_persisted",
            sj_present_after=False,
        )
        return {
            **base,
            "ok": False,
            "outcome": "failed",
            "reason": "write_not_persisted",
            "writes": 0,
            "stop_batch": True,
        }

    persisted_parents = tuple(
        sorted(deliveries.note_parent_po_ids(after), key=lambda x: int(x))
    )
    if persisted_parents != entry.parent_po_ids or not deliveries.note_is_multi_po(after):
        audit.mark_reconciliation_required(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            error_code="persisted_parent_mismatch",
            sj_present_after=True,
            writes=1,
        )
        return {
            **base,
            "ok": False,
            "outcome": "reconciliation_required",
            "reason": "persisted_parent_mismatch",
            "writes": 1,
            "stop_batch": True,
        }

    if after_snap.get("delivered") and not before_snap.get("delivered"):
        audit.mark_reconciliation_required(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            error_code="delivery_confirmation_mutated",
            sj_present_after=True,
            writes=1,
        )
        return {
            **base,
            "ok": False,
            "outcome": "reconciliation_required",
            "reason": "delivery_confirmation_mutated",
            "writes": 1,
            "stop_batch": True,
        }

    if int(after_snap.get("qty_confirmed") or 0) > 0:
        audit.mark_reconciliation_required(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            error_code="loading_qty_confirmed_mutated",
            sj_present_after=True,
            writes=1,
        )
        return {
            **base,
            "ok": False,
            "outcome": "reconciliation_required",
            "reason": "loading_qty_confirmed_mutated",
            "writes": 1,
            "stop_batch": True,
        }

    was_insert = bool(result.get("was_insert"))
    outcome: Outcome = "created" if was_insert else "unchanged"
    disposition = f"ingested_{outcome}"

    try:
        review_res = _resolve_review_if_needed(
            db, accurate_sj_id=entry.accurate_sj_id, disposition=disposition
        )
    except Exception as exc:  # noqa: BLE001
        audit.mark_reconciliation_required(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            error_code="review_resolve_failed",
            sj_present_after=True,
            writes=1 if was_insert else 0,
        )
        return {
            **base,
            "ok": False,
            "outcome": "reconciliation_required",
            "reason": "review_resolve_failed",
            "error_type": type(exc).__name__,
            "writes": 1 if was_insert else 0,
            "stop_batch": True,
        }

    try:
        loading_res = _ensure_loading_availability(db, after)
    except Exception as exc:  # noqa: BLE001
        audit.mark_reconciliation_required(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            error_code="loading_task_failed",
            sj_present_after=True,
            writes=1 if was_insert else 0,
        )
        return {
            **base,
            "ok": False,
            "outcome": "reconciliation_required",
            "reason": "loading_task_failed",
            "error_type": type(exc).__name__,
            "review_resolution": review_res,
            "writes": 1 if was_insert else 0,
            "stop_batch": True,
        }

    writes = 1 if was_insert else 0
    try:
        audit.complete_item(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            outcome=outcome,
            writes=writes,
            sj_present_after=True,
            review_resolution=review_res,
            loading_task_outcome=str(loading_res.get("outcome")),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            **base,
            "ok": False,
            "outcome": "reconciliation_required",
            "reason": "audit_complete_failed",
            "error_type": type(exc).__name__,
            "writes": writes,
            "stop_batch": True,
        }

    return {
        **base,
        "ok": True,
        "outcome": outcome,
        "reason": "ingested",
        "writes": writes,
        "was_insert": was_insert,
        "review_resolution": review_res,
        "loading_task_outcome": loading_res.get("outcome"),
        "side_effects_after": after_snap,
    }


def run_controlled_multi_po_sj_replay(
    db: Database,
    *,
    approval: ReplayApproval,
    client: Any,
    mongo_guard: MongoGuardMode = "test",
    manifest_path: Path | None = None,
    app_env: str = "test",
    live_sha: str | None = None,
    stop_on_unsafe_error: bool = True,
) -> dict[str, Any]:
    from app.services import controlled_sj_replay_audit as audit
    from app.services.accurate_auto_sync.locks import AutoSyncLockError
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        release_po_repair_maintenance,
    )

    manifest = load_sealed_manifest(manifest_path)
    assert_runtime_approval(
        approval, manifest=manifest, app_env=app_env, live_sha=live_sha
    )
    mode: Literal["dry_run", "apply"] = (
        "apply" if approval.mode == "apply" else "dry_run"
    )
    results: list[dict[str, Any]] = []
    stopped = False
    stop_reason: str | None = None
    run_id: str | None = None
    lease_fp: str | None = None
    maintenance: dict[str, Any] | None = None
    fence_epoch: int | None = None
    audit_finalize_verified: bool = mode == "dry_run"
    audit_finalize_error: str | None = None
    reconciliation_hold: bool = False

    def _release_own_maintenance() -> None:
        nonlocal maintenance
        handle = maintenance
        maintenance = None
        if handle is None:
            return
        release_po_repair_maintenance(
            db,
            owner_token=str(handle["owner_token"]),
            job_tokens=dict(handle.get("job_tokens") or {}),
        )

    if mode == "apply":
        try:
            maintenance = acquire_po_repair_maintenance(
                db,
                ttl_seconds=DEFAULT_MAINTENANCE_TTL_SECONDS,
                actor=MAINTENANCE_ACTOR,
            )
        except (PoRepairMaintenanceError, AutoSyncLockError) as exc:
            raise ControlledMultiPoSjReplayError(
                f"exclusive_writer_fence_unavailable: {exc}",
                code="maintenance_acquire_failed",
            ) from exc

    try:
        if mode == "apply":
            assert maintenance is not None
            lease_fp = audit.lease_fingerprint(str(maintenance["owner_token"]))
            fence_epoch = int(maintenance.get("fence_epoch") or 0)
            run_doc = audit.start_run(
                db,
                batch_id=manifest.batch_id,
                manifest_sha256=manifest.manifest_sha256,
                id_list_sha256=manifest.id_list_sha256,
                live_sha=live_sha,
                mode=mode,
                sj_count=manifest.sj_count,
                lease_fp=lease_fp,
                fence_epoch=fence_epoch,
            )
            run_id = str(run_doc["run_id"])

        for entry in manifest.entries:
            try:
                payload = client.fetch_delivery_order_detail(entry.accurate_sj_id)
            except Exception as exc:  # noqa: BLE001
                item = {
                    "accurate_sj_id": entry.accurate_sj_id,
                    "ok": False,
                    "outcome": "failed",
                    "reason": "accurate_fetch_failed",
                    "error_type": type(exc).__name__,
                    "writes": 0,
                    "stop_batch": True,
                }
                results.append(item)
                if stop_on_unsafe_error:
                    stopped = True
                    stop_reason = "accurate_fetch_failed"
                    break
                continue

            if not isinstance(payload, Mapping):
                item = {
                    "accurate_sj_id": entry.accurate_sj_id,
                    "ok": False,
                    "outcome": "failed",
                    "reason": "malformed_payload",
                    "writes": 0,
                    "stop_batch": True,
                }
                results.append(item)
                stopped = True
                stop_reason = "malformed_payload"
                break

            item = replay_one_sj(
                db,
                manifest=manifest,
                accurate_sj_id=entry.accurate_sj_id,
                payload=payload,
                mode=mode,
                mongo_guard=mongo_guard,
                run_id=run_id,
                lease_fp=lease_fp,
                maintenance_owner_token=(
                    str(maintenance["owner_token"]) if maintenance else None
                ),
                maintenance_job_tokens=(
                    dict(maintenance.get("job_tokens") or {}) if maintenance else None
                ),
            )
            results.append(item)
            if item.get("stop_batch") and stop_on_unsafe_error:
                stopped = True
                stop_reason = str(item.get("reason") or "stop_batch")
                break
            if item.get("outcome") == "reconciliation_required":
                reconciliation_hold = True
                stopped = True
                stop_reason = str(item.get("reason") or "reconciliation_required")
                break

        if mode == "apply" and run_id:
            created = sum(1 for r in results if r.get("outcome") == "created")
            already = sum(1 for r in results if r.get("outcome") == "already_present")
            failed = sum(
                1
                for r in results
                if r.get("outcome") in {"failed", "conflict", "reconciliation_required"}
            )
            ineligible = sum(1 for r in results if r.get("outcome") == "ineligible")
            status = "completed"
            if stopped or reconciliation_hold:
                status = "stopped" if stopped else "completed"
            if reconciliation_hold:
                status = "reconciliation_required"
            try:
                audit.finish_run(
                    db,
                    run_id=run_id,
                    status=status,
                    summary={
                        "created": created,
                        "already_present": already,
                        "failed": failed,
                        "ineligible": ineligible,
                        "processed": len(results),
                    },
                    stopped=stopped,
                    stop_reason=stop_reason,
                )
                audit_finalize_verified = True
            except Exception as finish_exc:  # noqa: BLE001
                audit_finalize_verified = False
                audit_finalize_error = type(finish_exc).__name__
                reconciliation_hold = True
                stop_reason = stop_reason or "audit_finish_failed_reconciliation_required"
    finally:
        _release_own_maintenance()

    ok = (
        not stopped
        and not reconciliation_hold
        and audit_finalize_verified
        and audit_finalize_error is None
        and all(
            r.get("ok") or r.get("outcome") in {"already_present", "eligible", "ineligible"}
            for r in results
        )
    )
    # ineligible parents during APPLY of dependent SJ is not overall success
    if any(
        r.get("reason") == "INELIGIBLE_PARENT_NOT_REGISTERED" for r in results
    ) and mode == "apply":
        ok = False

    return {
        "ok": ok and not any(r.get("stop_batch") for r in results),
        "mode": mode,
        "batch_id": manifest.batch_id,
        "manifest_sha256": manifest.manifest_sha256,
        "id_list_sha256": manifest.id_list_sha256,
        "sj_count": manifest.sj_count,
        "run_id": run_id,
        "fence_epoch": fence_epoch,
        "stopped": stopped,
        "stop_reason": stop_reason,
        "reconciliation_hold": reconciliation_hold,
        "audit_finalize_verified": audit_finalize_verified,
        "audit_finalize_error": audit_finalize_error,
        "results": results,
        "writes_total": sum(int(r.get("writes") or 0) for r in results),
    }
