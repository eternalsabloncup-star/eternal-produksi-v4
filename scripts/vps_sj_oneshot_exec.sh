#!/usr/bin/env bash
# Owner wrapper: limited root for credentials + worktree only.
# PR oneshot code runs as eternaladmin — NOT as root.
# Tidak mengubah checkout branch main production.
#
# Satu perintah:
#   sudo bash /tmp/vps_sj_oneshot_exec.sh
# (file diambil dari origin branch via git show — lihat header laporan FINAL)

set -euo pipefail
umask 077

if [[ "$(id -u)" -ne 0 ]]; then
  echo 'FAIL: jalankan dengan sudo (root hanya untuk stage kredensial + worktree)' >&2
  exit 1
fi

REPO="${ETERNAL_V4_REPO:-/home/eternaladmin/eternal-production-v4}"
BRANCH="${ETERNAL_V4_PR6_BRANCH:-cursor/v4-tahap01-po-fields-finalize-0ea3}"
PIN_SHA="${ETERNAL_V4_PR6_SHA:-}"  # kosong = tip origin/BRANCH setelah fetch
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

PROD_HEAD_BEFORE="$(git -C "$REPO" rev-parse HEAD)"
PROD_BRANCH_BEFORE="$(git -C "$REPO" branch --show-current || true)"

# Fetch only — tidak checkout main
git -C "$REPO" fetch origin "$BRANCH" --quiet
TIP="$(git -C "$REPO" rev-parse "origin/$BRANCH")"
if [[ -z "$PIN_SHA" ]]; then
  PIN_SHA="$TIP"
fi

# Worktree terpisah (tanpa menyentuh index/HEAD main)
if [[ -e "$WT" ]]; then
  if git -C "$WT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$WT" checkout -f "origin/$BRANCH" --quiet
  else
    echo "FAIL: WT path exists but is not a git worktree: $WT" >&2
    exit 1
  fi
else
  git -C "$REPO" worktree add --detach "$WT" "origin/$BRANCH"
fi

WT_HEAD="$(git -C "$WT" rev-parse HEAD)"
if [[ "$WT_HEAD" != "$PIN_SHA" ]]; then
  echo "FAIL: worktree HEAD $WT_HEAD != PIN_SHA $PIN_SHA" >&2
  exit 1
fi
if [[ "$PIN_SHA" != "$TIP" ]]; then
  echo "FAIL: PIN_SHA $PIN_SHA != origin/$BRANCH tip $TIP" >&2
  exit 1
fi

# Stage kredensial terbatas: Accurate* saja + mongo test password.
# Strip seluruh alias Mongo dari backend.env agar 27018 tidak ikut ke proses uji.
rm -rf "$CRED"
mkdir -m 700 "$CRED"
# shellcheck disable=SC2016
grep -Ev '^(MONGO_URL|MONGODB_URI|MONGODB_URL|DB_NAME|MONGODB_DB|V4_MONGO_AUTH_SOURCE|MONGO_AUTH_SOURCE)=' \
  "$SRC_BACKEND_ENV" > "$CRED/accurate.env"
cp "$SRC_MONGO_PW" "$CRED/mongo.password"
chmod 600 "$CRED/accurate.env" "$CRED/mongo.password"
chown -R "$RUN_USER:$RUN_USER" "$CRED"
# Worktree harus readable/executable oleh runner (bukan root-owned lock)
chown -R "$RUN_USER:$RUN_USER" "$WT" 2>/dev/null || true

PROD_HEAD_AFTER="$(git -C "$REPO" rev-parse HEAD)"
PROD_BRANCH_AFTER="$(git -C "$REPO" branch --show-current || true)"
if [[ "$PROD_HEAD_AFTER" != "$PROD_HEAD_BEFORE" || "$PROD_BRANCH_AFTER" != "$PROD_BRANCH_BEFORE" ]]; then
  echo "FAIL: production checkout berubah (dilarang)" >&2
  exit 1
fi

echo "wrapper_ok=1 prod_branch=$PROD_BRANCH_AFTER prod_head=$PROD_HEAD_AFTER wt_head=$WT_HEAD run_user=$RUN_USER"

cleanup_cred() {
  rm -f "$CRED/accurate.env" "$CRED/mongo.password" 2>/dev/null || true
  rmdir "$CRED" 2>/dev/null || true
}
trap cleanup_cred EXIT

# Jalankan kode PR sebagai eternaladmin (bukan root)
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
  bash "$WT/scripts/vps_sj_live_sync_oneshot.sh"
RC=$?
exit "$RC"
