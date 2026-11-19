#!/usr/bin/env bash
# ONE-SHOT: Accurate LIVE SJ → Mongo V4 TEST 27019 (VPS) — tanpa deploy / tanpa prod sync.
#
# Prasyarat VPS (Owner, sudah PASS):
#   - Test Mongo 127.0.0.1:27019, RS eternalV4TestRS, DB eternal_v4_test_sj_activation
#   - User eternal_v4_test_sync + password file
#   - Accurate live di /etc/eternal-v4/backend/backend.env (boleh berisi MONGO_URL 27018 —
#     script menimpa ke test SETELAH source)
#   - JANGAN sentuh 27017 / 27018
#
# Owner entrypoint (limited root → eternaladmin): scripts/vps_sj_oneshot_exec.sh
#
# DILARANG: cetak secret, tulis Accurate, sync ke eternal_v4, merge, restart service,
#           mengubah checkout main production.
# Alur: discover SJ↔SO (read-only) → seed PO terkait ke Mongo test → sync SJ
# terpilih 2x (idempotency). Prefer ACCURATE_SMOKE_SO_ID bila ada SJ single-SO;
# jika tidak, fallback PO eligible lain dari data Accurate asli (bukan tebakan).
# MULTI_SO_NOT_SUPPORTED tetap fail-closed (tidak dipilih sebagai kandidat uji).
# ONESHOT_PASS: ≥1 SJ tersinkron + second-pass idempotency PASS.
# ONESHOT_BLOCKED: discovery kosong / seluruh SJ skipped / tidak ada SJ teruji.

set -euo pipefail

umask 077

# --- Defaults VPS nyata (boleh override env) ---
REPO_ORIGIN="${ETERNAL_V4_REPO:-/home/eternaladmin/eternal-production-v4}"
BRANCH="${ETERNAL_V4_PR6_BRANCH:-cursor/v4-tahap01-po-fields-finalize-0ea3}"
EXPECTED_SHA="${ETERNAL_V4_PR6_SHA:-}"
WT_ROOT="${ETERNAL_V4_SJ_WT:-/tmp/eternal-v4-pr6-sj-oneshot}"
PY_BIN="${ETERNAL_V4_PYTHON:-/home/eternaladmin/eternal-production-v4/backend/.venv/bin/python}"
BACKEND_ENV="${ETERNAL_V4_BACKEND_ENV:-/etc/eternal-v4/backend/backend.env}"
MONGO_PW_FILE="${ETERNAL_V4_TEST_MONGO_PW:-/etc/eternal-v4/test-mongo-app.password}"
TEST_HOST="${ETERNAL_V4_TEST_MONGO_HOST:-127.0.0.1}"
TEST_PORT="${ETERNAL_V4_TEST_MONGO_PORT:-27019}"
TEST_RS="${ETERNAL_V4_TEST_MONGO_RS:-eternalV4TestRS}"
TEST_DB="${ETERNAL_V4_TEST_MONGO_DB:-eternal_v4_test_sj_activation}"
TEST_USER="${ETERNAL_V4_TEST_MONGO_USER:-eternal_v4_test_sync}"
TEST_AUTH_SOURCE="${ETERNAL_V4_TEST_MONGO_AUTH:-eternal_v4_test_sj_activation}"
SO_ID="${ACCURATE_SMOKE_SO_ID:-95106}"
MAX_PAGES="${SJ_SYNC_MAX_PAGES:-5}"
MAX_ROWS="${SJ_SYNC_MAX_ROWS:-100}"
PAGE_SIZE="${SJ_SYNC_PAGE_SIZE:-20}"

FINAL_CODE="oneshot_incomplete"
FINAL_OK=0
HEAD="unknown"
STAGES_FILE=""

