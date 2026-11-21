"""Selective historical PO recovery — allowlist + date-floor exception only.

Kontrak:
- Global ``ACCURATE_SYNC_START_DATE`` tidak berubah.
- Hanya ID di manifest sealed yang boleh melewati date floor.
- Default DRY_RUN (0 writes).
- APPLY production fail-closed tanpa multi-guard Owner (bukan satu env var).
- Tidak terhubung timer / startup / API publik.
- Tidak menerima arbitrary PO ID di luar allowlist.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Mapping

from pymongo.database import Database

from app.integrations.accurate.fields import as_text
from app.repositories.org_master_repository import get_branch, validate_branch_code_format
from app.repositories.po_repository import count_by_accurate_id, get_by_accurate_id
from app.services.accurate_auto_sync.date_floor import (
    configured_date_floor,
    parse_accurate_trans_date,
)
from app.services.branch_identity import normalize_branch_identity
from app.services.po_acceptance import ACCEPTANCE_ACCEPTED, acceptance_block
from app.services.po_sync import MongoGuardMode, sync_sales_order_payload

BATCH_ID = "historical_po_recovery_20261121"
DEFAULT_MANIFEST_NAME = "historical_po_recovery_20261121.json"
ALLOWED_BRANCHES = frozenset({"50", "150"})
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

Outcome = Literal[
    "eligible",
    "ineligible",
    "already_present",
    "created",
    "unchanged",
    "conflict",
    "failed",
]


class HistoricalRecoveryError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManifestEntry:
    accurate_po_id: str
    trans_date: date
    branch_id: str
    sj_count: int


@dataclass(frozen=True)
class SealedManifest:
    label: str
    version: str
    batch_id: str
    audit_date: str
    audit_source: str
    global_date_floor: date
    po_count: int
    entries: tuple[ManifestEntry, ...]
    manifest_sha256: str
    path: Path

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(e.accurate_po_id for e in self.entries)

    def entry(self, accurate_po_id: str) -> ManifestEntry | None:
        aid = str(accurate_po_id)
        for e in self.entries:
            if e.accurate_po_id == aid:
                return e
        return None


@dataclass(frozen=True)
class HistoricalFloorPermit:
    """Izin sempit: satu accurate_id yang sudah lolos allowlist + revalidasi."""

    accurate_id: str
    expected_branch_id: str
    expected_trans_date: date
    manifest_sha256: str
    batch_id: str

    def permits(self, accurate_id: str, payload: Mapping[str, Any]) -> bool:
        if str(accurate_id) != self.accurate_id:
            return False
        payload_id = str(payload.get("id") or payload.get("accurate_id") or "").strip()
        if payload_id != self.accurate_id:
            return False
        parsed = parse_accurate_trans_date(payload.get("transDate"))
        if parsed is None or parsed != self.expected_trans_date:
            return False
        br = normalize_branch_identity(payload.get("branchId"))
        if br != self.expected_branch_id:
            return False
        return True


@dataclass(frozen=True)
class RecoveryApproval:
    """Runtime authorization — bukan string bebas / satu env var umum."""

    mode: Literal["dry_run", "apply"]
    expect_manifest_sha256: str
    expect_po_count: int
    expect_batch_id: str
    # APPLY production multi-guard (semua wajib):
    execute: bool = False
    i_understand_this_writes_production: bool = False
    expect_live_sha: str | None = None
    confirm_batch_id: str | None = None


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "manifests" / DEFAULT_MANIFEST_NAME


def compute_manifest_sha256(body: Mapping[str, Any]) -> str:
    payload = {k: v for k, v in body.items() if k != "manifest_sha256"}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_sealed_manifest(path: Path | None = None) -> SealedManifest:
    manifest_path = path or default_manifest_path()
    if not manifest_path.is_file():
        raise HistoricalRecoveryError(
            f"Manifest hilang: {manifest_path}",
            code="manifest_missing",
        )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HistoricalRecoveryError(
            "Manifest JSON tidak valid.",
            code="manifest_invalid_json",
        ) from exc
    if not isinstance(raw, dict):
        raise HistoricalRecoveryError(
            "Manifest root harus object.",
            code="manifest_invalid",
        )

    claimed = str(raw.get("manifest_sha256") or "").strip().lower()
    actual = compute_manifest_sha256(raw)
    if not claimed or claimed != actual:
        raise HistoricalRecoveryError(
            "Manifest checksum tidak cocok (tamper / corrupt).",
            code="manifest_checksum_mismatch",
        )

    batch_id = str(raw.get("batch_id") or "").strip()
    label = str(raw.get("label") or "").strip()
    if batch_id != BATCH_ID or label != BATCH_ID:
        raise HistoricalRecoveryError(
            "batch_id/label manifest tidak dikenali.",
            code="manifest_batch_mismatch",
        )

    try:
        floor = date.fromisoformat(str(raw.get("global_date_floor") or ""))
    except ValueError as exc:
        raise HistoricalRecoveryError(
            "global_date_floor tidak valid.",
            code="manifest_invalid",
        ) from exc
    if floor != date(2026, 11, 1):
        raise HistoricalRecoveryError(
            "Manifest mengubah global date floor — ditolak.",
            code="manifest_floor_changed",
        )

    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise HistoricalRecoveryError(
            "entries wajib list.",
            code="manifest_invalid",
        )
    po_count = int(raw.get("po_count") or 0)
    if po_count != 80 or len(entries_raw) != 80:
        raise HistoricalRecoveryError(
            "po_count/entries wajib tepat 80.",
            code="manifest_count_mismatch",
        )

    seen: set[str] = set()
    entries: list[ManifestEntry] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            raise HistoricalRecoveryError(
                "entry bukan object.",
                code="manifest_invalid",
            )
        aid = str(item.get("accurate_po_id") or "").strip()
        if not aid or not aid.isdigit():
            raise HistoricalRecoveryError(
                "accurate_po_id tidak valid.",
                code="manifest_invalid_id",
            )
        if aid in seen:
            raise HistoricalRecoveryError(
                f"Duplikat accurate_po_id: {aid}",
                code="manifest_duplicate_id",
            )
        seen.add(aid)
        try:
            td = date.fromisoformat(str(item.get("trans_date") or ""))
        except ValueError as exc:
            raise HistoricalRecoveryError(
                f"trans_date invalid untuk {aid}",
                code="manifest_invalid_date",
            ) from exc
        if td >= floor:
            raise HistoricalRecoveryError(
                f"PO {aid} tidak historis (trans_date >= floor).",
                code="manifest_not_historical",
            )
        br = normalize_branch_identity(item.get("branch_id"))
        if br not in ALLOWED_BRANCHES:
            raise HistoricalRecoveryError(
                f"Cabang luar scope untuk {aid}: {br!r}",
                code="manifest_branch_out_of_scope",
            )
        entries.append(
            ManifestEntry(
                accurate_po_id=aid,
                trans_date=td,
                branch_id=br or "",
                sj_count=int(item.get("sj_count") or 0),
            )
        )

    return SealedManifest(
        label=label,
        version=str(raw.get("version") or ""),
        batch_id=batch_id,
        audit_date=str(raw.get("audit_date") or ""),
        audit_source=str(raw.get("audit_source") or ""),
        global_date_floor=floor,
        po_count=po_count,
        entries=tuple(entries),
        manifest_sha256=claimed,
        path=manifest_path,
    )


def assert_id_in_allowlist(manifest: SealedManifest, accurate_id: str) -> ManifestEntry:
    entry = manifest.entry(accurate_id)
    if entry is None:
        raise HistoricalRecoveryError(
            f"PO {accurate_id} di luar allowlist sealed.",
            code="not_in_allowlist",
        )
    return entry


def issue_floor_permit(
    manifest: SealedManifest, accurate_id: str
) -> HistoricalFloorPermit:
    entry = assert_id_in_allowlist(manifest, accurate_id)
    return HistoricalFloorPermit(
        accurate_id=entry.accurate_po_id,
        expected_branch_id=entry.branch_id,
        expected_trans_date=entry.trans_date,
        manifest_sha256=manifest.manifest_sha256,
        batch_id=manifest.batch_id,
    )


def _normalize_sha40(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not SHA40_RE.fullmatch(text):
        return None
    return text


def assert_runtime_approval(
    approval: RecoveryApproval,
    *,
    manifest: SealedManifest,
    app_env: str,
    live_sha: str | None = None,
) -> None:
    if approval.expect_batch_id != manifest.batch_id:
        raise HistoricalRecoveryError(
            "expect_batch_id tidak cocok.",
            code="approval_batch_mismatch",
        )
    if approval.expect_manifest_sha256.lower() != manifest.manifest_sha256:
        raise HistoricalRecoveryError(
            "expect_manifest_sha256 tidak cocok.",
            code="approval_checksum_mismatch",
        )
    if int(approval.expect_po_count) != manifest.po_count:
        raise HistoricalRecoveryError(
            "expect_po_count tidak cocok.",
            code="approval_count_mismatch",
        )

    if approval.mode == "dry_run":
        return

    if approval.mode != "apply":
        raise HistoricalRecoveryError(
            "mode approval tidak dikenal.",
            code="approval_mode_invalid",
        )

    # APPLY selalu butuh execute + confirm batch — bukan satu env var.
    if not approval.execute:
        raise HistoricalRecoveryError(
            "APPLY ditolak: execute=false (fail-closed).",
            code="apply_execute_required",
        )
    if approval.confirm_batch_id != manifest.batch_id:
        raise HistoricalRecoveryError(
            "APPLY ditolak: confirm_batch_id wajib exact batch sealed.",
            code="apply_confirm_batch_required",
        )

    env = (app_env or "").strip().lower()
    if env == "production":
        if not approval.i_understand_this_writes_production:
            raise HistoricalRecoveryError(
                "APPLY production ditolak: i_understand_this_writes_production wajib.",
                code="apply_production_ack_required",
            )
        want = _normalize_sha40(approval.expect_live_sha)
        have = _normalize_sha40(live_sha)
        if want is None:
            raise HistoricalRecoveryError(
                "APPLY production ditolak: expect_live_sha wajib SHA40.",
                code="apply_live_sha_required",
            )
        if have is None or want != have:
            raise HistoricalRecoveryError(
                "APPLY production ditolak: live SHA tidak cocok.",
                code="apply_live_sha_mismatch",
            )
    elif env not in {"test", "development", "dev"}:
        raise HistoricalRecoveryError(
            f"APPLY ditolak pada app_env={app_env!r}.",
            code="apply_env_refused",
        )


def _require_active_branch(db: Database, branch_id: str) -> str:
    code = validate_branch_code_format(branch_id)
    doc = get_branch(db, code)
    if doc is None or not bool(doc.get("is_active", False)):
        raise HistoricalRecoveryError(
            f"Cabang tidak aktif/tidak dikenal: {code}",
            code="branch_inactive",
        )
    return code


def _payload_identity_ok(payload: Mapping[str, Any]) -> tuple[bool, str | None]:
    number = as_text(payload.get("number") or payload.get("po_no")).strip()
    if not number:
        return False, "missing_po_number"
    items = payload.get("detailItem") or payload.get("items") or []
    if not isinstance(items, list) or len(items) <= 0:
        return False, "no_detail_items"
    for it in items:
        if not isinstance(it, dict):
            return False, "malformed_detail_item"
        lid = it.get("id") or it.get("salesOrderDetailId") or it.get("accurate_line_id")
        if lid is None or str(lid).strip() == "":
            return False, "items_missing_line_id"
    return True, None


def _count_safe(db: Database, collection: str, query: dict[str, Any]) -> int:
    try:
        return int(db[collection].count_documents(query))
    except Exception:  # noqa: BLE001 — collection/index belum ada = 0
        return 0


def _side_effect_snapshot(db: Database, accurate_id: str) -> dict[str, Any]:
    po = get_by_accurate_id(db, accurate_id)
    # acceptance_block(None) → pending; samakan baseline sebelum/sesudah insert.
    acc = acceptance_block(po)
    tasks = _count_safe(
        db,
        "operational_tasks",
        {"$or": [{"accurate_po_id": accurate_id}, {"accurate_id": accurate_id}]},
    )
    loading = _count_safe(db, "loading_events", {"accurate_po_id": accurate_id})
    delivery = _count_safe(db, "delivery_events", {"accurate_po_id": accurate_id})
    stock = 0
    for name in ("stock_movements", "inventory_movements", "stock_ledger"):
        stock += _count_safe(db, name, {"accurate_po_id": accurate_id})
    payments = _count_safe(
        db, "accurate_sales_receipts", {"accurate_po_ids": accurate_id}
    )
    return {
        "acceptance_status": acc.get("status"),
        "po_exists": po is not None,
        "operational_tasks": int(tasks),
        "loading_events": int(loading),
        "delivery_events": int(delivery),
        "stock_movements": int(stock),
        "receipts": int(payments),
        "po_count": count_by_accurate_id(db, accurate_id),
    }


def validate_payload_against_entry(
    db: Database,
    *,
    entry: ManifestEntry,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Revalidasi Accurate payload vs manifest — fail-closed."""
    payload_id = str(payload.get("id") or payload.get("accurate_id") or "").strip()
    if payload_id != entry.accurate_po_id:
        return {
            "ok": False,
            "outcome": "ineligible",
            "reason": "accurate_id_mismatch",
        }
    parsed = parse_accurate_trans_date(payload.get("transDate"))
    if parsed is None:
        return {"ok": False, "outcome": "failed", "reason": "invalid_trans_date"}
    if parsed != entry.trans_date:
        return {
            "ok": False,
            "outcome": "ineligible",
            "reason": "trans_date_mismatch",
            "expected": entry.trans_date.isoformat(),
            "actual": parsed.isoformat(),
        }
    if parsed >= configured_date_floor():
        # Manifest historis; jika Accurate sekarang >= floor, exception tidak perlu —
        # tetapi tetap tolak exception path (bukan mode normal di sini).
        return {
            "ok": False,
            "outcome": "ineligible",
            "reason": "no_longer_before_floor",
        }
    br = normalize_branch_identity(payload.get("branchId"))
    if br != entry.branch_id:
        return {
            "ok": False,
            "outcome": "ineligible",
            "reason": "branch_mismatch",
            "expected": entry.branch_id,
            "actual": br,
        }
    if br not in ALLOWED_BRANCHES:
        return {
            "ok": False,
            "outcome": "ineligible",
            "reason": "out_of_scope_branch",
        }
    try:
        _require_active_branch(db, br or "")
    except HistoricalRecoveryError as exc:
        return {"ok": False, "outcome": "failed", "reason": exc.code}

    ok_id, id_reason = _payload_identity_ok(payload)
    if not ok_id:
        return {"ok": False, "outcome": "ineligible", "reason": id_reason}

    existing = get_by_accurate_id(db, entry.accurate_po_id)
    if existing is not None:
        existing_br = normalize_branch_identity(existing.get("branch_id"))
        if existing_br and existing_br != entry.branch_id:
            return {
                "ok": False,
                "outcome": "conflict",
                "reason": "existing_branch_conflict",
                "existing_branch_id": existing_br,
            }
        acc = acceptance_block(existing)
        if acc.get("status") == ACCEPTANCE_ACCEPTED:
            # Sudah accepted — recovery tidak boleh mengubah; treat as conflict/skip.
            return {
                "ok": False,
                "outcome": "conflict",
                "reason": "already_accepted",
            }
        return {
            "ok": True,
            "outcome": "already_present",
            "reason": "mongo_has_po",
            "existing": True,
        }
    return {"ok": True, "outcome": "eligible", "reason": "ok", "existing": False}


