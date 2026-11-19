#!/usr/bin/env python3
"""One-shot Accurate auto-sync worker (terpisah dari proses FastAPI).

Contoh (flags default OFF → zero Accurate / zero write):
  PYTHONPATH=backend python3 scripts/accurate_auto_sync_worker.py --job po
  PYTHONPATH=backend python3 scripts/accurate_auto_sync_worker.py --job all

Scheduler eksternal (systemd timer / cron) memanggil skrip ini setiap
V4_ACCURATE_AUTO_SYNC_INTERVAL_SECONDS (default 60). Jangan dijalankan dari
startup uvicorn.

Tidak mengaktifkan production sync. Tidak createIndex. Output tanpa secret.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="One-shot Accurate auto-sync worker.")
    p.add_argument(
        "--job",
        choices=("po", "receipt", "sj", "all"),
        default="all",
        help="Job yang dijalankan sekali (default: all).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, get_db
    from app.services.accurate_auto_sync.runner import run_auto_sync_job

    clear_settings_cache()
    close_client()
    try:
        settings = get_settings()
        db = get_db(settings)
        # Sengaja TIDAK ensure_indexes / seed.
        result = run_auto_sync_job(db, args.job)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "code": getattr(exc, "code", "worker_error"),
                }
            )
        )
        return 2
    finally:
        close_client()
        clear_settings_cache()

    # Jangan cetak URI/token/payload pelanggan.
    safe = result
    if isinstance(result, dict) and "jobs" in result:
        safe = {
            "ok": result.get("ok"),
            "jobs": {
                name: {
                    k: v
                    for k, v in job.items()
                    if k
                    in {
                        "ok",
                        "job",
                        "enabled",
                        "blocked",
                        "accurate_requests",
                        "writes",
                        "counters",
                        "error",
                        "stopped_reason",
                    }
                }
                for name, job in (result.get("jobs") or {}).items()
            },
        }
    else:
        safe = {
            k: v
            for k, v in result.items()
            if k
            in {
                "ok",
                "job",
                "enabled",
                "blocked",
                "accurate_requests",
                "writes",
                "counters",
                "error",
                "stopped_reason",
            }
        }
    print(json.dumps(safe, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