log() { printf '%s\n' "$*"; }
die() {
  FINAL_CODE="fail"
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

json_out() { printf '%s\n' "$1"; }

_emit_final() {
  local code="$1"
  local ok="$2"
  json_out "{\"ok\":${ok},\"code\":\"${code}\",\"head\":\"${HEAD}\",\"test_port\":${TEST_PORT},\"test_db\":\"${TEST_DB}\",\"writes_accurate\":false,\"production_sync\":false,\"webhook\":false}"
}

cleanup() {
  local ec=$?
  [[ -n "${STAGES_FILE:-}" && -f "${STAGES_FILE}" ]] && rm -f "${STAGES_FILE}" 2>/dev/null || true
  [[ -n "${RUN_LOG:-}" && -f "${RUN_LOG}" ]] && rm -f "${RUN_LOG}" 2>/dev/null || true
  [[ -n "${PW_TMP:-}" && -f "${PW_TMP}" ]] && rm -f "${PW_TMP}" 2>/dev/null || true
  # Jangan hapus worktree (termasuk yang dibuat root sebelumnya).
  if [[ "$ec" -ne 0 && "$FINAL_OK" -eq 0 ]]; then
    case "$FINAL_CODE" in
      ONESHOT_PASS|ONESHOT_BLOCKED|secret_leak_guard|oneshot_failed) ;;
      *) _emit_final "${FINAL_CODE:-oneshot_failed}" false >&2 || true ;;
    esac
  fi
}
trap cleanup EXIT
RUN_LOG=""
WT_CREATED_BY_ONESHOT=0

# --- Fail-closed: port production/legacy ---
[[ "$TEST_PORT" == "27017" || "$TEST_PORT" == "27018" ]] && \
  die "TEST_PORT=$TEST_PORT dilarang (27017/27018)"
[[ "$TEST_DB" == "eternal_v4" || "$TEST_DB" == "eternal_produksi_v4" ]] && \
  die "TEST_DB production dilarang"

[[ -d "$REPO_ORIGIN/.git" ]] || die "Repo tidak ditemukan: $REPO_ORIGIN"
[[ -x "$PY_BIN" ]] || die "Python venv tidak ditemukan: $PY_BIN"
[[ -f "$BACKEND_ENV" ]] || die "Backend env Accurate tidak ada: $BACKEND_ENV"
[[ -r "$BACKEND_ENV" ]] || die "Backend env Accurate tidak readable (butuh stage kredensial terbatas)"
[[ -f "$MONGO_PW_FILE" ]] || die "Password Mongo test tidak ada: $MONGO_PW_FILE"
[[ -r "$MONGO_PW_FILE" ]] || die "Password Mongo test tidak readable (butuh stage kredensial terbatas)"

# Tolak jika dijalankan sebagai root dengan kode PR (wrapper harus drop ke eternaladmin)
if [[ "$(id -u)" -eq 0 ]]; then
  die "Jangan jalankan oneshot sebagai root — gunakan scripts/vps_sj_oneshot_exec.sh"
fi

PROD_HEAD_BEFORE="$(git -C "$REPO_ORIGIN" rev-parse HEAD)"
PROD_BRANCH_BEFORE="$(git -C "$REPO_ORIGIN" branch --show-current || true)"

# --- Worktree: semua git sebagai user ini; skip checkout jika SHA bersih + index writable ---
# shellcheck source=/dev/null
if [[ -f "$REPO_ORIGIN/scripts/lib/oneshot_worktree.sh" ]]; then
  source "$REPO_ORIGIN/scripts/lib/oneshot_worktree.sh"
elif [[ -f "$(dirname "$0")/lib/oneshot_worktree.sh" ]]; then
  source "$(dirname "$0")/lib/oneshot_worktree.sh"
else
  _LIB_TMP="$(mktemp -t oneshot-wt-lib.XXXXXX)"
  git -C "$REPO_ORIGIN" show "origin/${BRANCH}:scripts/lib/oneshot_worktree.sh" > "$_LIB_TMP"
  # shellcheck source=/dev/null
  source "$_LIB_TMP"
  rm -f "$_LIB_TMP"
fi

oneshot_ensure_worktree "$REPO_ORIGIN" "$BRANCH" "$WT_ROOT" "${EXPECTED_SHA:-}" \
  || die "ensure worktree gagal"

PROD_HEAD_AFTER="$(git -C "$REPO_ORIGIN" rev-parse HEAD)"
PROD_BRANCH_AFTER="$(git -C "$REPO_ORIGIN" branch --show-current || true)"
if [[ "$PROD_HEAD_AFTER" != "$PROD_HEAD_BEFORE" || "$PROD_BRANCH_AFTER" != "$PROD_BRANCH_BEFORE" ]]; then
  die "Checkout production berubah — abort"
fi

cd "$WT_ROOT"
HEAD="$(git rev-parse HEAD)"
log "worktree=$WT_ROOT"
log "head=$HEAD"
log "branch_target=$BRANCH"
log "git_user=$(id -un)"
if [[ -n "$EXPECTED_SHA" && "$HEAD" != "$EXPECTED_SHA" ]]; then
  die "HEAD $HEAD tidak cocok EXPECTED_SHA=$EXPECTED_SHA"
