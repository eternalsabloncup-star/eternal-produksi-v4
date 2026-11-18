#!/usr/bin/env python3
"""Probe ACCURATE DB pin numerik untuk V4 — eksplisit, read-only.

- Memanggil api-token.do, memilih d.database.id numerik.
- Tidak mencetak nilai ID / token / secret ke stdout.
- Opsional menulis pin ke file 0600 (V4_ACCURATE_DB_PIN_OUT).
- Opsional verifikasi baca SO 95106 dengan pin tersebut.

Jalankan:
  PYTHONPATH=backend python3 scripts/accurate_db_pin_probe.py

Env kredensial (utamakan V4_* agar terpisah dari ERP lama):
  V4_ACCURATE_ACCOUNT_BASE / V4_ACCURATE_BASE_URL
  V4_ACCURATE_API_TOKEN
  V4_ACCURATE_API_SIGNATURE_SECRET

  (fallback: ACCURATE_ACCOUNT_BASE, ACCURATE_API_TOKEN, … — tanpa ACCURATE_DB_ID)

Env probe:
  V4_ACCURATE_DB_PIN_OUT — path file untuk menyimpan pin numerik (mode 0600)
  ACCURATE_SMOKE_SO_ID — default 95106
  ACCURATE_DB_PIN_SKIP_SO_VERIFY=1 — lewati verifikasi SO
"""

from __future__ import annotations

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


def _timeout_seconds() -> float:
    raw = _env("ACCURATE_HTTP_TIMEOUT_SECONDS", default="20")
    try:
        value = float(raw)
    except ValueError:
        value = 20.0
    return max(5.0, min(value, 60.0))


def run(*, transport=None) -> int:
    from app.integrations.accurate.client import AccurateReadClient, AccurateReadStatus
    from app.integrations.accurate.db_pin_probe import run_db_pin_probe
    from app.integrations.accurate.smoke_validate import SmokeExpectations

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
    # Sengaja tanpa ACCURATE_DB_ID — UUID ERP lama tidak dipakai sebagai pin probe.
    client = AccurateReadClient(
        account_base_url=account,
        api_token=token,
        signature_secret=secret,
        db_id="",
        timeout_seconds=_timeout_seconds(),
        transport=transport,
    )
    if client.status != AccurateReadStatus.READY:
        print("probe: FAIL")
        print("code: accurate_credentials_missing")
        print(
            "errors:\n  - set V4_ACCURATE_* account+token+signature "
            "(jangan mengandalkan ACCURATE_DB_ID ERP lama)"
        )
        return 2

    pin_out = _env("V4_ACCURATE_DB_PIN_OUT", "ACCURATE_DB_PIN_OUT")
    skip_so = _env("ACCURATE_DB_PIN_SKIP_SO_VERIFY", default="").lower() in {
        "1",
        "true",
        "yes",
    }
    so_id = _env("ACCURATE_SMOKE_SO_ID", default="95106")
    result = run_db_pin_probe(
        client,
        pin_out_path=pin_out or None,
        verify_sales_order=not skip_so,
        expectations=SmokeExpectations(accurate_id=str(so_id)),
    )
    print(result.format_report())
    if result.ok and result.pin_file_written:
        print(
            "next: set V4_ACCURATE_DB_ID from pin file into V4 env only "
            "(jangan paste nilai ke chat/log); lalu jalankan accurate_live_smoke.py"
        )
    return 0 if result.ok else 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