def recover_one_po(
    db: Database,
    *,
    manifest: SealedManifest,
    accurate_id: str,
    payload: Mapping[str, Any],
    mode: Literal["dry_run", "apply"],
    mongo_guard: MongoGuardMode = "test",
) -> dict[str, Any]:
    """Proses satu PO. ``payload`` wajib dari Accurate read resmi (caller)."""
    try:
        entry = assert_id_in_allowlist(manifest, accurate_id)
    except HistoricalRecoveryError as exc:
        return {
            "accurate_po_id": str(accurate_id),
            "ok": False,
            "outcome": "ineligible",
            "reason": exc.code,
            "writes": 0,
        }

    before_snap = _side_effect_snapshot(db, entry.accurate_po_id)
    check = validate_payload_against_entry(db, entry=entry, payload=payload)
    base = {
        "accurate_po_id": entry.accurate_po_id,
        "manifest_branch_id": entry.branch_id,
        "manifest_trans_date": entry.trans_date.isoformat(),
        "sj_count": entry.sj_count,
        "writes": 0,
        "side_effects_before": before_snap,
    }
    if not check.get("ok"):
        return {
            **base,
            "ok": False,
            "outcome": check.get("outcome") or "failed",
            "reason": check.get("reason"),
            "detail": {
                k: v
                for k, v in check.items()
                if k not in {"ok", "outcome", "reason", "existing"}
            },
        }

    if mode == "dry_run":
        return {
            **base,
            "ok": True,
            "outcome": check.get("outcome") or "eligible",
            "reason": check.get("reason") or "dry_run",
            "dry_run": True,
            "would_write": check.get("outcome") == "eligible",
        }

    # APPLY
    if check.get("outcome") == "already_present":
        after_snap = _side_effect_snapshot(db, entry.accurate_po_id)
        return {
            **base,
            "ok": True,
            "outcome": "already_present",
            "reason": "mongo_has_po",
            "writes": 0,
            "side_effects_after": after_snap,
            "side_effects_unchanged": after_snap == before_snap,
        }

    permit = issue_floor_permit(manifest, entry.accurate_po_id)
    if not permit.permits(entry.accurate_po_id, payload):
        return {
            **base,
            "ok": False,
            "outcome": "failed",
            "reason": "floor_permit_rejected",
            "writes": 0,
        }

    try:
        result = sync_sales_order_payload(
            db,
            dict(payload),
            mongo_guard=mongo_guard,
            historical_floor_permit=permit,
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed, jangan klaim sukses
        return {
            **base,
            "ok": False,
            "outcome": "failed",
            "reason": "upsert_failed",
            "error_type": type(exc).__name__,
            "writes": 0,
        }

    writes = int(result.get("writes") or 0)
    outcome = str(result.get("outcome") or "")
    after = get_by_accurate_id(db, entry.accurate_po_id)
    after_snap = _side_effect_snapshot(db, entry.accurate_po_id)

    if writes <= 0 or after is None:
        return {
            **base,
            "ok": False,
            "outcome": "failed",
            "reason": result.get("reason") or outcome or "write_not_persisted",
            "writes": 0,
            "side_effects_after": after_snap,
        }

    # Side-effect invariants (bukan po_exists/po_count — itu memang berubah saat create)
    if after_snap["acceptance_status"] != before_snap["acceptance_status"]:
        return {
            **base,
            "ok": False,
            "outcome": "failed",
            "reason": "acceptance_mutated",
            "writes": writes,
            "side_effects_after": after_snap,
        }
    for key in (
        "operational_tasks",
        "loading_events",
        "delivery_events",
        "stock_movements",
        "receipts",
    ):
        if after_snap[key] != before_snap[key]:
            return {
                **base,
                "ok": False,
                "outcome": "failed",
                "reason": f"side_effect_{key}",
                "writes": writes,
                "side_effects_after": after_snap,
            }
    side_effects_unchanged = all(
        after_snap[k] == before_snap[k]
        for k in (
            "acceptance_status",
            "operational_tasks",
            "loading_events",
            "delivery_events",
            "stock_movements",
            "receipts",
        )
    )

    mapped = "created" if result.get("was_insert") else "unchanged"
    if outcome == "updated" and not result.get("was_insert"):
        mapped = "unchanged"
    if result.get("was_insert"):
        mapped = "created"

    return {
        **base,
        "ok": True,
        "outcome": mapped,
        "reason": "recovered" if mapped == "created" else "idempotent",
        "writes": writes,
        "branch_apply": result.get("branch_apply"),
        "count_after": result.get("count_after"),
        "side_effects_after": after_snap,
        "side_effects_unchanged": side_effects_unchanged,
    }


def run_historical_po_recovery(
    db: Database,
    *,
    approval: RecoveryApproval,
    client: Any,
    mongo_guard: MongoGuardMode = "test",
    manifest_path: Path | None = None,
    app_env: str = "test",
    live_sha: str | None = None,
    stop_on_unsafe_error: bool = True,
) -> dict[str, Any]:
    """Batch recovery untuk seluruh allowlist. Tidak menerima arbitrary ID list."""
    manifest = load_sealed_manifest(manifest_path)
    # Global floor harus tetap 2026-11-01 di runtime.
    if configured_date_floor() != date(2026, 11, 1):
        raise HistoricalRecoveryError(
            "Runtime date floor berubah dari 2026-11-01 — abort.",
            code="runtime_floor_changed",
        )
    assert_runtime_approval(
        approval, manifest=manifest, app_env=app_env, live_sha=live_sha
    )

    mode: Literal["dry_run", "apply"] = (
        "apply" if approval.mode == "apply" else "dry_run"
    )
    results: list[dict[str, Any]] = []
    stopped = False
    stop_reason: str | None = None

    for entry in manifest.entries:
        try:
            payload = client.fetch_sales_order_detail(entry.accurate_po_id)
        except Exception as exc:  # noqa: BLE001
            item = {
                "accurate_po_id": entry.accurate_po_id,
                "ok": False,
                "outcome": "failed",
                "reason": "accurate_fetch_failed",
                "error_type": type(exc).__name__,
                "writes": 0,
            }
            results.append(item)
            if stop_on_unsafe_error:
                stopped = True
                stop_reason = "accurate_fetch_failed"
                break
            continue

        if not isinstance(payload, Mapping):
            results.append(
                {
                    "accurate_po_id": entry.accurate_po_id,
                    "ok": False,
                    "outcome": "failed",
                    "reason": "malformed_payload",
                    "writes": 0,
                }
            )
            if stop_on_unsafe_error:
                stopped = True
                stop_reason = "malformed_payload"
                break
            continue

        item = recover_one_po(
            db,
            manifest=manifest,
            accurate_id=entry.accurate_po_id,
            payload=payload,
            mode=mode,
            mongo_guard=mongo_guard,
        )
        results.append(item)
        if stop_on_unsafe_error and item.get("outcome") == "failed" and not item.get("ok"):
            # Infrastruktur / persist failure → stop batch.
            if item.get("reason") in {
                "upsert_failed",
                "write_not_persisted",
                "acceptance_mutated",
            } or str(item.get("reason") or "").startswith("side_effect_"):
                stopped = True
                stop_reason = str(item.get("reason"))
                break

    summary = {
        "eligible": 0,
        "ineligible": 0,
        "already_present": 0,
        "created": 0,
        "unchanged": 0,
        "conflict": 0,
        "failed": 0,
        "writes": 0,
    }
    for r in results:
        oc = str(r.get("outcome") or "failed")
        if oc in summary:
            summary[oc] += 1
        else:
            summary["failed"] += 1
        summary["writes"] += int(r.get("writes") or 0)

    return {
        "ok": not stopped and summary["failed"] == 0,
        "mode": mode,
        "batch_id": manifest.batch_id,
        "manifest_sha256": manifest.manifest_sha256,
        "manifest_po_count": manifest.po_count,
        "processed": len(results),
        "stopped": stopped,
        "stop_reason": stop_reason,
        "global_date_floor": configured_date_floor().isoformat(),
        "summary": summary,
        "results": results,
        "production_ingest_executed": False
        if mode == "dry_run"
        else app_env.strip().lower() == "production",
    }
