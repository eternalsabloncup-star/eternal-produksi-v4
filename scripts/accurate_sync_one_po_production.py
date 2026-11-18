#!/usr/bin/env python3
"""Manual sync SATU Sales Order Accurate → Mongo operasional eternal_v4.

Jalur PRODUKSI eksplisit — terpisah dari scripts/accurate_sync_one_po.py (test).

Default: DRY-RUN (0 writes, tidak membuat index).

Syarat target:
  APP_ENV=production
  MONGO host 127.0.0.1|localhost port 27018
  DB_NAME=eternal_v4
  authSource=eternal_v4
  V4_ACCURATE_DB_ID numerik + pin wajib

Contoh dry-run VPS:
  export APP_ENV=production
  export MONGO_URL='mongodb://USER:***@127.0.0.1:27018/eternal_v4?authSource=eternal_v4'
  export DB_NAME=eternal_v4
  export V4_ACCURATE_ACCOUNT_BASE=…
  export V4_ACCURATE_API_TOKEN=…
  export V4_ACCURATE_API_SIGNATURE_SECRET=…
  export V4_ACCURATE_DB_ID=…   # pin numerik
  export V4_ACCURATE_REQUIRE_DB_PIN=1
  PYTHONPATH=backend python3 scripts/accurate_sync_one_po_production.py 95106

Apply (Owner):
  PYTHONPATH=backend python3 scripts/accurate_sync_one_po_production.py 95106 \\
    --apply --confirm-po-id 95106

Tidak ada scheduler/bulk. Tidak menulis Accurate. Tidak menyentuh port 27017.
Output aman: tanpa token, password, URI, atau payload pelanggan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Production sync satu PO Accurate → eternal_v4 (manual)."
    )
    p.add_argument(
        "accurate_id",
        nargs="?",
        default=os.environ.get("ACCURATE_SMOKE_SO_ID", "95106"),
        help="Accurate sales order id (satu PO per eksekusi).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Tulis Mongo (default: dry-run).",
    )
    p.add_argument(
        "--confirm-po-id",
        default=None,
        help="Wajib sama dengan accurate_id saat --apply.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dry_run = not args.apply

    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, get_db
    from app.db_guard import UnsafeMongoTargetError
    from app.integrations.accurate.auth import AccurateAuthError
    from app.services.po_sync_production import (
        ProductionSyncError,
        run_production_sync_one_po,
    )

    clear_settings_cache()
    close_client()
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "code": "settings_error", "error": type(exc).__name__}
            )
        )
        return 2

    try:
        db = get_db(settings)
        # Sengaja TIDAK memanggil ensure_indexes — no auto-migration.
        result = run_production_sync_one_po(
            db,
            args.accurate_id,
            dry_run=dry_run,
            confirm_po_id=args.confirm_po_id,
            settings=settings,
        )
    except UnsafeMongoTargetError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "unsafe_mongo_target"}))
        return 3
    except ProductionSyncError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "production_sync"}))
        return 4
    except AccurateAuthError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "accurate_auth"}))
        return 5
    except TimeoutError:
        print(json.dumps({"ok": False, "code": "timeout", "error": "accurate_timeout"}))
        return 6
    except ConnectionError as exc:
        print(
            json.dumps(
                {"ok": False, "code": "connection_error", "error": exc.__class__.__name__}
            )
        )
        return 7
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "code": "error", "error": type(exc).__name__}))
        return 8
    finally:
        close_client()
        clear_settings_cache()

    # Jangan cetak URI / token / pelanggan.
    safe = {k: v for k, v in result.items() if k not in {"document", "po", "payload"}}
    print(json.dumps(safe, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
