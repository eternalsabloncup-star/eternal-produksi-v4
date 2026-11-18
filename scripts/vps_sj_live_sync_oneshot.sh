#!/usr/bin/env bash
# ONE-SHOT: Accurate LIVE SJ → Mongo V4 TEST (VPS) — tanpa deploy / tanpa prod sync.
#
# Prasyarat VPS (Owner, sudah PASS):
#   - Test Mongo 127.0.0.1:27019, RS eternalV4TestRS, DB eternal_v4_test_sj_activation
#   - User eternal_v4_test_sync + password file
#   - Accurate live di /etc/eternal-v4/backend/backend.env
#   - JANGAN sentuh 27017 / 27018
#
# Satu perintah (setelah PR #6 di-fetch):
#   sudo -u ubuntu bash /opt/eternal-v4/src/scripts/vps_sj_live_sync_oneshot.sh
#
# Atau dari checkout yang sudah berisi branch PR #6:
#   ./scripts/vps_sj_live_sync_oneshot.sh
#
# DILARANG: cetak secret, tulis Accurate, sync ke eternal_v4, merge, restart service.

set -euo pipefail

umask 077

# --- Defaults VPS (boleh override env) ---
REPO_ORIGIN="${ETERNAL_V4_REPO:-/opt/eternal-v4/src}"
BRANCH="${ETERNAL_V4_PR6_BRANCH:-cursor/v4-tahap01-po-fields-finalize-0ea3}"
EXPECTED_SHA="${ETERNAL_V4_PR6_SHA:-}"   # opsional pin SHA
WT_ROOT="${ETERNAL_V4_SJ_WT:-/tmp/eternal-v4-pr6-sj-oneshot}"
VENV="${ETERNAL_V4_VENV:-/opt/eternal-v4/venv}"
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

log() { printf '%s\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

json_out() { printf '%s\n' "$1"; }

# --- Fail-closed: port production/legacy ---
[[ "$TEST_PORT" == "27017" || "$TEST_PORT" == "27018" ]] && \
  die "TEST_PORT=$TEST_PORT dilarang (27017/27018)"

[[ -d "$REPO_ORIGIN/.git" ]] || die "Repo tidak ditemukan: $REPO_ORIGIN"
[[ -x "$VENV/bin/python" ]] || die "Venv tidak ditemukan: $VENV/bin/python"
[[ -f "$BACKEND_ENV" ]] || die "Backend env Accurate tidak ada: $BACKEND_ENV"
[[ -f "$MONGO_PW_FILE" ]] || die "Password Mongo test tidak ada: $MONGO_PW_FILE"
[[ -r "$MONGO_PW_FILE" ]] || die "Password Mongo test tidak readable"

# --- Worktree terpisah (tidak mengganti main production checkout) ---
mkdir -p "$(dirname "$WT_ROOT")"
if [[ -d "$WT_ROOT/.git" || -f "$WT_ROOT/.git" ]]; then
  log "worktree_reuse=$WT_ROOT"
  git -C "$WT_ROOT" fetch origin "$BRANCH" --quiet
  git -C "$WT_ROOT" checkout -f "origin/$BRANCH" --quiet
else
  # Bersihkan path kosong/rusak
  rm -rf "$WT_ROOT"
  git -C "$REPO_ORIGIN" fetch origin "$BRANCH" --quiet
  git -C "$REPO_ORIGIN" worktree add --detach "$WT_ROOT" "origin/$BRANCH"
fi

cd "$WT_ROOT"
HEAD="$(git rev-parse HEAD)"
log "worktree=$WT_ROOT"
log "head=$HEAD"
log "branch_target=$BRANCH"
if [[ -n "$EXPECTED_SHA" && "$HEAD" != "$EXPECTED_SHA" && "$HEAD" != "$EXPECTED_SHA"* ]]; then
  die "HEAD $HEAD tidak cocok EXPECTED_SHA=$EXPECTED_SHA"
fi

# Pastikan konektor + kill switch ada di tree ini
[[ -f backend/app/services/delivery_sync.py ]] || die "delivery_sync.py missing — checkout bukan PR #6?"
[[ -f scripts/verify_sj_sync_isolation.py ]] || die "verify_sj_sync_isolation.py missing"

# --- Baca password tanpa echo ---
MONGO_PW="$(tr -d '\r\n' < "$MONGO_PW_FILE")"
[[ -n "$MONGO_PW" ]] || die "Password Mongo test kosong"

# URL-encode password minimal (python, tanpa cetak nilai)
MONGO_PW_ENC="$("$VENV/bin/python" - <<'PY' "$MONGO_PW"
import sys, urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=""))
PY
)"

