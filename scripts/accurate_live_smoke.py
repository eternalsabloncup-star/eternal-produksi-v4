#!/usr/bin/env python3
"""Smoke test pembacaan Accurate live — adapter V4 (eksplisit saja).

Hanya GET/POST auth + sales-order/detail.do. Tidak menulis MongoDB.
Tidak menulis Accurate. Tidak menjalankan backend server.
Tidak mengaktifkan sumber Inbox API.

Jalankan eksplisit:
  cd backend && python3 ../scripts/accurate_live_smoke.py
  # atau:
  PYTHONPATH=backend python3 scripts/accurate_live_smoke.py

Env wajib:
  ACCURATE_BASE_URL atau ACCURATE_ACCOUNT_BASE
  ACCURATE_API_TOKEN
  ACCURATE_API_SIGNATURE_SECRET (alias ACCURATE_SIGNATURE_SECRET)

Env pin (utamakan V4, terpisah dari UUID ERP lama):
  V4_ACCURATE_DB_ID — pin numerik d.database.id (disarankan untuk smoke ber-pin)
  ACCURATE_DB_ID — fallback saja

Env opsional lain:
  ACCURATE_HTTP_TIMEOUT_SECONDS — default 20 (cap 60)
  ACCURATE_SMOKE_SO_ID — default 95106
  ACCURATE_REQUIRE_DB_PIN=1 — gagalkan smoke jika pin kosong

Exit 0 jika validasi lulus; non-zero jika gagal.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Pastikan package app ter-import tanpa mengandalkan Mongo Settings.
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _env(*names: str, default: str = "") -> str:
    for name in names:
        val = os.environ.get(name)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


def _timeout_seconds() -> float:
    raw = _env("ACCURATE_HTTP_TIMEOUT_SECONDS", default="20")
    try:
        value = float(raw)
    except ValueError:
        value = 20.0
    # Batasi: jangan timeout tak terbatas.
    return max(5.0, min(value, 60.0))


def build_client_from_environ(*, transport=None):
    """Bangun client dari env runtime — tanpa Settings/Mongo, tanpa file ERP lama."""
    from app.integrations.accurate.client import AccurateReadClient

    account = _env(
        "V4_ACCURATE_ACCOUNT_BASE",
        "V4_ACCURATE_BASE_URL",
        "ACCURATE_ACCOUNT_BASE",
        "ACCURATE_BASE_URL",
    )
    token = _env("V4_ACCURATE_API_TOKEN", "ACCURATE_API_TOKEN")
    secret = _env(
        "V4_ACCURATE_API_SIGNATURE_SECRET",
        "ACCURATE_API_SIGNATURE_SECRET",
        "ACCURATE_SIGNATURE_SECRET",
    )
    # Utamakan V4_ACCURATE_DB_ID agar tidak ikut UUID ACCURATE_DB_ID ERP lama.
    db_id = _env("V4_ACCURATE_DB_ID", "ACCURATE_DB_ID")
    require_pin = _env(
        "V4_ACCURATE_REQUIRE_DB_PIN",
        "ACCURATE_REQUIRE_DB_PIN",
        default="",
    ).lower() in {"1", "true", "yes"}
    return AccurateReadClient(
        account_base_url=account,
        api_token=token,
        signature_secret=secret,
        db_id=db_id,
        timeout_seconds=_timeout_seconds(),
        transport=transport,
        require_db_pin=require_pin,
    )


def run_smoke(*, transport=None) -> int:
    from app.integrations.accurate.auth import AccurateAuthError
    from app.integrations.accurate.client import AccurateReadStatus
    from app.integrations.accurate.smoke_validate import (
        SmokeExpectations,
        format_smoke_report,
        validate_sales_order_smoke,
    )

    so_id = _env("ACCURATE_SMOKE_SO_ID", default="95106")
    expectations = SmokeExpectations(accurate_id=str(so_id))

    client = build_client_from_environ(transport=transport)
    if client.require_db_pin and not client.db_id:
        result = validate_sales_order_smoke(None, auth_ok=False, expectations=expectations)
        result.errors = [
            "V4_ACCURATE_DB_ID wajib (V4_ACCURATE_REQUIRE_DB_PIN=1) — "
            "pin numerik d.database.id, bukan UUID ERP lama"
        ]
        print(format_smoke_report(result))
        print("errors:\n  - auth/http: accurate_database_pin_required")
        return 3
    if client.status != AccurateReadStatus.READY:
        result = validate_sales_order_smoke(None, auth_ok=False, expectations=expectations)
        result.errors = [
            "env Accurate belum lengkap "
            "(V4_ACCURATE_ACCOUNT_BASE/BASE_URL, V4_ACCURATE_API_TOKEN, "
            "V4_ACCURATE_API_SIGNATURE_SECRET)"
        ]
        print(format_smoke_report(result))
        return 2

    payload = None
    auth_ok = False
    try:
        # Satu detail SO saja — open_db + detail (retry dibatasi di client).
        payload = client.fetch_sales_order_detail(so_id)
        auth_ok = True
    except AccurateAuthError as exc:
        print(format_smoke_report(
            validate_sales_order_smoke(None, auth_ok=False, expectations=expectations)
        ))
        print(f"errors:\n  - auth/http: {exc.code}")
        # Diagnostik aman: field / type / kind / length / match — tanpa nilai ID.
        if getattr(exc, "diagnostics", None):
            print("diagnostics:")
            for row in exc.diagnostics:
                parts = [
                    f"field={row.get('field')}",
                    f"type={row.get('type')}",
                    f"kind={row.get('kind')}",
                    f"length={row.get('length')}",
                ]
                if "match" in row:
                    parts.append(f"match={row.get('match')}")
                print(f"  - {', '.join(parts)}")
        return 3
    except TimeoutError:
        print(format_smoke_report(
            validate_sales_order_smoke(None, auth_ok=False, expectations=expectations)
        ))
        print("errors:\n  - timeout Accurate")
        return 4
    except ConnectionError:
        print(format_smoke_report(
            validate_sales_order_smoke(None, auth_ok=False, expectations=expectations)
        ))
        print("errors:\n  - koneksi Accurate gagal")
        return 5
    except Exception as exc:  # noqa: BLE001 — laporkan tipe saja, bukan detail sensitif
        print(format_smoke_report(
            validate_sales_order_smoke(None, auth_ok=False, expectations=expectations)
        ))
        print(f"errors:\n  - {exc.__class__.__name__}")
        return 6

    result = validate_sales_order_smoke(
        payload if isinstance(payload, dict) else None,
        auth_ok=auth_ok,
        expectations=expectations,
    )
    print(format_smoke_report(result))
    return 0 if result.ok else 1


def main() -> None:
    raise SystemExit(run_smoke())


if __name__ == "__main__":
    main()