fi
oneshot_worktree_index_writable "$WT_ROOT" \
  || die "index worktree tidak writable oleh $(id -un)"

[[ -f backend/app/services/delivery_sync.py ]] || die "delivery_sync.py missing — checkout bukan PR #6?"
[[ -f backend/app/oneshot_mongo_lock.py ]] || die "oneshot_mongo_lock.py missing"
[[ -f scripts/verify_sj_sync_isolation.py ]] || die "verify_sj_sync_isolation.py missing"
[[ -f scripts/accurate_sj_sync_nonprod.py ]] || die "accurate_sj_sync_nonprod.py missing"

# --- Baca password tanpa echo / tanpa argv; bangun URI terkunci SEBELUM source backend.env ---
PW_TMP="$(mktemp -t eternal-v4-sj-pw.XXXXXX)"
tr -d '\r\n' < "$MONGO_PW_FILE" > "$PW_TMP"
chmod 600 "$PW_TMP"
[[ -s "$PW_TMP" ]] || { rm -f "$PW_TMP"; die "Password Mongo test kosong"; }

LOCKED_MONGO_URL="$("$PY_BIN" - <<'PY' "$PW_TMP" "$TEST_USER" "$TEST_HOST" "$TEST_PORT" "$TEST_DB" "$TEST_AUTH_SOURCE" "$TEST_RS"
import sys, urllib.parse
from pathlib import Path
pw_path, user, host, port, db, auth, rs = sys.argv[1:8]
pw = Path(pw_path).read_text(encoding="utf-8").strip()
Path(pw_path).write_text("", encoding="utf-8")  # scrub file ASAP
if not pw:
    raise SystemExit("empty_mongo_password")
enc = urllib.parse.quote(pw, safe="")
print(
    f"mongodb://{user}:{enc}@{host}:{port}/{db}"
    f"?authSource={auth}&replicaSet={rs}&directConnection=true"
)
PY
)"
rm -f "$PW_TMP"

case "$LOCKED_MONGO_URL" in
  *:27017*|*:27018*) die "LOCKED URI mengandung port terlarang 27017/27018" ;;
esac
case "$LOCKED_MONGO_URL" in
  *:"${TEST_PORT}"*) ;;
  *) die "LOCKED URI tidak mengandung TEST_PORT=$TEST_PORT" ;;
esac

STAGES_FILE="$(mktemp -t eternal-v4-sj-oneshot-stages.XXXXXX)"
RUN_LOG="$(mktemp -t eternal-v4-sj-oneshot-log.XXXXXX)"
export LOCKED_MONGO_URL
export WT_ROOT PY_BIN BACKEND_ENV
export TEST_HOST TEST_PORT TEST_RS TEST_DB TEST_USER TEST_AUTH_SOURCE
export SO_ID MAX_PAGES MAX_ROWS PAGE_SIZE STAGES_FILE

