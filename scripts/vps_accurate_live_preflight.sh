#!/usr/bin/env bash
# Preflight Accurate LIVE di VPS Eternal V4 — READ-ONLY.
#
# Kompatibel dengan kode yang sudah ada di main (AccurateReadClient +
# scripts/accurate_live_smoke.py). Tidak bergantung pada file PR #6 yang
# belum di-deploy.
#
# JALANKAN DI VPS (bukan Cloud Agent), dari root checkout eternal-produksi-v4:
#   chmod +x scripts/vps_accurate_live_preflight.sh
#   ./scripts/vps_accurate_live_preflight.sh
#   ./scripts/vps_accurate_live_preflight.sh --smoke-so
#   ./scripts/vps_accurate_live_preflight.sh --smoke-so --probe-do
#
# DILARANG oleh desain:
# - mencetak token / signature / DB pin / isi .env
# - menulis Accurate
# - menulis / sync / ingest Mongo
# - restart service, ubah Nginx, ubah env production
# - memakai variabel V3_* / GATEWAY_M2M_TOKEN

set -euo pipefail

SMOKE_SO=0
PROBE_DO=0
for arg in "$@"; do
  case "$arg" in
    --smoke-so) SMOKE_SO=1 ;;
    --probe-do) PROBE_DO=1 ;;
    -h|--help)
      sed -n '2,25p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== VPS Accurate preflight (read-only) ==="
echo "cwd=$ROOT"
echo "git_head=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "git_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"

# Presence of connector code (no secrets).
python3 - <<'PY'
from pathlib import Path
import sys
root = Path(".").resolve()
checks = {
    "client.py": root / "backend/app/integrations/accurate/client.py",
    "auth.py": root / "backend/app/integrations/accurate/auth.py",
    "accurate_live_smoke.py": root / "scripts/accurate_live_smoke.py",
}
for name, path in checks.items():
    print(f"code_{name.replace('.','_')}={'present' if path.is_file() else 'MISSING'}")
# Refuse V3 leakage into this session
v3 = [k for k in ("V3_ACCURATE_READ_GATEWAY_URL", "V3_ACCURATE_READ_GATEWAY_TOKEN", "GATEWAY_M2M_TOKEN") if __import__("os").environ.get(k)]
print(f"v3_env_present={bool(v3)}")
if v3:
    print("error: V3/gateway env terdeteksi — hentikan (tidak diizinkan).", file=sys.stderr)
    sys.exit(10)
PY

# Load process env only — do NOT cat .env files (may contain secrets).
# If the VPS service injects env into the shell, those win. Owner may also:
#   set -a; source /path/to/v4-live.env; set +a
# before running this script (file must not be committed).
python3 - <<'PY'
import os, sys
from urllib.parse import urlparse

def first(*names: str) -> str:
    for n in names:
        v = os.environ.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""

host_raw = first(
    "ACCURATE_BASE_URL",
    "ACCURATE_ACCOUNT_BASE",
    "V4_ACCURATE_BASE_URL",
    "V4_ACCURATE_ACCOUNT_BASE",
)
host = urlparse(host_raw).hostname if host_raw else ""
token = first("ACCURATE_API_TOKEN", "V4_ACCURATE_API_TOKEN")
sig = first(
    "ACCURATE_API_SIGNATURE_SECRET",
    "ACCURATE_SIGNATURE_SECRET",
    "V4_ACCURATE_API_SIGNATURE_SECRET",
)
pin = first("V4_ACCURATE_DB_ID", "ACCURATE_DB_ID")
req = first("V4_ACCURATE_REQUIRE_DB_PIN", "ACCURATE_REQUIRE_DB_PIN")

print(f"account_host={host or '(none)'}")
print(f"account_host_is_test_tld={bool(host and host.endswith('.test'))}")
print(f"token_present={bool(token)} token_len_bucket={'lt20' if token and len(token)<20 else ('ok' if token else 'missing')}")
print(f"signature_present={bool(sig)} signature_len_bucket={'lt20' if sig and len(sig)<20 else ('ok' if sig else 'missing')}")
print(f"db_pin_present={bool(pin)}")
print(f"require_db_pin={req or '(unset)'}")
print(f"app_env={os.environ.get('APP_ENV', '(unset)')}")

