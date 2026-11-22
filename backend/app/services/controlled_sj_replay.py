"""Controlled SJ Replay — sealed allowlist, DRY_RUN default, APPLY fail-closed.

Kontrak:
- Hanya SJ ID di manifest sealed.
- Tidak menerima arbitrary ID / wildcard / range.
- Tidak mengaktifkan SJ timer atau flag global.
- DRY_RUN: zero Mongo write (termasuk audit).
- APPLY: exclusive po_repair_maintenance + durable audit.
- Ingest via jalur resmi; Loading availability via ensure_loading_sj_task.
- Tidak ada Loading/Delivery confirmation otomatis.
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

BATCH_ID = "controlled_sj_replay_20261122"
DEFAULT_MANIFEST_NAME = "controlled_sj_replay_20261122.json"
APPROVED_ID_LIST_SHA256 = (
    "9ff5c3c4f9c08d32038c106e26f59e1b23692d8f76da054c8f95c8877097d77d"
)
EXCLUDED_QUARANTINE_IDS = frozenset(
    {
        "119068",
        "119155",
        "119721",
        "119950",
        "119979",
        "120806",
        "120851",
    }
)
ALLOWED_BRANCHES = frozenset({"50", "150"})
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
MAINTENANCE_ACTOR = "controlled_sj_replay_apply"
DEFAULT_MAINTENANCE_TTL_SECONDS = 1800

SYSTEM_USER = {
    "email_normalized": "system:controlled-sj-replay",
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


class ControlledSjReplayError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManifestEntry:
    accurate_sj_id: str
    accurate_so_id: str
    branch_id: str


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
        raise ControlledSjReplayError(
            f"manifest tidak ditemukan: {manifest_path}",
            code="manifest_missing",
        )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ControlledSjReplayError("manifest bukan object", code="manifest_invalid")

    expected_meta = compute_manifest_sha256(raw)
    got_meta = str(raw.get("manifest_sha256") or "")
    if got_meta != expected_meta:
        raise ControlledSjReplayError(
            "manifest_sha256 mismatch — tampering/STOP",
            code="manifest_checksum_mismatch",
        )

    batch_id = str(raw.get("batch_id") or "")
    if batch_id != BATCH_ID:
        raise ControlledSjReplayError(
            f"batch_id tidak dikenal: {batch_id}",
            code="manifest_batch_invalid",
        )

    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ControlledSjReplayError("entries kosong", code="manifest_empty")

    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    for row in entries_raw:
        if not isinstance(row, dict):
            raise ControlledSjReplayError(
                "entry bukan object", code="manifest_entry_invalid"
            )
        sj_id = as_text(row.get("accurate_sj_id"))
        so_id = as_text(row.get("accurate_so_id"))
        branch = normalize_branch_identity(row.get("branch_id")) or as_text(
            row.get("branch_id")
        )
        if not sj_id or not so_id or not branch:
            raise ControlledSjReplayError(
                "entry identity tidak lengkap", code="manifest_entry_incomplete"
            )
        if sj_id in EXCLUDED_QUARANTINE_IDS:
            raise ControlledSjReplayError(
                f"excluded quarantine id in manifest: {sj_id}",
                code="manifest_excluded_id",
            )
        if sj_id in seen:
            raise ControlledSjReplayError(
                f"duplicate sj id: {sj_id}", code="manifest_duplicate_id"
            )
        if branch not in ALLOWED_BRANCHES:
            raise ControlledSjReplayError(
                f"branch tidak diizinkan: {branch}", code="manifest_branch_invalid"
            )
        seen.add(sj_id)
        entries.append(
            ManifestEntry(
                accurate_sj_id=sj_id, accurate_so_id=so_id, branch_id=branch
            )
        )

    sj_count = int(raw.get("sj_count") or 0)
    if sj_count != len(entries) or sj_count != 150:
        raise ControlledSjReplayError(
            f"sj_count mismatch: declared={sj_count} actual={len(entries)}",
            code="manifest_count_mismatch",
        )

    id_list = [e.accurate_sj_id for e in entries]
    id_sha = compute_id_list_sha256(id_list)
    declared_id_sha = str(raw.get("id_list_sha256") or "")
    if id_sha != APPROVED_ID_LIST_SHA256 or declared_id_sha != APPROVED_ID_LIST_SHA256:
        raise ControlledSjReplayError(
            "id_list_sha256 tidak cocok dengan checksum P7 yang disetujui",
            code="id_list_checksum_mismatch",
        )

    excl = {str(x) for x in (raw.get("excluded_quarantine_ids") or [])}
    if excl != set(EXCLUDED_QUARANTINE_IDS):
        raise ControlledSjReplayError(
            "excluded_quarantine_ids mismatch", code="excluded_ids_mismatch"
        )
    if seen & EXCLUDED_QUARANTINE_IDS:
        raise ControlledSjReplayError(
            "excluded id muncul di entries", code="manifest_excluded_present"
        )

    return SealedManifest(
        label=str(raw.get("label") or ""),
        version=str(raw.get("version") or ""),
        batch_id=batch_id,
        audit_date=str(raw.get("audit_date") or ""),
        audit_source=str(raw.get("audit_source") or ""),
        sj_count=sj_count,
        id_list_sha256=id_sha,
        manifest_sha256=got_meta,
        entries=tuple(entries),
        path=manifest_path,
    )


def assert_runtime_approval(
    approval: ReplayApproval,
    *,
    manifest: SealedManifest,
    app_env: str,
    live_sha: str | None,
) -> None:
    if approval.expect_batch_id != manifest.batch_id:
        raise ControlledSjReplayError(
            "expect_batch_id mismatch", code="approval_batch_mismatch"
        )
    if approval.expect_sj_count != manifest.sj_count:
        raise ControlledSjReplayError(
            "expect_sj_count mismatch", code="approval_count_mismatch"
        )
    if approval.expect_manifest_sha256 != manifest.manifest_sha256:
        raise ControlledSjReplayError(
            "expect_manifest_sha256 mismatch", code="approval_manifest_sha_mismatch"
        )
    if approval.expect_id_list_sha256 != manifest.id_list_sha256:
        raise ControlledSjReplayError(
            "expect_id_list_sha256 mismatch", code="approval_id_list_sha_mismatch"
        )

    if approval.mode != "apply":
        return

    if not approval.execute:
        raise ControlledSjReplayError(
            "APPLY membutuhkan --execute", code="apply_execute_required"
        )
    if approval.confirm_batch_id != manifest.batch_id:
        raise ControlledSjReplayError(
            "confirm_batch_id wajib exact", code="apply_confirm_batch_required"
        )

    env = (app_env or "").lower()
    if env == "production":
        if not approval.i_understand_this_writes_production:
            raise ControlledSjReplayError(
                "production APPLY membutuhkan acknowledgment",
                code="apply_production_ack_required",
            )
        if not approval.expect_live_sha or not SHA40_RE.match(approval.expect_live_sha):
            raise ControlledSjReplayError(
                "expect_live_sha wajib SHA40", code="apply_live_sha_required"
            )
        if not live_sha or live_sha != approval.expect_live_sha:
            raise ControlledSjReplayError(
                "LIVE SHA mismatch", code="apply_live_sha_mismatch"
            )
    elif env not in {"test", "development", "dev", "local"}:
        raise ControlledSjReplayError(
            f"env ditolak untuk APPLY: {app_env}", code="apply_env_refused"
        )


def _guard_mongo(db: Database, *, mongo_guard: MongoGuardMode) -> None:
    """Guard target Mongo tanpa mengaktifkan / mensyaratkan SJ timer flags.

    Controlled Replay sengaja independen dari V4_SJ_SYNC_ENABLED agar tidak
    memaksa aktivasi sync global.
    """
    import os

    from app.config import get_settings

    settings = get_settings()
    expected_auth = (
        os.environ.get("V4_MONGO_AUTH_SOURCE")
        or os.environ.get("MONGO_AUTH_SOURCE")
        or ""
    ).strip() or None
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


def assert_id_in_allowlist(manifest: SealedManifest, accurate_sj_id: str) -> ManifestEntry:
    entry = manifest.entry(accurate_sj_id)
    if entry is None:
        raise ControlledSjReplayError(
            f"SJ {accurate_sj_id} di luar allowlist sealed.",
            code="not_in_allowlist",
        )
    if accurate_sj_id in EXCLUDED_QUARANTINE_IDS:
        raise ControlledSjReplayError(
            f"SJ {accurate_sj_id} excluded quarantine.",
            code="excluded_quarantine",
        )
    return entry


def _payload_branch_id(payload: Mapping[str, Any]) -> str:
    raw = payload.get("branchId")
    if raw is None and isinstance(payload.get("branch"), dict):
        raw = payload["branch"].get("id")
    return normalize_branch_identity(raw) or as_text(raw)


def _is_cancelled_or_void(payload: Mapping[str, Any]) -> bool:
    for k in ("statusName", "status", "manualStatus"):
        v = payload.get(k)
        if v is None:
            continue
        sv = str(v).lower()
        if "cancel" in sv or "void" in sv:
            return True
    for k in ("cancelled", "canceled", "isCancelled", "isVoid", "void"):
        if payload.get(k) in (True, 1, "1", "true", "True"):
            return True
    return False


def _side_effect_snapshot(db: Database, accurate_sj_id: str) -> dict[str, Any]:
    note = deliveries.get_delivery_note(db, accurate_sj_id)
    loading = deliveries.get_active_loading_event(db, accurate_sj_id)
    delivered = deliveries.has_successful_delivery_event(db, accurate_sj_id)
    return {
        "sj_exists": note is not None,
        "sj_version": (note or {}).get("sj_version"),
        "active_loading": loading is not None,
        "delivered": bool(delivered),
    }


def evaluate_sj_candidate(
    db: Database,
    *,
    manifest: SealedManifest,
    accurate_sj_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validasi identitas sebelum write. Tidak menulis Mongo."""
    entry = assert_id_in_allowlist(manifest, accurate_sj_id)
    base = {
        "accurate_sj_id": entry.accurate_sj_id,
        "expected_so_id": entry.accurate_so_id,
        "expected_branch_id": entry.branch_id,
    }

    if not isinstance(payload, Mapping):
        return {**base, "ok": False, "outcome": "failed", "reason": "malformed_payload"}

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
        if code == "MULTI_SO_NOT_SUPPORTED":
            return {
                **base,
                "ok": False,
                "outcome": "conflict",
                "reason": "multi_so",
                "stop_batch": True,
            }
        return {
            **base,
            "ok": False,
            "outcome": "conflict",
            "reason": "identity_mismatch",
            "error_code": code,
            "stop_batch": True,
        }

    actual_so = as_text(note.get("accurate_po_id"))
    if actual_so != entry.accurate_so_id:
        return {
            **base,
            "ok": False,
            "outcome": "conflict",
            "reason": "so_id_mismatch",
            "actual_so_id": actual_so,
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

    po = get_by_accurate_id(db, entry.accurate_so_id)
    if po is None:
        return {
            **base,
            "ok": False,
            "outcome": "ineligible",
            "reason": "po_still_missing",
        }
    po_branch = normalize_branch_identity(po.get("branch_id")) or as_text(
        po.get("branch_id")
    )
    if po_branch and po_branch != entry.branch_id:
        return {
            **base,
            "ok": False,
            "outcome": "conflict",
            "reason": "po_branch_mismatch",
            "stop_batch": True,
        }

    existing = deliveries.get_delivery_note(db, entry.accurate_sj_id)
    if existing is not None:
        existing_po = as_text(existing.get("accurate_po_id"))
        if existing_po and existing_po != entry.accurate_so_id:
            return {
                **base,
                "ok": False,
                "outcome": "conflict",
                "reason": "existing_sj_so_conflict",
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
        "reason": "ready_single_so",
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
    """Loading task availability only — tidak konfirmasi qty."""
    from app.services.delivery_service import ensure_loading_sj_task

    before_key = None
    try:
        from app.services.delivery_service import loading_sj_task_key

        before = None
        key = loading_sj_task_key(str(note.get("accurate_sj_id") or ""))
        from app.repositories import task_repository as tasks

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
        raise ControlledSjReplayError(
            "review resolution tidak terverifikasi",
            code="review_resolve_unverified",
        )
    return "resolved"


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
        "expected_so_id": entry.accurate_so_id,
        "expected_branch_id": entry.branch_id,
        "manifest_branch_id": entry.branch_id,
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
        }
        if mode == "apply" and run_id:
            audit.mark_item_processing(
                db,
                batch_id=manifest.batch_id,
                run_id=run_id,
                accurate_sj_id=entry.accurate_sj_id,
                expected_so_id=entry.accurate_so_id,
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
        expected_so_id=entry.accurate_so_id,
        expected_branch_id=entry.branch_id,
        lease_fp=lease_fp,
        attempt_number=attempt_number,
        sj_present_before=bool(before_snap.get("sj_exists")),
    )

    # already present — idempotent resolve review + ensure loading
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

    # Official ingest under maintenance (tanpa writer_section — bentrok maintenance).
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

    if as_text(after.get("accurate_po_id")) != entry.accurate_so_id:
        audit.mark_reconciliation_required(
            db,
            run_id=run_id,
            accurate_sj_id=entry.accurate_sj_id,
            error_code="persisted_so_mismatch",
            sj_present_after=True,
            writes=1,
        )
        return {
            **base,
            "ok": False,
            "outcome": "reconciliation_required",
            "reason": "persisted_so_mismatch",
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


def run_controlled_sj_replay(
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
    """Batch replay untuk seluruh allowlist. Tidak menerima arbitrary ID list."""
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

    def _record_apply_item_failure(*, accurate_sj_id: str, entry: ManifestEntry, error_code: str) -> None:
        assert run_id is not None
        audit.mark_item_processing(
            db,
            batch_id=manifest.batch_id,
            run_id=run_id,
            accurate_sj_id=accurate_sj_id,
            expected_so_id=entry.accurate_so_id,
            expected_branch_id=entry.branch_id,
            lease_fp=lease_fp,
            attempt_number=1,
            sj_present_before=False,
        )
        audit.complete_item(
            db,
            run_id=run_id,
            accurate_sj_id=accurate_sj_id,
            outcome="failed",
            writes=0,
            error_code=error_code,
            sj_present_after=False,
        )

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
            raise ControlledSjReplayError(
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
                if mode == "apply" and run_id:
                    try:
                        _record_apply_item_failure(
                            accurate_sj_id=entry.accurate_sj_id,
                            entry=entry,
                            error_code="accurate_fetch_failed",
                        )
                    except Exception as audit_exc:  # noqa: BLE001
                        item["reason"] = "audit_write_failed"
                        item["error_type"] = type(audit_exc).__name__
                        results.append(item)
                        stopped = True
                        stop_reason = "audit_write_failed"
                        break
                results.append(item)
                if stop_on_unsafe_error:
                    stopped = True
                    stop_reason = "accurate_fetch_failed"
                    break
                continue

            item = replay_one_sj(
                db,
                manifest=manifest,
                accurate_sj_id=entry.accurate_sj_id,
                payload=payload if isinstance(payload, Mapping) else {},
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
                stop_reason = str(item.get("reason") or item.get("outcome") or "stopped")
                break

        summary = {
            "eligible": 0,
            "ineligible": 0,
            "already_present": 0,
            "created": 0,
            "unchanged": 0,
            "conflict": 0,
            "failed": 0,
            "reconciliation_required": 0,
            "writes": 0,
        }
        for r in results:
            oc = str(r.get("outcome") or "")
            if oc in summary:
                summary[oc] += 1
            summary["writes"] += int(r.get("writes") or 0)

        status = "completed"
        if stopped:
            status = "stopped"
        if any(r.get("outcome") == "reconciliation_required" for r in results):
            status = "reconciliation_required"

        if mode == "apply" and run_id:
            try:
                finished = audit.finish_run(
                    db,
                    run_id=run_id,
                    status=status,
                    summary=summary,
                    stopped=stopped,
                    stop_reason=stop_reason,
                )
                if finished is None or finished.get("status") != status:
                    raise ControlledSjReplayError(
                        "finish_run tidak terverifikasi",
                        code="audit_finalize_unverified",
                    )
                audit_finalize_verified = True
            except Exception as exc:  # noqa: BLE001
                audit_finalize_verified = False
                audit_finalize_error = type(exc).__name__
                status = "reconciliation_required"

        ok = (
            (not stopped)
            and audit_finalize_verified
            and status == "completed"
            and summary["failed"] == 0
            and summary["reconciliation_required"] == 0
            and summary["conflict"] == 0
        )
        return {
            "ok": ok,
            "mode": mode,
            "batch_id": manifest.batch_id,
            "manifest_sha256": manifest.manifest_sha256,
            "id_list_sha256": manifest.id_list_sha256,
            "processed": len(results),
            "sj_count": manifest.sj_count,
            "run_id": run_id,
            "summary": summary,
            "stopped": stopped,
            "stop_reason": stop_reason,
            "audit_finalize_verified": audit_finalize_verified,
            "audit_finalize_error": audit_finalize_error,
            "status": status,
            "results": results,
            "live_sha": live_sha,
        }
    finally:
        _release_own_maintenance()
