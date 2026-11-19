#!/usr/bin/env bash
# Owner wrapper: root HANYA stage kredensial + repair ownership worktree admin dir.
# Semua operasi git (fetch/worktree/checkout) berjalan sebagai eternaladmin — tidak pernah root.
# Tidak mengubah checkout branch main production. Tidak chown -R seluruh repo production.
#
# Satu perintah:
#   cd /home/eternaladmin/eternal-production-v4 && git fetch origin cursor/v4-tahap01-po-fields-finalize-0ea3 \
#     && git show origin/cursor/v4-tahap01-po-fields-finalize-0ea3:scripts/vps_sj_oneshot_exec.sh > /tmp/vps_sj_oneshot_exec.sh \
#     && chmod 700 /tmp/vps_sj_oneshot_exec.sh && sudo bash /tmp/vps_sj_oneshot_exec.sh

set -euo pipefail
umask 077

if [[ "$(id -u)" -ne 0 ]]; then
  echo 'FAIL: jalankan dengan sudo (root hanya untuk stage kredensial + ownership repair)' >&2
  exit 1
fi

REPO="${ETERNAL_V4_REPO:-/home/eternaladmin/eternal-production-v4}"
BRANCH="${ETERNAL_V4_PR6_BRANCH:-cursor/v4-tahap01-po-fields-finalize-0ea3}"
PIN_SHA="${ETERNAL_V4_PR6_SHA:-}"
WT="${ETERNAL_V4_SJ_WT:-/tmp/eternal-v4-pr6-sj-oneshot}"
CRED="${ETERNAL_V4_CRED_DIR:-/tmp/eternal-v4-sj-oneshot-cred}"
RUN_USER="${ETERNAL_V4_RUN_USER:-eternaladmin}"
PY_BIN="${ETERNAL_V4_PYTHON:-$REPO/backend/.venv/bin/python}"
SRC_BACKEND_ENV="${ETERNAL_V4_BACKEND_ENV_SRC:-/etc/eternal-v4/backend/backend.env}"
SRC_MONGO_PW="${ETERNAL_V4_TEST_MONGO_PW_SRC:-/etc/eternal-v4/test-mongo-app.password}"

[[ -d "$REPO/.git" ]] || { echo "FAIL: repo missing $REPO" >&2; exit 1; }
[[ -x "$PY_BIN" ]] || { echo "FAIL: python missing $PY_BIN" >&2; exit 1; }
[[ -f "$SRC_BACKEND_ENV" ]] || { echo "FAIL: backend.env missing" >&2; exit 1; }
[[ -f "$SRC_MONGO_PW" ]] || { echo "FAIL: mongo password file missing" >&2; exit 1; }
id -u "$RUN_USER" >/dev/null 2>&1 || { echo "FAIL: user $RUN_USER missing" >&2; exit 1; }

as_user() {
  sudo -u "$RUN_USER" env -i \
    HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)" \
    USER="$RUN_USER" \
    LOGNAME="$RUN_USER" \
    LANG=C.UTF-8 \
    PATH="/usr/bin:/bin" \
    "$@"
}

PROD_HEAD_BEFORE="$(as_user git -C "$REPO" rev-parse HEAD)"
PROD_BRANCH_BEFORE="$(as_user git -C "$REPO" branch --show-current || true)"

# Fetch tip sebagai RUN_USER (bukan root) — hindari metadata .git milik root
as_user git -C "$REPO" fetch origin "$BRANCH" --quiet
TIP="$(as_user git -C "$REPO" rev-parse "origin/$BRANCH")"
if [[ -z "$PIN_SHA" ]]; then
  PIN_SHA="$TIP"
fi
if [[ "$PIN_SHA" != "$TIP" ]]; then
  echo "FAIL: PIN_SHA $PIN_SHA != origin/$BRANCH tip $TIP" >&2
  exit 1
fi

# --- Root: stage kredensial terbatas (bukan git) ---
rm -rf "$CRED"
mkdir -m 700 "$CRED"
grep -Ev '^(MONGO_URL|MONGODB_URI|MONGODB_URL|DB_NAME|MONGODB_DB|V4_MONGO_AUTH_SOURCE|MONGO_AUTH_SOURCE)=' \
  "$SRC_BACKEND_ENV" > "$CRED/accurate.env"
