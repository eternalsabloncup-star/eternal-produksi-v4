#!/usr/bin/env python3
"""Bootstrap Master loading-sj + grants Owner (M5) — idempotent, targeted.

Contoh:
  PYTHONPATH=backend python3 scripts/bootstrap_loading_sj.py \\
    --expected-db-name eternal_v4_test_m5_loading

Tidak full seed. Tidak createIndex. Tidak request-path.
Production: butuh --allow-production + persetujuan Owner (gunakan activate_loading_sj_release.py).
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
from app.services.loading_sj_bootstrap import (  # noqa: E402
    LoadingSjBootstrapError,
    audit_loading_sj,
    bootstrap_loading_sj_master,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap Master loading-sj (M5).")
    p.add_argument(
        "--expected-db-name",
        required=True,
        help="Nama DB target eksplisit (wajib cocok dengan DB_NAME efektif).",
    )
    p.add_argument(
        "--allow-production",
        action="store_true",
        help="Izinkan DB ops/production (hanya dengan approval Owner).",
    )
    p.add_argument(
        "--audit-only",
        action="store_true",
        help="Hanya cetak audit before — tanpa write.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    clear_settings_cache()
    settings = get_settings()
    db = get_db(settings)
    try:
        if args.audit_only:
            print(json.dumps({"ok": True, "audit": audit_loading_sj(db)}, default=str))
            return 0
        result = bootstrap_loading_sj_master(
            db,
            expected_db_name=args.expected_db_name,
            allow_production=bool(args.allow_production),
        )
        print(json.dumps(result, default=str))
        return 0
    except LoadingSjBootstrapError as exc:
        print(
            json.dumps(
                {"ok": False, "code": exc.code, "error": str(exc)},
                default=str,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        close_client()
        clear_settings_cache()


if __name__ == "__main__":
    raise SystemExit(main())
