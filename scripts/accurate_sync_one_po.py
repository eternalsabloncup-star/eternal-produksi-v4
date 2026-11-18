#!/usr/bin/env python3
"""Manual sync satu Sales Order Accurate → MongoDB V4 test (eksplisit).

Tidak menjalankan scheduler. Tidak menulis Accurate. Tidak menyentuh ERP 27017.
Inbox DEMO tidak diubah.

Contoh (authSource = DB tempat user test dibuat — bukan user ops eternal_v4):
  # Set MONGO_URL di shell dengan authSource=eternal_v4_test_manual (password tidak dicatat di sini)
  export DB_NAME='eternal_v4_test_manual'
  export APP_ENV=development
  export V4_ACCURATE_ACCOUNT_BASE=…
  export V4_ACCURATE_API_TOKEN=…
  export V4_ACCURATE_API_SIGNATURE_SECRET=…
  export V4_ACCURATE_DB_ID=…          # pin numerik
  export V4_ACCURATE_REQUIRE_DB_PIN=1
  PYTHONPATH=backend python3 scripts/accurate_sync_one_po.py 95106

Output aman: po_count, line_count, inserted/updated, idempotent — tanpa token/pin/pelanggan.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _env(*names: str, default: str = "") -> str:
    for name in names:
        val = os.environ.get(name)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


def build_accurate_client(*, transport=None):
    from app.integrations.accurate.client import AccurateReadClient

    require_pin = _env(
        "V4_ACCURATE_REQUIRE_DB_PIN", "ACCURATE_REQUIRE_DB_PIN", default="1"
    ).lower() in {"1", "true", "yes"}
    return AccurateReadClient(
        account_base_url=_env(
            "V4_ACCURATE_ACCOUNT_BASE",
            "V4_ACCURATE_BASE_URL",
            "ACCURATE_ACCOUNT_BASE",
            "ACCURATE_BASE_URL",
        ),
        api_token=_env("V4_ACCURATE_API_TOKEN", "ACCURATE_API_TOKEN"),
        signature_secret=_env(
            "V4_ACCURATE_API_SIGNATURE_SECRET",
            "ACCURATE_API_SIGNATURE_SECRET",
            "ACCURATE_SIGNATURE_SECRET",
        ),
        db_id=_env("V4_ACCURATE_DB_ID", "ACCURATE_DB_ID"),
        timeout_seconds=float(
            _env("ACCURATE_HTTP_TIMEOUT_SECONDS", default="20") or "20"
        ),
        transport=transport,
        require_db_pin=require_pin,
    )


def run(accurate_id: str, *, transport=None) -> int:
    from app.config import clear_settings_cache, get_settings
    from app.db import close_client, ensure_indexes, get_db
    from app.db_guard import UnsafeMongoTargetError
    from app.integrations.accurate.auth import AccurateAuthError
    from app.services.po_sync import sync_one_sales_order_from_accurate

    # Pastikan settings membaca env terkini (tanpa cache basi).
    clear_settings_cache()
    close_client()
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "code": "settings_error", "error": type(exc).__name__}))
        return 2

    try:
        db = get_db(settings)
        ensure_indexes(db)
        client = build_accurate_client(transport=transport)
        result = sync_one_sales_order_from_accurate(
            db, accurate_id, client=client
        )
    except UnsafeMongoTargetError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "unsafe_mongo_target"}))
        return 3
    except AccurateAuthError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": "accurate_auth"}))
        return 4
    except TimeoutError:
        print(json.dumps({"ok": False, "code": "timeout", "error": "accurate_timeout"}))
        return 5
    except ConnectionError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "connection_error",
                    "error": exc.__class__.__name__,
                }
            )
        )
        return 6
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "code": "error", "error": exc.__class__.__name__}))
        return 7
    finally:
        close_client()
        clear_settings_cache()

    print(
        json.dumps(
            {
                "ok": True,
                "accurate_id": result["accurate_id"],
                "po_no": result["po_no"],
                "po_count": result.get("po_count", result["count_after"]),
                "line_count": result["line_count"],
                "inserted": result["was_insert"],
                "updated": result.get("was_updated", not result["was_insert"]),
                "idempotent": result.get(
                    "idempotent",
                    bool(result["duplicate_prevented"] and result["count_after"] == 1),
                ),
                "duplicate_prevented": result["duplicate_prevented"],
                "line_diff": result["line_diff"],
                "process_kinds": result["process_kinds"],
                "operational_preserved": result["operational_preserved"],
                "db_name": settings.mongodb_db,
                "mongo_guard": "passed",
                "mongo_auth_source_ok": True,
            },
            default=str,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> None:
    args = list(argv if argv is not None else sys.argv[1:])
    accurate_id = args[0] if args else _env("ACCURATE_SMOKE_SO_ID", default="95106")
    raise SystemExit(run(str(accurate_id)))


if __name__ == "__main__":
    main()
