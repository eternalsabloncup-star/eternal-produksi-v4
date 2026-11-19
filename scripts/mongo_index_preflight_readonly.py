#!/usr/bin/env python3
"""Preflight indeks Loading/SJ — READ ONLY. Tidak createIndex / tidak migrasi.

Contoh (Mongo test 27019):
  APP_ENV=test DB_NAME=eternal_v4_test_sj_activation \\
  MONGO_URL='mongodb://…@127.0.0.1:27019/?authSource=…&replicaSet=…' \\
  PYTHONPATH=backend python3 scripts/mongo_index_preflight_readonly.py

Production 27018 hanya dengan --allow-production-readonly (tetap tidak menulis).
27017 ERP legacy: selalu ditolak.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Mongo index preflight")
    parser.add_argument(
        "--allow-production-readonly",
        action="store_true",
        help="Izinkan inspeksi 27018 / DB production tanpa menulis",
    )
    args = parser.parse_args(argv)

    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, get_db
    from app.services.index_preflight import (
        assert_preflight_target_safe,
        inspect_required_indexes,
    )

    clear_settings_cache()
    close_client()
    settings = get_settings()
    try:
        target = assert_preflight_target_safe(
            uri=settings.mongodb_uri,
            db_name=settings.mongodb_db,
            allow_production_readonly=bool(args.allow_production_readonly),
        )
        db = get_db(settings)
        report = inspect_required_indexes(db)
    except PermissionError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "unsafe_preflight_target",
                    "error": str(exc),
                    "writes": False,
                },
                ensure_ascii=False,
            )
        )
        return 2
    finally:
        close_client()
        clear_settings_cache()

    # Jangan cetak URI / credential — hanya host/port/db dari target helper.
    out = {
        "ok": bool(report.get("ok")),
        "writes": False,
        "create_index_executed": False,
        "target": {
            "host": target.get("host"),
            "ports": target.get("ports"),
            "db_name": target.get("db_name"),
        },
        "present": report.get("present"),
        "missing": report.get("missing"),
        "conflict": report.get("conflict"),
        "maintenance_plan": report.get("create_index_commands_for_owner_approval"),
        "note": "Perintah createIndex di maintenance_plan BUTUH persetujuan Owner — tidak dijalankan oleh skrip ini.",
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
