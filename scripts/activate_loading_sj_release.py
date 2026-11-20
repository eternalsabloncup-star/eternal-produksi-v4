#!/usr/bin/env python3
"""Release activation Loading SJ (M5) — satu prosedur otomatis saat deploy disetujui.

Urutan fail-closed:
  SHA40 exact → PR#26 merge commit ancestor → guard Loading/Delivery di tree →
  regression → indexes → backup → isolation → bootstrap → reconcile →
  set V4_LOADING_SJ_TASK_ENABLED=1 → restart backend (wajib) →
  probe /api/health (healthy + features.loading_sj_task_enabled) →
  baru boleh klaim activated=True.

Env proses script BUKAN bukti aktivasi.

Contoh dry-run:
  PYTHONPATH=backend python3 scripts/activate_loading_sj_release.py \\
    --approved-sha <SHA40> \\
    --pr26-merged-ref cff64838d62ef233fd7c02f73b6ca403913d3f26 \\
    --expected-db-name eternal_v4_test_m5 \\
    --env-file /tmp/m5-backend.env --dry-run

Production: Owner approval wajib. Jangan jalankan tanpa restart + probe.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import clear_settings_cache, get_settings  # noqa: E402
from app.db import close_client, get_db  # noqa: E402
from app.services.loading_sj_activation import (  # noqa: E402
    PR26_MERGE_COMMIT_SHA,
    LoadingSjActivationHold,
    activate_loading_sj_release,
    probe_running_backend_http,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Activate Loading SJ release (M5).")
    p.add_argument("--repo", type=Path, default=ROOT, help="Path repo git.")
    p.add_argument(
        "--approved-sha",
        required=True,
        help="Exact approved release SHA40 (40 hex; prefix ditolak).",
    )
    p.add_argument("--expected-db-name", required=True, help="Target DB eksplisit.")
    p.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="Path backend.env yang akan di-upsert V4_LOADING_SJ_TASK_ENABLED=1.",
    )
    p.add_argument(
        "--pr26-merged-ref",
        required=True,
        help=(
            "SHA40 merge commit PR #26 yang wajib ancestor HEAD "
            f"(canonical main: {PR26_MERGE_COMMIT_SHA})."
        ),
    )
    p.add_argument(
        "--backend-base-url",
        default=None,
        help="Base URL backend berjalan untuk probe /api/health (wajib selain dry-run).",
    )
    p.add_argument(
        "--restart-cmd",
        default=None,
        help=(
            "Perintah shell restart backend (wajib selain dry-run). "
            "Contoh: systemctl restart eternal-v4-backend. "
            "JANGAN dijalankan di coding phase tanpa approval."
        ),
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

    restart_callback = None
    running_backend_probe = None
    if not args.dry_run:
        if not args.restart_cmd:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "hold": True,
                        "gate": "restart_required",
                        "error": "--restart-cmd wajib selain dry-run",
                    }
                ),
                file=sys.stderr,
            )
            return 3
        if not args.backend_base_url:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "hold": True,
                        "gate": "running_backend_probe_required",
                        "error": "--backend-base-url wajib selain dry-run",
                    }
                ),
                file=sys.stderr,
            )
            return 3

        restart_cmd = args.restart_cmd

        def restart_callback() -> dict:
            proc = subprocess.run(
                restart_cmd,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "detail": (proc.stderr or proc.stdout or "").strip()[:500],
            }

        base_url = args.backend_base_url

        def running_backend_probe() -> dict:
            return probe_running_backend_http(base_url)

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
            restart_callback=restart_callback,
            running_backend_probe=running_backend_probe,
        )
        print(json.dumps(result, default=str))
        if result.get("hold") or not result.get("ok"):
            return 3
        if not args.dry_run and not result.get("activated"):
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