MONGO_URL="mongodb://${TEST_USER}:${MONGO_PW_ENC}@${TEST_HOST}:${TEST_PORT}/${TEST_DB}?authSource=${TEST_AUTH_SOURCE}&replicaSet=${TEST_RS}&directConnection=true"

# Hapus password plaintext dari shell ASAP (URI tetap di env proses ini saja)
unset MONGO_PW MONGO_PW_ENC
export MONGO_URL

# Sanity URI ports (string check, tanpa secret)
case "$MONGO_URL" in
  *:27017*|*:27018*) die "URI mengandung port terlarang 27017/27018" ;;
esac
case "$MONGO_URL" in
  *:"${TEST_PORT}"*) ;;
  *) die "URI tidak mengandung TEST_PORT=$TEST_PORT" ;;
esac

# --- Subshell terisolasi: load Accurate env + jalankan seluruh rangkaian ---
set +e
SUMMARY="$(
  set -euo pipefail
  # Jangan warisi stub Cloud Agent / V3
  unset V4_ACCURATE_ACCOUNT_BASE V4_ACCURATE_BASE_URL \
        V4_ACCURATE_API_TOKEN V4_ACCURATE_API_SIGNATURE_SECRET \
        V4_ACCURATE_DB_ID V3_ACCURATE_READ_GATEWAY_URL V3_ACCURATE_READ_GATEWAY_TOKEN \
        GATEWAY_M2M_TOKEN || true

  set -a
  # shellcheck disable=SC1090
  source "$BACKEND_ENV"
  set +a

  export APP_ENV=test
  export COOKIE_SECURE=false
  export MONGODB_URI="$MONGO_URL"
  export DB_NAME="$TEST_DB" MONGODB_DB="$TEST_DB"
  export V4_MONGO_AUTH_SOURCE="$TEST_AUTH_SOURCE"
  export V4_SJ_SYNC_ENABLED=1
  export V4_ACCURATE_REQUIRE_DB_PIN="${V4_ACCURATE_REQUIRE_DB_PIN:-1}"
  export PYTHONPATH="$WT_ROOT/backend"
  export ACCURATE_SMOKE_SO_ID="$SO_ID"
  export ETERNAL_V4_TEST_MONGO_RS="$TEST_RS"

  PY="$VENV/bin/python"

  # 1) Isolasi + RS + user (tanpa payload pelanggan)
  "$PY" - <<'PY'
import json, os, sys
from urllib.parse import urlparse, parse_qs
from pymongo import MongoClient
from app.db_guard import assert_safe_mongo_sync_target, UnsafeMongoTargetError, extract_mongo_ports
from app.config import clear_settings_cache, get_settings

clear_settings_cache()
uri = os.environ["MONGO_URL"]
db_name = os.environ["DB_NAME"]
ports = extract_mongo_ports(uri)
if 27017 in ports or 27018 in ports:
    print(json.dumps({"ok": False, "code": "forbidden_port", "ports": ports}))
    sys.exit(20)
try:
    assert_safe_mongo_sync_target(
        uri=uri, db_name=db_name, app_env="test",
        expected_auth_source=os.environ.get("V4_MONGO_AUTH_SOURCE"),
    )
except UnsafeMongoTargetError as e:
    print(json.dumps({"ok": False, "code": e.code, "stage": "mongo_guard"}))
    sys.exit(21)

client = MongoClient(uri, serverSelectionTimeoutMS=8000)
hello = client.admin.command("hello")
set_name = hello.get("setName")
primary = bool(hello.get("isWritablePrimary") or hello.get("ismaster"))
want_rs = os.environ.get("ETERNAL_V4_TEST_MONGO_RS", "eternalV4TestRS")
if set_name != want_rs or not primary:
    print(json.dumps({
        "ok": False, "code": "replica_not_ready",
        "setName": set_name, "primary": primary, "want": want_rs,
    }))
    sys.exit(22)

db = client[db_name]
# privilege smoke: insert+delete marker
marker = {"_id": "__sj_oneshot_probe__", "ok": True}
db["__oneshot_probe__"].replace_one({"_id": marker["_id"]}, marker, upsert=True)
db["__oneshot_probe__"].delete_one({"_id": marker["_id"]})

