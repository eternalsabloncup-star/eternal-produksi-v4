#!/usr/bin/env bash
# Global flock agar PO/Invoice/Receipt tidak bentrok request Accurate.
# Lock Mongo per-job tetap dipakai di dalam worker.
#
# Kontrak timeout (harus selaras systemd TimeoutStartSec):
#   flock wait  <= 60s
#   worker      = DEFAULT_TIMEOUT_SECONDS 50s (po/invoice/receipt)
#   TimeoutStartSec harus > flock_wait + worker + margin startup/stop
set -euo pipefail

JOB="${1:-}"
case "$JOB" in
  po|invoice|receipt) ;;
  sj)
    echo "SJ production timer dilarang di wrapper ini" >&2
    exit 2
    ;;
  *)
    echo "usage: $0 po|invoice|receipt" >&2
    exit 2
    ;;
esac

REPO="${ETERNAL_V4_REPO:-/home/eternaladmin/eternal-production-v4}"
PY="${ETERNAL_V4_PYTHON:-$REPO/backend/.venv/bin/python}"
LOCK_DIR="${ETERNAL_V4_FLOCK_DIR:-/home/eternaladmin/eternal-v4-run}"
LOCK_FILE="$LOCK_DIR/accurate-auto-sync.flock"
# Pendek agar antrean tidak menumpuk; gagal cepat jika Accurate masih dipakai.
FLOCK_WAIT="${ETERNAL_V4_FLOCK_WAIT:-60}"
# Exit code khusus saat gagal dapat lock (bukan sukses worker).
FLOCK_BUSY_EXIT="${ETERNAL_V4_FLOCK_BUSY_EXIT:-75}"

mkdir -p "$LOCK_DIR"

if ! [[ "$FLOCK_WAIT" =~ ^[0-9]+$ ]] || (( FLOCK_WAIT < 1 || FLOCK_WAIT > 120 )); then
  echo "FLOCK_WAIT tidak valid: $FLOCK_WAIT (1..120)" >&2
  exit 2
fi

# flock -w: keluar nonzero bila lock tidak didapat dalam FLOCK_WAIT detik.
# -E: kode exit konflik/timeout yang jelas (bukan 0).
exec flock -w "$FLOCK_WAIT" -E "$FLOCK_BUSY_EXIT" "$LOCK_FILE" \
  env PYTHONPATH="$REPO/backend" \
  "$PY" "$REPO/scripts/accurate_auto_sync_worker.py" --job "$JOB"
