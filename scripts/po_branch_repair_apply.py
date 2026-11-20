#!/usr/bin/env python3
"""Owner-gated PO branch_id repair apply — DEFAULT REFUSE / fail-closed.

Wajib untuk production write:
  --execute + --i-understand-this-writes-production
  --expect-live-sha (repo live ada + match)
  sealed manifest + checksum + count
  --backup-meta (archive file ADA + SHA256 cocok + eternal_v4@27018)
  --expect-total-po + --expect-missing-po (wajib, cocok snapshot)
  Mongo maintenance gate (tahan job lock PO/SJ + refuse writer API)

JSON --maintenance-lock hanya attestasi operator; isolasi nyata = Mongo gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
_SCRIPTS = _ROOT / "scripts"
for p in (_BACKEND, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from lib.po_branch_repair_safety import (  # noqa: E402
    DEFAULT_LIVE_REPO,
    assert_no_po_sync_worker,
    assert_snapshot_and_manifest_consistency,
    load_backup_meta,
    load_maintenance_lock,
    load_sealed_candidates,
    prewrite_block_reason,
    require_safe_db_target,
    require_snapshot_args,
    resolve_live_sha,
)


def _persist_audit(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))


def main() -> int:
    p = argparse.ArgumentParser(description="Owner-gated branch_id repair (CAS)")
    p.add_argument("--candidates", required=True, help="Sealed candidates JSON")
    p.add_argument("--expect-live-sha", required=True)
    p.add_argument("--expect-manifest-sha256", required=True)
    p.add_argument("--expect-candidate-count", type=int, required=True)
    p.add_argument("--backup-meta", required=True)
    p.add_argument(
        "--maintenance-lock",
        required=True,
        help="Attestasi operator (bukan pengganti Mongo maintenance gate)",
    )
    p.add_argument("--audit-out", required=True)
    p.add_argument("--live-repo", default=str(DEFAULT_LIVE_REPO))
    p.add_argument(
        "--expect-total-po",
        type=int,
        required=True,
        help="Wajib: total PO production saat apply",
    )
    p.add_argument(
        "--expect-missing-po",
        type=int,
        required=True,
        help="Wajib: jumlah PO tanpa branch_id saat apply",
    )
    p.add_argument(
        "--i-understand-this-writes-production",
        action="store_true",
        help="Wajib untuk mengizinkan write",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Tanpa flag ini: refuse (zero write)",
    )
    args = p.parse_args()

    audit_path = Path(args.audit_out)
    base_report: dict[str, Any] = {
        "ok": False,
        "mode": "OWNER_APPROVED_WRITE",
        "wrote_mongo": False,
        "refused": False,
        "stopped": False,
        "expect_live_sha": args.expect_live_sha,
        "filled": 0,
        "blocked": 0,
        "already": 0,
        "skipped": 0,
        "failed": 0,
        "items": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if not args.i_understand_this_writes_production or not args.execute:
        base_report.update(
            {
                "refused": True,
                "reason": "missing_owner_write_flags",
                "hint": "Butuh --execute dan --i-understand-this-writes-production",
            }
        )
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 2

    maint_handle: dict[str, Any] | None = None
    try:
        expect_total, expect_missing = require_snapshot_args(
            expect_total_po=args.expect_total_po,
            expect_missing_po=args.expect_missing_po,
        )
        live_sha = resolve_live_sha(
            live_repo=Path(args.live_repo), expect_sha=args.expect_live_sha
        )
        backup = load_backup_meta(Path(args.backup_meta))
        lock = load_maintenance_lock(Path(args.maintenance_lock))
        assert_no_po_sync_worker()
        items = load_sealed_candidates(
            Path(args.candidates),
            expect_sha256=args.expect_manifest_sha256,
            expect_count=args.expect_candidate_count,
        )
    except ValueError as exc:
        base_report.update({"refused": True, "reason": str(exc)})
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 3

    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, get_db
    from app.repositories.po_repository import set_branch_id_if_empty_and_unaccepted
    from app.services.po_repair_maintenance import (
        PoRepairMaintenanceError,
        acquire_po_repair_maintenance,
        refresh_po_repair_maintenance,
        release_po_repair_maintenance,
    )

    clear_settings_cache()
    close_client()
    settings = get_settings()
    try:
        db_gate = require_safe_db_target(
            app_env=settings.app_env,
            mongodb_uri=settings.mongodb_uri,
            db_name=settings.mongodb_db,
            require_production=True,
        )
    except ValueError as exc:
        base_report.update({"refused": True, "reason": str(exc)})
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 4

    if getattr(settings, "v4_po_auto_sync_enabled", False):
        base_report.update({"refused": True, "reason": "po_auto_sync_must_be_off"})
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 5

    db = get_db(settings)
    if db.name != "eternal_v4":
        base_report.update({"refused": True, "reason": f"db_handle_mismatch:{db.name}"})
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 4

    try:
        snap = assert_snapshot_and_manifest_consistency(
            db,
            items=items,
            expect_total_po=expect_total,
            expect_missing_po=expect_missing,
        )
    except ValueError as exc:
        base_report.update({"refused": True, "reason": str(exc)})
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 6

    try:
        maint_handle = acquire_po_repair_maintenance(
            db, actor="po_branch_repair_apply", ttl_seconds=3600
        )
    except PoRepairMaintenanceError as exc:
        base_report.update(
            {
                "refused": True,
                "reason": f"maintenance_acquire_failed:{exc}",
                "blocker": "PO/SJ sync lease aktif atau maintenance lain — STOP tanpa write",
            }
        )
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 7
    except Exception as exc:  # noqa: BLE001 — AutoSyncLockError dll
        base_report.update(
            {
                "refused": True,
                "reason": f"isolation_unavailable:{type(exc).__name__}:{exc}",
                "blocker": "Tidak dapat membuktikan isolasi writer — STOP tanpa write",
            }
        )
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 7

    approved_ids = {it["accurate_id"] for it in items}
    audit: list[dict[str, Any]] = []
    filled = blocked = already = skipped = failed = 0
    stopped = False
    stop_reason = None
    wrote_any = False

    try:
        for idx, it in enumerate(items):
            if idx and idx % 25 == 0:
                if not refresh_po_repair_maintenance(
                    db,
                    owner_token=maint_handle["owner_token"],
                    job_tokens=maint_handle.get("job_tokens"),
                ):
                    stopped = True
                    stop_reason = "maintenance_lease_lost"
                    failed += 1
                    audit.append({"accurate_id": it["accurate_id"], "status": "lease_lost"})
                    break

            aid = it["accurate_id"]
            if aid not in approved_ids:
                failed += 1
                audit.append({"accurate_id": aid, "status": "foreign_id_rejected"})
                stopped = True
                stop_reason = "foreign_id"
                break

            block = prewrite_block_reason(
                db,
                accurate_id=aid,
                expected_branch_id=it["expected_branch_id"],
                expected_po_no=it.get("po_no"),
                expected_trans_date=it.get("accurate_trans_date"),
            )
            if block == "already_has_branch":
                # Drift setelah gate snapshot → fail-closed (set kandidat berubah).
                blocked += 1
                audit.append(
                    {
                        "accurate_id": aid,
                        "status": "blocked",
                        "reason": "candidate_branched_after_snapshot",
                    }
                )
                stopped = True
                stop_reason = "manifest_drift_mid_apply"
                break
            if block is not None:
                blocked += 1
                audit.append(
                    {
                        "accurate_id": aid,
                        "status": "blocked",
                        "reason": block,
                        "expected": it["expected_branch_id"],
                    }
                )
                stopped = True
                stop_reason = block
                break

            try:
                filled_doc = set_branch_id_if_empty_and_unaccepted(
                    db, aid, it["expected_branch_id"]
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                audit.append(
                    {
                        "accurate_id": aid,
                        "status": "write_error",
                        "error": type(exc).__name__,
                        "msg": str(exc)[:200],
                    }
                )
                stopped = True
                stop_reason = "write_error"
                break

            if filled_doc is None:
                blocked += 1
                race = prewrite_block_reason(
                    db,
                    accurate_id=aid,
                    expected_branch_id=it["expected_branch_id"],
                    expected_po_no=it.get("po_no"),
                    expected_trans_date=it.get("accurate_trans_date"),
                )
                audit.append(
                    {
                        "accurate_id": aid,
                        "status": "cas_miss_or_blocked",
                        "reason": race or "cas_miss",
                        "expected": it["expected_branch_id"],
                    }
                )
                stopped = True
                stop_reason = race or "cas_miss"
                break

            wrote_any = True
            filled += 1
            audit.append(
                {
                    "accurate_id": aid,
                    "status": "filled",
                    "branch_id": filled_doc.get("branch_id"),
                    "expected": it["expected_branch_id"],
                }
            )

        remaining = len(items) - len(audit)
        if stopped and remaining > 0:
            for it in items[len(audit) :]:
                skipped += 1
                audit.append(
                    {
                        "accurate_id": it["accurate_id"],
                        "status": "skipped_after_stop",
                        "stop_reason": stop_reason,
                    }
                )
    finally:
        if maint_handle is not None:
            release_po_repair_maintenance(
                db,
                owner_token=maint_handle["owner_token"],
                job_tokens=maint_handle.get("job_tokens"),
            )

    ok = (
        not stopped
        and failed == 0
        and blocked == 0
        and filled + already == len(items)
        and skipped == 0
    )
    report = {
        **base_report,
        "ok": ok,
        "wrote_mongo": wrote_any,
        "stopped": stopped,
        "stop_reason": stop_reason,
        "live_sha": live_sha,
        "db_gate": db_gate,
        "backup_sha256": backup.get("archive_sha256"),
        "backup_archive": backup.get("archive_path"),
        "operator_lock_token_fp": str(lock.get("token"))[:8] + "…",
        "maintenance_mongo": True,
        "manifest_sha256": args.expect_manifest_sha256,
        "candidate_count": len(items),
        "snapshot": snap,
        "filled": filled,
        "blocked": blocked,
        "already": already,
        "skipped": skipped,
        "failed": failed,
        "items": audit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Isolasi: Mongo maintenance gate + job lock PO/SJ; writer API refuse.",
            "Bukan transaksi multi-dokumen. Partial apply ⇒ ok=false.",
            "Backup archive file wajib ada dan SHA256 cocok.",
        ],
    }
    _persist_audit(audit_path, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "audit_out": str(audit_path),
                "filled": filled,
                "blocked": blocked,
                "already": already,
                "skipped": skipped,
                "failed": failed,
                "stopped": stopped,
                "wrote_mongo": wrote_any,
            }
        )
    )
    close_client()
    clear_settings_cache()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
