#!/usr/bin/env bash
# Global flock agar PO/Invoice/Receipt tidak bentrok request Accurate.
# Lock Mongo per-job tetap dipakai di dalam worker.
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

REPO=/home/eternaladmin/eternal-production-v4
PY="$REPO/backend/.venv/bin/python"
LOCK_DIR=/home/eternaladmin/eternal-v4-run
LOCK_FILE="$LOCK_DIR/accurate-auto-sync.flock"
mkdir -p "$LOCK_DIR"

# Tunggu hingga 240s jika job lain masih memegang Accurate.
exec flock -w 240 "$LOCK_FILE" \
  env PYTHONPATH="$REPO/backend" \
  "$PY" "$REPO/scripts/accurate_auto_sync_worker.py" --job "$JOB"
