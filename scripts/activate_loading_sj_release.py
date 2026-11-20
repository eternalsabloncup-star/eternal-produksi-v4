#!/usr/bin/env python3
"""Release activation Loading SJ (M5) — satu prosedur otomatis saat deploy disetujui.

Urutan fail-closed:
  SHA → PR#26/guard Accepted → regression → indexes → backup → isolation →
  bootstrap loading-sj → verify grants → set V4_LOADING_SJ_TASK_ENABLED=1 →
  (opsional restart) → verify Settings efektif.

Contoh dry-run (coding / preflight):
  PYTHONPATH=backend python3 scripts/activate_loading_sj_release.py \\
    --approved-sha <SHA> --expected-db-name eternal_v4_test_m5 \\
    --env-file /tmp/m5-backend.env --dry-run

Production: Owner approval wajib. Jangan jalankan tanpa PR #26 merged + guard PASS.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import clear_settings_cache, get_settings  # noqa: E402
from app.db import close_client, get_db  # noqa: E402
from app.services.loading_sj_activation import (  # noqa: E402
    LoadingSjActivationHold,
    activate_loading_sj_release,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Activate Loading SJ release (M5).")
    p.add_argument("--repo", type=Path, default=ROOT, help="Path repo git.")
    p.add_argument("--approved-sha", required=True, help="Exact approved release SHA.")
    p.add_argument("--expected-db-name", required=True, help="Target DB eksplisit.")
    p.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="Path backend.env yang akan di-upsert V4_LOADING_SJ_TASK_ENABLED=1.",
    )
    p.add_argument(
        "--pr26-merged-ref",
        default=None,
        help="Opsional: merge commit / ref PR #26 yang harus ancestor HEAD.",
    )
    p.add_argument("--allow-production", action="store_true")
    p.add_argument("--backup-ok", action="store_true")
    p.add_argument("--no-other-deploy", action="store_true")
    p.add_argument("--backend-healthy", action="store_true")
    p.add_argument("--mongo-healthy", action="store_true")
    p.add_argument(
        "--guard-regression-ok",
        action="store_true",
        help="Konfirmasi regression Accepted guard PR #26 sudah PASS.",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--skip-bootstrap",
        action="store_true",
        help="Lewati bootstrap (hanya bila Master sudah diverifikasi).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    clear_settings_cache()
    settings = get_settings()
    db = get_db(settings)
    try:
        result = activate_loading_sj_release(
            repo=args.repo.resolve(),
            db=db,
            approved_sha=args.approved_sha,
            expected_db_name=args.expected_db_name,
            env_file=args.env_file,
            allow_production=bool(args.allow_production),
            pr26_merged_ref=args.pr26_merged_ref,
            backup_ok=bool(args.backup_ok),
            no_other_deploy=bool(args.no_other_deploy),
            backend_healthy=bool(args.backend_healthy),
            mongo_healthy=bool(args.mongo_healthy),
            guard_regression_ok=bool(args.guard_regression_ok),
            dry_run=bool(args.dry_run),
            skip_bootstrap=bool(args.skip_bootstrap),
            restart_callback=None,
            apply_environ_for_verify=not bool(args.dry_run),
        )
        print(json.dumps(result, default=str))
        if result.get("hold") or not result.get("ok"):
            return 3
        return 0
    except LoadingSjActivationHold as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "hold": True,
                    "code": exc.code,
                    "gate": exc.gate,
                    "error": str(exc),
                },
                default=str,
            ),
            file=sys.stderr,
        )
        return 3
    finally:
        close_client()
        clear_settings_cache()


if __name__ == "__main__":
    raise SystemExit(main())