# --- Subshell: source Accurate env lalu PIN ulang Mongo test ---
set +e
(
  set -euo pipefail
  unset V4_ACCURATE_ACCOUNT_BASE V4_ACCURATE_BASE_URL \
        V4_ACCURATE_API_TOKEN V4_ACCURATE_API_SIGNATURE_SECRET \
        V4_ACCURATE_DB_ID V3_ACCURATE_READ_GATEWAY_URL V3_ACCURATE_READ_GATEWAY_TOKEN \
        GATEWAY_M2M_TOKEN \
        MONGO_URL MONGODB_URI MONGODB_URL DB_NAME MONGODB_DB \
        V4_MONGO_AUTH_SOURCE MONGO_AUTH_SOURCE || true

  set -a
  # shellcheck disable=SC1090
  source "$BACKEND_ENV"
  set +a

  # KRITIS: backend.env sering berisi MONGO_URL → 27018. Timpa SETELAH source.
  export APP_ENV=development
  export COOKIE_SECURE=false
  export V4_SJ_SYNC_ENABLED=1
  export V4_ACCURATE_REQUIRE_DB_PIN="${V4_ACCURATE_REQUIRE_DB_PIN:-1}"
  export PYTHONPATH="$WT_ROOT/backend"
  export ACCURATE_SMOKE_SO_ID="$SO_ID"
  export ETERNAL_V4_TEST_MONGO_RS="$TEST_RS"

  # Pin via helper (menolak locked URI 27017/27018)
  "$PY_BIN" - <<'PY'
import json, os, sys
from app.config import clear_settings_cache
from app.db_guard import UnsafeMongoTargetError
from app.oneshot_mongo_lock import (
    assert_effective_sj_oneshot_mongo_target,
    pin_sj_oneshot_test_mongo_env,
)

locked = os.environ["LOCKED_MONGO_URL"]
try:
    pin_sj_oneshot_test_mongo_env(
        locked_uri=locked,
        db_name=os.environ["TEST_DB"],
        auth_source=os.environ["TEST_AUTH_SOURCE"],
    )
except UnsafeMongoTargetError as e:
    print(json.dumps({"ok": False, "stage": "pin", "code": e.code}))
    sys.exit(20)

clear_settings_cache()
try:
    report = assert_effective_sj_oneshot_mongo_target(
        expected_host=os.environ["TEST_HOST"],
        expected_port=int(os.environ["TEST_PORT"]),
        expected_db=os.environ["TEST_DB"],
        expected_user=os.environ["TEST_USER"],
        expected_auth_source=os.environ["TEST_AUTH_SOURCE"],
        expected_replica_set=os.environ["TEST_RS"],
        app_env="development",
    )
except UnsafeMongoTargetError as e:
    print(json.dumps({"ok": False, "stage": "effective_settings", "code": e.code}))
    sys.exit(21)

if not report.get("sj_sync_enabled"):
    print(json.dumps({"ok": False, "stage": "effective_settings", "code": "SJ_SYNC_DISABLED"}))
    sys.exit(24)

print(json.dumps({"ok": True, "stage": "pin_and_settings", **{k: report[k] for k in (
    "host", "port", "db", "user", "auth_source", "replica_set", "sj_sync_enabled",
)}}))
PY

  # Subproses Python tidak mewariskan mutate ke shell — pin ulang eksplisit:
  export MONGO_URL="$LOCKED_MONGO_URL"
  export MONGODB_URI="$LOCKED_MONGO_URL"
  unset MONGODB_URL || true
  export DB_NAME="$TEST_DB"
  export MONGODB_DB="$TEST_DB"
  export V4_MONGO_AUTH_SOURCE="$TEST_AUTH_SOURCE"
  export MONGO_AUTH_SOURCE="$TEST_AUTH_SOURCE"

  case "${MONGO_URL}${MONGODB_URI}" in
    *:27017*|*:27018*)
      echo '{"ok":false,"stage":"shell_env","code":"forbidden_port_after_pin"}'
      exit 22
      ;;
  esac

  # Koneksi Mongo nyata + RS + user (SEBELUM tulis bisnis)
  "$PY_BIN" - <<'PY'
import json, os, sys
from urllib.parse import unquote, urlparse
from pymongo import MongoClient
from app.config import clear_settings_cache, get_settings
from app.db_guard import UnsafeMongoTargetError, extract_mongo_ports
from app.oneshot_mongo_lock import assert_effective_sj_oneshot_mongo_target

clear_settings_cache()
try:
    assert_effective_sj_oneshot_mongo_target(
        expected_host=os.environ["TEST_HOST"],
        expected_port=int(os.environ["TEST_PORT"]),
        expected_db=os.environ["TEST_DB"],
        expected_user=os.environ["TEST_USER"],
        expected_auth_source=os.environ["TEST_AUTH_SOURCE"],
        expected_replica_set=os.environ["TEST_RS"],
        app_env="development",
    )
except UnsafeMongoTargetError as e:
    print(json.dumps({"ok": False, "stage": "pre_connect", "code": e.code}))
    sys.exit(23)

settings = get_settings()
uri = settings.mongodb_uri
ports = extract_mongo_ports(uri)
if 27017 in ports or 27018 in ports or int(os.environ["TEST_PORT"]) not in ports:
    print(json.dumps({"ok": False, "stage": "pre_connect", "code": "forbidden_or_wrong_port", "ports": ports}))
    sys.exit(23)

client = MongoClient(uri, serverSelectionTimeoutMS=8000)
hello = client.admin.command("hello")
set_name = hello.get("setName")
primary = bool(hello.get("isWritablePrimary") or hello.get("ismaster"))
want_rs = os.environ["TEST_RS"]
if set_name != want_rs or not primary:
    print(json.dumps({
        "ok": False, "stage": "replica", "code": "replica_not_ready",
        "setName": set_name, "primary": primary, "want": want_rs,
    }))
    sys.exit(25)

