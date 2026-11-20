#!/usr/bin/env bash
# Global flock khusus job SJ (terpisah dari wrapper po/invoice/receipt).
# Tetap memakai lock Accurate yang sama agar request API tidak bentrok.
#
# Aktivasi production HANYA setelah Owner menyetujui exact SHA + dual-flag:
#   V4_SJ_SYNC_ENABLED=1
#   V4_SJ_SYNC_PRODUCTION_ENABLED=1
set -euo pipefail

JOB="${1:-sj}"
if [[ "$JOB" != "sj" ]]; then
  echo "usage: $0 sj" >&2
  exit 2
fi

REPO="${ETERNAL_V4_REPO:-/home/eternaladmin/eternal-production-v4}"
PY="${ETERNAL_V4_PYTHON:-$REPO/backend/.venv/bin/python}"
LOCK_DIR="${ETERNAL_V4_FLOCK_DIR:-/home/eternaladmin/eternal-v4-run}"
LOCK_FILE="$LOCK_DIR/accurate-auto-sync.flock"
FLOCK_WAIT="${ETERNAL_V4_FLOCK_WAIT:-60}"
FLOCK_BUSY_EXIT="${ETERNAL_V4_FLOCK_BUSY_EXIT:-75}"

mkdir -p "$LOCK_DIR"

if ! [[ "$FLOCK_WAIT" =~ ^[0-9]+$ ]] || (( FLOCK_WAIT < 1 || FLOCK_WAIT > 120 )); then
  echo "FLOCK_WAIT tidak valid: $FLOCK_WAIT (1..120)" >&2
  exit 2
fi

exec flock -w "$FLOCK_WAIT" -E "$FLOCK_BUSY_EXIT" "$LOCK_FILE" \
  env PYTHONPATH="$REPO/backend" \
  "$PY" "$REPO/scripts/accurate_auto_sync_worker.py" --job sj
