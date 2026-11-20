#!/usr/bin/env bash
# Uji terisolasi wrapper flock — tanpa worker production / Accurate / Mongo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WRAP="$ROOT/scripts/vps_accurate_job_flock.sh"
TMP="$(mktemp -d -t eternal-flock-test.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

fake_worker="$TMP/fake_worker.py"
cat >"$fake_worker" <<'PY'
#!/usr/bin/env python3
import json, os, sys, time
job = "unknown"
if "--job" in sys.argv:
    job = sys.argv[sys.argv.index("--job") + 1]
hold = float(os.environ.get("FAKE_WORKER_HOLD", "0"))
if hold > 0:
    time.sleep(hold)
print(json.dumps({"ok": True, "job": job, "enabled": False, "accurate_requests": 0, "writes": 0}))
PY
chmod +x "$fake_worker"

# Stub python path: flock script calls $PY scripts/accurate_auto_sync_worker.py
# Point REPO to TMP with fake layout.
mkdir -p "$TMP/repo/backend/.venv/bin" "$TMP/repo/scripts" "$TMP/lock"
ln -sf "$(command -v python3)" "$TMP/repo/backend/.venv/bin/python"
# Intercept worker by placing a wrapper as accurate_auto_sync_worker.py
cat >"$TMP/repo/scripts/accurate_auto_sync_worker.py" <<PY
#!/usr/bin/env python3
import runpy
runpy.run_path("$fake_worker", run_name="__main__")
PY
chmod +x "$TMP/repo/scripts/accurate_auto_sync_worker.py"

export ETERNAL_V4_REPO="$TMP/repo"
export ETERNAL_V4_FLOCK_DIR="$TMP/lock"
export ETERNAL_V4_FLOCK_WAIT=2
export ETERNAL_V4_FLOCK_BUSY_EXIT=75

echo "1) bash -n"
bash -n "$WRAP"

echo "2) lock tersedia → exit 0"
out="$("$WRAP" po)"
echo "$out" | grep -q '"job": "po"'
echo "   PASS available"

echo "3) lock dipakai → exit 75 (bukan 0)"
# Hold lock in background
(
  flock -w 5 "$TMP/lock/accurate-auto-sync.flock" \
    bash -c 'sleep 5'
) &
holder=$!
# Ensure holder has the lock
sleep 0.3
set +e
"$WRAP" invoice >/tmp/flock-busy.out 2>/tmp/flock-busy.err
rc=$?
set -e
wait "$holder" || true
if [[ "$rc" -ne 75 ]]; then
  echo "FAIL: expected exit 75 on busy lock, got $rc" >&2
  cat /tmp/flock-busy.out /tmp/flock-busy.err >&2 || true
  exit 1
fi
echo "   PASS busy exit=$rc"

echo "4) tidak ada dua worker bersamaan"
# Holder runs fake worker for 3s under flock; second must get 75
export FAKE_WORKER_HOLD=3
(
  "$WRAP" po >/tmp/flock-a.out 2>/tmp/flock-a.err
) &
a=$!
sleep 0.4
set +e
"$WRAP" receipt >/tmp/flock-b.out 2>/tmp/flock-b.err
rc2=$?
set -e
wait "$a"
if [[ "$rc2" -ne 75 ]]; then
  echo "FAIL: concurrent second worker expected 75, got $rc2" >&2
  exit 1
fi
# First should have succeeded
grep -q '"job": "po"' /tmp/flock-a.out
echo "   PASS no concurrent workers (second exit=$rc2)"

echo "5) sj ditolak"
set +e
"$WRAP" sj >/dev/null 2>&1
rc3=$?
set -e
[[ "$rc3" -eq 2 ]]
echo "   PASS sj rejected"

echo "ALL_FLOCK_TESTS_PASS"
