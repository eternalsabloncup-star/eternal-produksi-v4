#!/usr/bin/env python3
"""Owner-gated PO branch_id repair apply — DEFAULT REFUSE / fail-closed.

Wajib untuk production write:
  --execute + --i-understand-this-writes-production
  --expect-live-sha (repo live ada + match)
  sealed manifest + checksum + count
  --backup-meta (archive file ADA + SHA256 cocok + eternal_v4@27018)
  --expect-total-po + --expect-missing-po (wajib, cocok snapshot)
  Mongo maintenance gate (drain writer lease + tahan job lock PO/SJ)

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

from lib.po_branch_repair_apply_loop import (  # noqa: E402
    AuditPersistError,
    persist_audit,
    run_cas_apply_loop,
)
from lib.po_branch_repair_safety import (  # noqa: E402
    DEFAULT_LIVE_REPO,
    assert_no_po_sync_worker,
    assert_snapshot_and_manifest_consistency,
    load_backup_meta,
    load_maintenance_lock,
    load_sealed_candidates,
    prewrite_block_reason,
    require_auto_sync_flags_off,
    require_safe_db_target,
    require_snapshot_args,
    resolve_live_sha,
)


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
        persist_audit(audit_path, base_report)
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
        persist_audit(audit_path, base_report)
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
        persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 4

    try:
        # Env/config gate (fail-closed). Runtime worker/timer = assert_no_po_sync_worker
        # di atas — flag OFF saja bukan bukti proses sudah berhenti.
        require_auto_sync_flags_off(settings)
    except ValueError as exc:
        base_report.update({"refused": True, "reason": str(exc)})
        persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 5

    db = get_db(settings)
    if db.name != "eternal_v4":
        base_report.update({"refused": True, "reason": f"db_handle_mismatch:{db.name}"})
        persist_audit(audit_path, base_report)
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
        persist_audit(audit_path, base_report)
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
                "blocker": "Writer in-flight atau sync lease aktif — STOP tanpa write",
            }
        )
        persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 7
    except Exception as exc:  # noqa: BLE001
        base_report.update(
            {
                "refused": True,
                "reason": f"isolation_unavailable:{type(exc).__name__}:{exc}",
                "blocker": "Tidak dapat membuktikan isolasi writer — STOP tanpa write",
            }
        )
        persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 7

    enriched_base = {
        **base_report,
        "live_sha": live_sha,
        "db_gate": db_gate,
        "backup_sha256": backup.get("archive_sha256"),
        "backup_archive": backup.get("archive_path"),
        "operator_lock_token_fp": str(lock.get("token"))[:8] + "…",
        "maintenance_mongo": True,
        "manifest_sha256": args.expect_manifest_sha256,
        "candidate_count": len(items),
        "snapshot": snap,
        "notes": [
            "Isolasi: writer lease drain + job lock PO/SJ.",
            "Bukan transaksi multi-dokumen. Partial apply ⇒ ok=false.",
            "Backup archive file wajib ada dan SHA256 cocok.",
        ],
    }

    try:
        report = run_cas_apply_loop(
            db,
            items,
            audit_path=audit_path,
            base_report=enriched_base,
            set_branch_fn=set_branch_id_if_empty_and_unaccepted,
            prewrite_fn=prewrite_block_reason,
            refresh_fn=refresh_po_repair_maintenance,
            maint_handle=maint_handle,
        )
    finally:
        if maint_handle is not None:
            release_po_repair_maintenance(
                db,
                owner_token=maint_handle["owner_token"],
                job_tokens=maint_handle.get("job_tokens"),
            )

    print(
        json.dumps(
            {
                "ok": report.get("ok"),
                "audit_out": str(audit_path),
                "filled": report.get("filled"),
                "blocked": report.get("blocked"),
                "already": report.get("already"),
                "skipped": report.get("skipped"),
                "failed": report.get("failed"),
                "stopped": report.get("stopped"),
                "wrote_mongo": report.get("wrote_mongo"),
                "stop_reason": report.get("stop_reason"),
            }
        )
    )
    close_client()
    clear_settings_cache()
    if report.get("audit_persist_error") and not Path(audit_path).is_file():
        return 8
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditPersistError:
        raise SystemExit(8)
