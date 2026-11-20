#!/usr/bin/env python3
"""Owner-gated PO branch_id repair apply — DEFAULT REFUSE / fail-closed.

Wajib:
  --execute
  --i-understand-this-writes-production
  --expect-live-sha (full 40-hex) + live repo exists & matches
  --expect-manifest-sha256 + sealed candidates
  --expect-candidate-count
  --backup-meta (fresh eternal_v4@27018)
  --maintenance-lock (workers cleared, PO sync off)
  APP_ENV=production, DB eternal_v4, Mongo :27018

Setiap item: pre-check acceptance/deps/identity drift → CAS atomik
``set_branch_id_if_empty_and_unaccepted``. Race/dep → STOP sisa write.
ok=False bila ada gagal/blocked/partial. Audit selalu dipersist.
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
    load_backup_meta,
    load_maintenance_lock,
    load_sealed_candidates,
    prewrite_block_reason,
    require_safe_db_target,
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
    p.add_argument("--maintenance-lock", required=True)
    p.add_argument("--audit-out", required=True)
    p.add_argument("--live-repo", default=str(DEFAULT_LIVE_REPO))
    p.add_argument("--expect-total-po", type=int, default=0)
    p.add_argument("--expect-missing-po", type=int, default=0)
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

    try:
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

    total = db["accurate_sales_orders"].count_documents({})
    missing = db["accurate_sales_orders"].count_documents(
        {"$or": [{"branch_id": {"$exists": False}}, {"branch_id": None}, {"branch_id": ""}]}
    )
    if args.expect_total_po and total != args.expect_total_po:
        base_report.update(
            {
                "refused": True,
                "reason": f"snapshot_total_drift:{total}!={args.expect_total_po}",
            }
        )
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 6
    if args.expect_missing_po and missing != args.expect_missing_po:
        base_report.update(
            {
                "refused": True,
                "reason": f"snapshot_missing_drift:{missing}!={args.expect_missing_po}",
            }
        )
        _persist_audit(audit_path, base_report)
        print(json.dumps(base_report))
        return 6

    approved_ids = {it["accurate_id"] for it in items}
    audit: list[dict[str, Any]] = []
    filled = blocked = already = skipped = failed = 0
    stopped = False
    stop_reason = None
    wrote_any = False

    for it in items:
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
            already += 1
            audit.append({"accurate_id": aid, "status": "already_has_branch"})
            continue
        if block is not None:
            # Fail-closed: accepted/deps/drift → stop remaining writes.
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
            # Race: accepted/deps/branch filled by other writer.
            blocked += 1
            # Re-check why
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
        "maintenance_token_fp": str(lock.get("token"))[:8] + "…",
        "manifest_sha256": args.expect_manifest_sha256,
        "candidate_count": len(items),
        "snapshot": {"total": total, "missing": missing},
        "filled": filled,
        "blocked": blocked,
        "already": already,
        "skipped": skipped,
        "failed": failed,
        "items": audit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Cross-collection dep check is fail-closed + maintenance isolation; not a multi-doc transaction.",
            "Partial apply ⇒ ok=false; remaining items skipped_after_stop.",
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