parsed = urlparse(uri)
conn_user = unquote(parsed.username or "")
if conn_user != os.environ["TEST_USER"]:
    print(json.dumps({"ok": False, "stage": "auth", "code": "wrong_mongo_user"}))
    sys.exit(26)

db = client[settings.mongodb_db]
if db.name != os.environ["TEST_DB"]:
    print(json.dumps({"ok": False, "stage": "auth", "code": "wrong_db_name"}))
    sys.exit(27)

marker = {"_id": "__sj_oneshot_probe__", "ok": True}
db["__oneshot_probe__"].replace_one({"_id": marker["_id"]}, marker, upsert=True)
db["__oneshot_probe__"].delete_one({"_id": marker["_id"]})

print(json.dumps({
    "ok": True, "stage": "mongo_live",
    "ports": ports, "setName": set_name, "primary": primary,
    "db": db.name, "user_ok": True, "writes_accurate": False,
}))
PY

  "$PY_BIN" scripts/verify_sj_sync_isolation.py
  ISO=$?
  [[ "$ISO" -eq 0 ]] || exit 28

  DISCOVER_OUT="$("$PY_BIN" scripts/accurate_sj_sync_nonprod.py \
    --discover \
    --prefer-po-id "$SO_ID" \
    --page-size "$PAGE_SIZE" --max-pages "$MAX_PAGES" --max-rows "$MAX_ROWS" \
    --actor-email "vps-oneshot@eternal.local")"
  DISCOVER_RC=$?
  printf '%s\n' "$DISCOVER_OUT"
  printf '%s\n' "$DISCOVER_OUT" >> "$STAGES_FILE"
  if [[ "$DISCOVER_RC" -ne 0 ]]; then
    # Tidak ada SJ single-SO eligible di window — BLOCKED (bukan fail sync).
    exit 42
  fi

  SELECTED_PO="$(printf '%s' "$DISCOVER_OUT" | "$PY_BIN" -c 'import json,sys; r=json.load(sys.stdin); print(r.get("selected_po_id") or "")')"
  SELECTED_SJ_CSV="$(printf '%s' "$DISCOVER_OUT" | "$PY_BIN" -c 'import json,sys; r=json.load(sys.stdin); print(",".join(r.get("selected_sj_ids") or []))')"
  [[ -n "$SELECTED_PO" && -n "$SELECTED_SJ_CSV" ]] || exit 42

  export ONESHOT_SELECTED_PO="$SELECTED_PO"
  export ONESHOT_SELECTED_SJ_CSV="$SELECTED_SJ_CSV"

  "$PY_BIN" - <<'PY'
import json, os, sys
from app.config import clear_settings_cache
from app.db import close_client, ensure_indexes, get_db
from app.db_guard import UnsafeMongoTargetError
from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.oneshot_mongo_lock import assert_effective_sj_oneshot_mongo_target
from app.repositories.po_repository import get_by_accurate_id, count_by_accurate_id
from app.services.po_sync import sync_one_sales_order_from_accurate

clear_settings_cache()
close_client()
assert_effective_sj_oneshot_mongo_target(
    expected_host=os.environ["TEST_HOST"],
    expected_port=int(os.environ["TEST_PORT"]),
    expected_db=os.environ["TEST_DB"],
    expected_user=os.environ["TEST_USER"],
    expected_auth_source=os.environ["TEST_AUTH_SOURCE"],
    expected_replica_set=os.environ["TEST_RS"],
    app_env="development",
)
so_id = os.environ.get("ONESHOT_SELECTED_PO") or os.environ.get("ACCURATE_SMOKE_SO_ID", "95106")
db = get_db()
ensure_indexes(db)
existing = get_by_accurate_id(db, so_id)
if existing is not None:
    print(json.dumps({
        "ok": True, "stage": "po_seed", "action": "already_present",
        "accurate_id": str(so_id), "po_count": count_by_accurate_id(db, so_id),
        "prefer_po_id": os.environ.get("ACCURATE_SMOKE_SO_ID", "95106"),
        "writes_accurate": False,
    }))
    sys.exit(0)
try:
    client = AccurateReadClient.from_settings()
    result = sync_one_sales_order_from_accurate(db, so_id, client=client, mongo_guard="test")
