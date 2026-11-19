#!/usr/bin/env bash
# Smoke Accurate LIVE — READ-ONLY, konfigurasi terpisah dari environment TEST.
#
# Tidak mengubah env shell pemanggil. Tidak menulis Accurate / Mongo production.
# Menolak host *.test (placeholder). Tidak mencetak token/signature/db pin.
#
# Cara Owner (pilih salah satu):
#
# A) File env live terpisah (direkomendasikan; jangan commit file ini):
#    cat > /tmp/accurate-live.env <<'EOF'
#    ACCURATE_ACCOUNT_BASE=https://account.accurate.id
#    ACCURATE_API_TOKEN=…
#    ACCURATE_API_SIGNATURE_SECRET=…
#    V4_ACCURATE_DB_ID=…          # pin numerik
#    V4_ACCURATE_REQUIRE_DB_PIN=1
#    ACCURATE_SMOKE_SO_ID=95106    # opsional
#    EOF
#    chmod 600 /tmp/accurate-live.env
#    ./scripts/accurate_live_readonly_smoke.sh /tmp/accurate-live.env
#
# B) Env sudah di-inject ke proses agent (bukan stub V4_*), lalu:
#    ./scripts/accurate_live_readonly_smoke.sh
#
# Precedence Settings (penting vs stub V4_*):
#   host: ACCURATE_BASE_URL > ACCURATE_ACCOUNT_BASE > V4_ACCURATE_BASE_URL > V4_ACCURATE_ACCOUNT_BASE
#   token: ACCURATE_API_TOKEN > V4_ACCURATE_API_TOKEN
#   signature: ACCURATE_API_SIGNATURE_SECRET > ACCURATE_SIGNATURE_SECRET > V4_ACCURATE_API_SIGNATURE_SECRET
#   db pin: V4_ACCURATE_DB_ID > ACCURATE_DB_ID
#
# Stub Cloud Agent (V4_ACCURATE_* = *.test / panjang pendek) akan menimpa
# jika tidak di-unset. Script ini meng-unset stub V4_* di subshell sebelum
# memuat file live / memakai ACCURATE_* yang lebih tinggi precedence.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-}"

# Subshell: jangan bocor ke shell pemanggil; buang stub V4 dulu.
run_smoke() {
  unset V4_ACCURATE_ACCOUNT_BASE V4_ACCURATE_BASE_URL \
        V4_ACCURATE_API_TOKEN V4_ACCURATE_API_SIGNATURE_SECRET \
        V4_ACCURATE_DB_ID V4_ACCURATE_REQUIRE_DB_PIN || true

  if [[ -n "${ENV_FILE}" ]]; then
    if [[ ! -f "${ENV_FILE}" ]]; then
      echo "error: env file tidak ditemukan: ${ENV_FILE}" >&2
      exit 2
    fi
    # shellcheck disable=SC1090
    set -a
    # Hanya baris KEY=VALUE; abaikan komentar.
    # Jangan echo isi file.
    source "${ENV_FILE}"
    set +a
  fi

  host_raw="${ACCURATE_ACCOUNT_BASE:-${ACCURATE_BASE_URL:-${V4_ACCURATE_ACCOUNT_BASE:-${V4_ACCURATE_BASE_URL:-}}}}"
  if [[ -z "${host_raw}" ]]; then
    echo "error: account base belum di-set (ACCURATE_ACCOUNT_BASE / ACCURATE_BASE_URL)." >&2
    echo "Owner: sediakan file env live terpisah — lihat header script." >&2
    exit 3
  fi
  case "${host_raw}" in
    *.test|*.test/*|*://*.test|*://*.test/*|https://account.accurate.test|http://account.accurate.test)
      echo "error: host masih placeholder *.test — tolak smoke live." >&2
      echo "host_scheme_host_only=$(python3 - <<'PY'
from urllib.parse import urlparse
import os
u=os.environ.get("ACCURATE_ACCOUNT_BASE") or os.environ.get("ACCURATE_BASE_URL") or ""
p=urlparse(u)
print(f"{p.scheme}://{p.hostname}")
PY
)" >&2
      exit 4
      ;;
  esac

  # Presence check only — never print values.
  python3 - <<'PY'
import os, sys
from urllib.parse import urlparse
need = [
    ("ACCURATE_ACCOUNT_BASE|ACCURATE_BASE_URL",
     os.environ.get("ACCURATE_ACCOUNT_BASE") or os.environ.get("ACCURATE_BASE_URL")),
    ("ACCURATE_API_TOKEN|V4_ACCURATE_API_TOKEN",
     os.environ.get("ACCURATE_API_TOKEN") or os.environ.get("V4_ACCURATE_API_TOKEN")),
    ("ACCURATE_API_SIGNATURE_SECRET|ACCURATE_SIGNATURE_SECRET|V4_ACCURATE_API_SIGNATURE_SECRET",
     os.environ.get("ACCURATE_API_SIGNATURE_SECRET")
     or os.environ.get("ACCURATE_SIGNATURE_SECRET")
     or os.environ.get("V4_ACCURATE_API_SIGNATURE_SECRET")),
]
missing = [n for n, v in need if not (v and str(v).strip())]
host = urlparse((os.environ.get("ACCURATE_ACCOUNT_BASE") or os.environ.get("ACCURATE_BASE_URL") or "").strip()).hostname
print(f"smoke_mode=read_only")
print(f"account_host={host}")
print(f"token_present={bool(os.environ.get('ACCURATE_API_TOKEN') or os.environ.get('V4_ACCURATE_API_TOKEN'))}")
print(f"signature_present={bool(os.environ.get('ACCURATE_API_SIGNATURE_SECRET') or os.environ.get('ACCURATE_SIGNATURE_SECRET') or os.environ.get('V4_ACCURATE_API_SIGNATURE_SECRET'))}")
print(f"db_pin_present={bool(os.environ.get('V4_ACCURATE_DB_ID') or os.environ.get('ACCURATE_DB_ID'))}")
print(f"require_db_pin={os.environ.get('V4_ACCURATE_REQUIRE_DB_PIN') or os.environ.get('ACCURATE_REQUIRE_DB_PIN') or ''}")
if missing:
    print("missing=" + ",".join(missing), file=sys.stderr)
    sys.exit(5)
if host and host.endswith(".test"):
    print("error: host *.test ditolak", file=sys.stderr)
    sys.exit(4)
PY

  export V4_ACCURATE_REQUIRE_DB_PIN="${V4_ACCURATE_REQUIRE_DB_PIN:-${ACCURATE_REQUIRE_DB_PIN:-1}}"
  cd "${ROOT}/backend"
  exec python3 "${ROOT}/scripts/accurate_live_smoke.py"
}

run_smoke