cp "$SRC_MONGO_PW" "$CRED/mongo.password"
chmod 600 "$CRED/accurate.env" "$CRED/mongo.password"
chown -R "$RUN_USER:$RUN_USER" "$CRED"

# --- Root: repair ownership HANYA WT + admin gitdir worktrees/* ---
LIB_TMP="$(mktemp -t oneshot-wt-lib.XXXXXX)"
as_user git -C "$REPO" show "origin/$BRANCH:scripts/lib/oneshot_worktree.sh" > "$LIB_TMP"
# shellcheck source=/dev/null
source "$LIB_TMP"
rm -f "$LIB_TMP"
oneshot_repair_worktree_ownership "$WT" "$RUN_USER" "$REPO"

cleanup_cred() {
  rm -f "$CRED/accurate.env" "$CRED/mongo.password" 2>/dev/null || true
  rmdir "$CRED" 2>/dev/null || true
}
trap cleanup_cred EXIT

# --- Ensure worktree + jalankan oneshot sebagai eternaladmin (semua git non-root) ---
sudo -u "$RUN_USER" env -i \
  HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)" \
  USER="$RUN_USER" \
  LOGNAME="$RUN_USER" \
  LANG=C.UTF-8 \
  PATH="/usr/bin:/bin:$REPO/backend/.venv/bin" \
  ETERNAL_V4_REPO="$REPO" \
  ETERNAL_V4_SJ_WT="$WT" \
  ETERNAL_V4_PR6_BRANCH="$BRANCH" \
  ETERNAL_V4_PR6_SHA="$PIN_SHA" \
  ETERNAL_V4_PYTHON="$PY_BIN" \
  ETERNAL_V4_BACKEND_ENV="$CRED/accurate.env" \
  ETERNAL_V4_TEST_MONGO_PW="$CRED/mongo.password" \
  ETERNAL_V4_TEST_MONGO_HOST=127.0.0.1 \
  ETERNAL_V4_TEST_MONGO_PORT=27019 \
  ETERNAL_V4_TEST_MONGO_RS=eternalV4TestRS \
  ETERNAL_V4_TEST_MONGO_DB=eternal_v4_test_sj_activation \
  ETERNAL_V4_TEST_MONGO_USER=eternal_v4_test_sync \
  ETERNAL_V4_TEST_MONGO_AUTH=eternal_v4_test_sj_activation \
  bash -c '
    set -euo pipefail
    REPO="$ETERNAL_V4_REPO"
    BRANCH="$ETERNAL_V4_PR6_BRANCH"
    WT="$ETERNAL_V4_SJ_WT"
    PIN="$ETERNAL_V4_PR6_SHA"
    LIB="$(mktemp -t oneshot-wt-lib.XXXXXX)"
    git -C "$REPO" show "origin/$BRANCH:scripts/lib/oneshot_worktree.sh" > "$LIB"
    # shellcheck source=/dev/null
    source "$LIB"
    rm -f "$LIB"
    export REPO_ORIGIN="$REPO" BRANCH WT_ROOT="$WT" EXPECTED_SHA="$PIN"
    oneshot_ensure_worktree "$REPO" "$BRANCH" "$WT" "$PIN"
    PROD_HEAD="$(git -C "$REPO" rev-parse HEAD)"
    PROD_BRANCH="$(git -C "$REPO" branch --show-current || true)"
    WT_HEAD="$(git -C "$WT" rev-parse HEAD)"
    echo "wrapper_ok=1 prod_branch=$PROD_BRANCH prod_head=$PROD_HEAD wt_head=$WT_HEAD run_user=$(id -un) git_as_root=0"
    exec bash "$WT/scripts/vps_sj_live_sync_oneshot.sh"
  '
RC=$?

PROD_HEAD_AFTER="$(as_user git -C "$REPO" rev-parse HEAD)"
PROD_BRANCH_AFTER="$(as_user git -C "$REPO" branch --show-current || true)"
if [[ "$PROD_HEAD_AFTER" != "$PROD_HEAD_BEFORE" || "$PROD_BRANCH_AFTER" != "$PROD_BRANCH_BEFORE" ]]; then
  echo "FAIL: production checkout berubah (dilarang)" >&2
  exit 1
fi

exit "$RC"