except UnsafeMongoTargetError as e:
    print(json.dumps({"ok": False, "stage": "po_seed", "code": e.code}))
    sys.exit(30)
except AccurateAuthError as e:
    print(json.dumps({"ok": False, "stage": "po_seed", "code": e.code}))
    sys.exit(31)
except Exception as e:
    print(json.dumps({"ok": False, "stage": "po_seed", "error": type(e).__name__}))
    sys.exit(32)
print(json.dumps({
    "ok": True, "stage": "po_seed", "action": "synced",
    "accurate_id": result.get("accurate_id"),
    "was_insert": result.get("was_insert"),
    "line_count": result.get("line_count"),
    "idempotent": result.get("idempotent"),
    "prefer_po_id": os.environ.get("ACCURATE_SMOKE_SO_ID", "95106"),
    "writes_accurate": False,
}))
PY

  SYNC1_OUT="$("$PY_BIN" scripts/accurate_sj_sync_nonprod.py \
    --sj-ids "$SELECTED_SJ_CSV" \
    --actor-email "vps-oneshot@eternal.local")"
  SYNC1=$?
  printf '%s\n' "$SYNC1_OUT"
  [[ "$SYNC1" -eq 0 ]] || exit 40
  printf '%s\n' "$SYNC1_OUT" >> "$STAGES_FILE"

  SYNC2_OUT="$("$PY_BIN" scripts/accurate_sj_sync_nonprod.py \
    --sj-ids "$SELECTED_SJ_CSV" \
    --actor-email "vps-oneshot@eternal.local")"
  SYNC2=$?
  printf '%s\n' "$SYNC2_OUT"
  [[ "$SYNC2" -eq 0 ]] || exit 41
  printf '%s\n' "$SYNC2_OUT" >> "$STAGES_FILE"

  "$PY_BIN" - <<'PY'
import json, os, sys
from app.config import clear_settings_cache
from app.db import close_client, get_db
from app.oneshot_mongo_lock import assert_effective_sj_oneshot_mongo_target
from app.repositories import delivery_repository as deliveries

clear_settings_cache()
close_client()
assert_effective_sj_oneshot_mongo_target(
    expected_host=os.environ["TEST_HOST"],
    expected_port=int(os.environ["TEST_PORT"]),
    expected_db=os.environ["TEST_DB"],
    expected_user=os.environ["TEST_USER"],
    expected_auth_source=os.environ["TEST_AUTH_SOURCE"],
    expected_replica_set=os.environ["TEST_RS"],
    app_env="development",
)
db = get_db()
notes = deliveries.list_delivery_notes(db)
po_ids = sorted({str(n.get("accurate_po_id") or "") for n in notes if n.get("accurate_po_id")})
sj_ids = [str(n.get("accurate_sj_id") or "") for n in notes]
dup = len(sj_ids) - len(set(sj_ids))
qty_total = 0.0
units = {}
versions = []
multi_item = 0
for n in notes:
    versions.append(int(n.get("sj_version") or 1))
    items = n.get("items") or []
    if len(items) > 1:
        multi_item += 1
    for it in items:
        q = float(it.get("qty") or 0)
        qty_total += q
        u = str(it.get("unit") or "") or "?"
        units[u] = units.get(u, 0.0) + q
audit_n = db["audit_events"].count_documents({
    "action": {"$in": [
        "delivery.note.revision",
        "delivery.loading.confirm",
        "delivery.loading.discrepancy",
        "delivery.sj.sync",
    ]}
}) if "audit_events" in db.list_collection_names() else 0

orphan = 0
missing_line = 0
for n in notes:
    if not n.get("accurate_po_id"):
        orphan += 1
    for it in n.get("items") or []:
        if not it.get("accurate_line_id"):
            missing_line += 1