settings = get_settings()
host = urlparse(settings.accurate_base_url).hostname or ""
if not host or host.endswith(".test"):
    print(json.dumps({"ok": False, "code": "accurate_not_live", "host": host or None}))
    sys.exit(23)
if not settings.sj_sync_enabled:
    print(json.dumps({"ok": False, "code": "SJ_SYNC_DISABLED"}))
    sys.exit(24)

print(json.dumps({
    "ok": True, "stage": "preflight",
    "ports": ports, "setName": set_name, "primary": primary,
    "db": db_name, "accurate_host_ok": True,
    "sj_sync_enabled": True, "writes_accurate": False,
}))
PY

  # 2) Pastikan PO uji ada (Accurate GET → Mongo test)
  "$PY" - <<'PY'
import json, os, sys
from app.config import clear_settings_cache
from app.db import close_client, ensure_indexes, get_db
from app.db_guard import UnsafeMongoTargetError
from app.integrations.accurate.auth import AccurateAuthError
from app.integrations.accurate.client import AccurateReadClient
from app.repositories.po_repository import get_by_accurate_id, count_by_accurate_id
from app.services.po_sync import sync_one_sales_order_from_accurate

clear_settings_cache()
close_client()
so_id = os.environ.get("ACCURATE_SMOKE_SO_ID", "95106")
db = get_db()
ensure_indexes(db)
existing = get_by_accurate_id(db, so_id)
if existing is not None:
    print(json.dumps({
        "ok": True, "stage": "po_seed", "action": "already_present",
        "accurate_id": str(so_id), "po_count": count_by_accurate_id(db, so_id),
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
}))
PY

  # 3) Sync SJ #1
  "$PY" scripts/accurate_sj_sync_nonprod.py \
    --page-size "$PAGE_SIZE" --max-pages "$MAX_PAGES" --max-rows "$MAX_ROWS" \
    --actor-email "vps-oneshot@eternal.local"
  SYNC1=$?
  [[ "$SYNC1" -eq 0 ]] || exit 40

  # 4) Sync SJ #2 (idempotency)
  "$PY" scripts/accurate_sj_sync_nonprod.py \
    --page-size "$PAGE_SIZE" --max-pages "$MAX_PAGES" --max-rows "$MAX_ROWS" \
    --actor-email "vps-oneshot@eternal.local"
  SYNC2=$?
  [[ "$SYNC2" -eq 0 ]] || exit 41

  # 5) Validasi hasil aman
  "$PY" - <<'PY'
import json, os, sys
from app.config import clear_settings_cache
from app.db import close_client, get_db
from app.repositories import delivery_repository as deliveries

clear_settings_cache()
close_client()
db = get_db()
notes = deliveries.list_delivery_notes(db)
# Agregat aman — tanpa nama pelanggan / alamat / nomor mentah panjang
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
    ]}
}) if "audit_events" in db.list_collection_names() else 0

# Relasi: setiap note punya accurate_po_id + items dengan accurate_line_id
orphan = 0
missing_line = 0
for n in notes:
    if not n.get("accurate_po_id"):
        orphan += 1
    for it in n.get("items") or []:
        if not it.get("accurate_line_id"):
            missing_line += 1

ok = dup == 0 and orphan == 0 and missing_line == 0
print(json.dumps({
    "ok": ok,
    "stage": "validate",
    "sj_count": len(notes),
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
    "writes_accurate": False,
    "production_ports_touched": False,
}))
sys.exit(0 if ok else 50)
PY
)"
RC=$?
set -e

# Cetak ringkasan (JSON stages) — guard kebocoran kasar
while IFS= read -r line; do
  case "$line" in
    *SIGNATURE*|*API_TOKEN=*)
      json_out '{"ok":false,"code":"secret_leak_guard"}'
      exit 60
      ;;
  esac
  printf '%s\n' "$line"
done <<< "$SUMMARY"

if [[ "$RC" -ne 0 ]]; then
  json_out "{\"ok\":false,\"code\":\"oneshot_failed\",\"exit\":$RC,\"head\":\"$HEAD\"}"
  exit "$RC"
fi

json_out "{\"ok\":true,\"code\":\"ONESHOT_PASS\",\"head\":\"$HEAD\",\"test_port\":$TEST_PORT,\"test_db\":\"$TEST_DB\",\"writes_accurate\":false,\"production_sync\":false,\"webhook\":false}"
exit 0