ok = bool(host and token and sig and not (host.endswith(".test")))
print(f"live_config_ready={ok}")
if not host:
    print("blocker=set ACCURATE_ACCOUNT_BASE or ACCURATE_BASE_URL (live host)")
if host and host.endswith(".test"):
    print("blocker=host is placeholder *.test — replace with live Accurate account base")
if not token:
    print("blocker=set ACCURATE_API_TOKEN (or V4_ACCURATE_API_TOKEN)")
if not sig:
    print("blocker=set ACCURATE_API_SIGNATURE_SECRET (or ACCURATE_SIGNATURE_SECRET / V4_*)")
if not pin:
    print("note=V4_ACCURATE_DB_ID missing — set pin + V4_ACCURATE_REQUIRE_DB_PIN=1 for pinned smoke")
sys.exit(0 if ok else 3)
PY
CONFIG_EC=$?

if [[ "$CONFIG_EC" -ne 0 ]]; then
  echo "preflight_config=NOT_READY exit=$CONFIG_EC"
  echo "STOP: konfigurasi Accurate live V4 belum valid di process env VPS."
  exit "$CONFIG_EC"
fi

echo "preflight_config=READY"

if [[ "$SMOKE_SO" -eq 1 ]]; then
  echo "=== Sales Order read-only smoke ==="
  export V4_ACCURATE_REQUIRE_DB_PIN="${V4_ACCURATE_REQUIRE_DB_PIN:-${ACCURATE_REQUIRE_DB_PIN:-1}}"
  PYTHONPATH=backend python3 scripts/accurate_live_smoke.py
  echo "so_smoke=PASS"
fi

if [[ "$PROBE_DO" -eq 1 ]]; then
  echo "=== Delivery Order endpoint probe (read-only) ==="
  PYTHONPATH=backend python3 - <<'PY'
"""Probe kandidat path DO. Tidak menulis. Tidak mencetak payload pelanggan."""
from __future__ import annotations
import json, re, sys
from pathlib import Path
# Impor helper smoke tanpa mengandalkan package scripts.
sys.path.insert(0, str(Path("scripts").resolve()))
from accurate_live_smoke import build_client_from_environ  # type: ignore

client = build_client_from_environ()
print(f"client_status={client.status.value}")

candidates = [
    ("list", "/accurate/api/delivery-order/list.do", {"sp.pageSize": 1, "sp.page": 1}),
    ("detail", "/accurate/api/delivery-order/detail.do", {"id": "0"}),
]

def scrub(msg: str) -> str:
    msg = re.sub(r"Bearer\s+\S+", "Bearer ***", msg)
    msg = re.sub(r"https?://[^\s/]+", "<host>", msg)
    return msg[:200]

proven = []
for kind, path, params in candidates:
    try:
        data = client._request_on_db_host("GET", path, params=params)
        keys = sorted(data.keys()) if isinstance(data, dict) else []
        rows = []
        if kind == "list" and isinstance(data, dict):
            rows = data.get("d") or data.get("data") or data.get("list") or []
            if isinstance(rows, dict):
                rows = rows.get("data") or rows.get("list") or []
        entry = {
            "path": path,
            "ok": True,
            "s": data.get("s") if isinstance(data, dict) else None,
            "top_keys": keys,
            "row_count": len(rows) if isinstance(rows, list) else None,
        }
        if kind == "list" and isinstance(rows, list) and rows and isinstance(rows[0], dict):
            # Field names only — no customer values.
            entry["first_row_field_names"] = sorted(rows[0].keys())
            entry["has_id"] = "id" in rows[0]
            entry["has_number"] = "number" in rows[0]
        print(json.dumps(entry, ensure_ascii=False))
        proven.append(path)
    except Exception as exc:
        print(json.dumps({"path": path, "ok": False, "error": scrub(str(exc))}, ensure_ascii=False))

print(f"do_paths_proven={proven}")
if not proven:
    sys.exit(6)
PY
fi

echo "DONE read-only. No Mongo/Accurate writes performed."