created = updated = unchanged = skipped = failed = listed = 0
stages_path = os.environ.get("STAGES_FILE", "")
sync_rows = []
discover = {}
if stages_path and os.path.isfile(stages_path):
    with open(stages_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("stage") == "discover" or row.get("mode") == "discover":
                discover = row
                continue
            if row.get("stage") == "sync" or "created" in row or "skipped" in row:
                sync_rows.append(row)
                created += int(row.get("created") or 0)
                updated += int(row.get("updated") or 0)
                unchanged += int(row.get("unchanged") or 0)
                skipped += int(row.get("skipped") or 0)
                failed += int(row.get("failed") or 0)
                listed += int(row.get("listed") or 0)

processed = created + updated + unchanged
sj_count = len(notes)
structure_ok = dup == 0 and orphan == 0 and missing_line == 0

pass1 = sync_rows[0] if len(sync_rows) >= 1 else {}
pass2 = sync_rows[1] if len(sync_rows) >= 2 else {}
pass1_processed = (
    int(pass1.get("created") or 0)
    + int(pass1.get("updated") or 0)
    + int(pass1.get("unchanged") or 0)
)
idempotent_second_pass = (
    len(sync_rows) >= 2
    and int(pass2.get("created") or 0) == 0
    and int(pass2.get("failed") or 0) == 0
    and bool(pass2.get("ok", True))
)
prefer_po = str(discover.get("prefer_po_id") or os.environ.get("ACCURATE_SMOKE_SO_ID", "95106"))
prefer_sj = int(discover.get("prefer_po_sj_count") or 0)
selected_po = str(discover.get("selected_po_id") or os.environ.get("ONESHOT_SELECTED_PO") or "")

if int(discover.get("selected_sj_count") or 0) == 0 and sj_count == 0:
    code = "ONESHOT_BLOCKED"
    ok = False
    reason = "no_eligible_single_so_sj_in_window"
elif sj_count == 0 and processed == 0 and (skipped > 0 or listed == 0):
    code = "ONESHOT_BLOCKED"
    ok = False
    reason = "all_sj_skipped_or_no_sj_tested"
elif sj_count == 0 or processed == 0 or pass1_processed == 0:
    code = "ONESHOT_BLOCKED"
    ok = False
    reason = "no_sj_successfully_tested"
elif not structure_ok:
    code = "oneshot_validate_failed"
    ok = False
    reason = "structure_or_relation_invalid"
elif not idempotent_second_pass:
    code = "oneshot_idempotency_failed"
    ok = False
    reason = "second_pass_not_idempotent"
else:
    code = "ONESHOT_PASS"
    ok = True
    reason = "sj_synced_and_idempotent"

print(json.dumps({
    "ok": ok,
    "code": code,
    "reason": reason,
    "stage": "validate",
    "sj_count": sj_count,
    "unique_sj": len(set(sj_ids)),
    "duplicate_sj_ids": dup,
    "po_id_count": len(po_ids),
    "qty_total": round(qty_total, 3),
    "qty_by_unit": {k: round(v, 3) for k, v in sorted(units.items())},
    "sj_with_multiple_items": multi_item,
    "sj_version_max": max(versions) if versions else 0,
    "orphan_po_refs": orphan,
    "items_missing_line_id": missing_line,
    "audit_events_related": audit_n,
    "sync_processed": processed,
    "sync_skipped": skipped,
    "sync_failed": failed,
    "sync_listed": listed,
    "pass1_processed": pass1_processed,
    "idempotent_second_pass": idempotent_second_pass,
    "prefer_po_id": prefer_po,
    "prefer_po_sj_count": prefer_sj,
    "prefer_po_had_sj": prefer_sj > 0,
    "selected_po_id": selected_po,
    "selection_reason": discover.get("selection_reason"),
    "discover_multi_so_rejected": discover.get("multi_so_rejected"),
    "writes_accurate": False,
    "production_ports_touched": False,
}))
sys.exit(0 if ok else (42 if code == "ONESHOT_BLOCKED" else 50))
PY
) >"$RUN_LOG" 2>&1
RC=$?
set -e

# Scan kebocoran kasar lalu cetak log (tanpa secret patterns)
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    *SIGNATURE*|*API_TOKEN=*|*password=*|*mongodb://*:*@*)
      rm -f "$RUN_LOG"
      FINAL_CODE="secret_leak_guard"
      json_out '{"ok":false,"code":"secret_leak_guard"}'
      exit 60
      ;;
  esac
  printf '%s\n' "$line"
done < "$RUN_LOG"
rm -f "$RUN_LOG"

if [[ "$RC" -eq 0 ]]; then
  FINAL_CODE="ONESHOT_PASS"
  FINAL_OK=1
  _emit_final "ONESHOT_PASS" true
  exit 0
fi

if [[ "$RC" -eq 42 ]]; then
  FINAL_CODE="ONESHOT_BLOCKED"
  FINAL_OK=0
  _emit_final "ONESHOT_BLOCKED" false
  exit 42
fi

FINAL_CODE="oneshot_failed"
FINAL_OK=0
_emit_final "oneshot_failed" false
exit "$RC"
