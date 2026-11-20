#!/bin/bash
# Start an isolated MongoDB replica set for local/CI tests (NOT production).
set -euo pipefail

NAME="${TEST_MONGO_NAME:-eternal-v4-test-mongo}"
# Default 27019 — test terisolasi (27018 = V4 production; 27017 = ERP/compose).
PORT="${TEST_MONGO_PORT:-27019}"
ROOT_USER="${MONGO_ROOT_USERNAME:-eternal}"
ROOT_PASS="${MONGO_ROOT_PASSWORD:-eternal_dev_password}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Cache kecil + memori index terbatas → lebih stabil di Cloud Agent.
WT_CACHE_GB="${TEST_MONGO_WT_CACHE_GB:-0.5}"

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker rm -f "$NAME" >/dev/null
fi

docker run -d --name "$NAME" \
  --ulimit nofile=65535:65535 \
  -p "127.0.0.1:${PORT}:27017" \
  -e MONGO_INITDB_ROOT_USERNAME="$ROOT_USER" \
  -e MONGO_INITDB_ROOT_PASSWORD="$ROOT_PASS" \
  -v "$ROOT_DIR/scripts/mongo-dev.key:/tmp/mongo-dev.key:ro" \
  mongo:7.0.16 \
  bash -c "cp /tmp/mongo-dev.key /data/configdb/keyfile && chmod 400 /data/configdb/keyfile && chown mongodb:mongodb /data/configdb/keyfile && exec docker-entrypoint.sh mongod --replSet rs0 --bind_ip_all --auth --keyFile /data/configdb/keyfile --wiredTigerCacheSizeGB ${WT_CACHE_GB} --setParameter maxIndexBuildMemoryUsageMegabytes=100"

echo "Waiting for MongoDB on 127.0.0.1:${PORT}..."
for _ in $(seq 1 60); do
  if docker exec "$NAME" mongosh -u "$ROOT_USER" -p "$ROOT_PASS" --authenticationDatabase admin --quiet --eval "db.adminCommand('ping').ok" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker exec "$NAME" mongosh -u "$ROOT_USER" -p "$ROOT_PASS" --authenticationDatabase admin --quiet --eval \
  "try { rs.status().ok } catch(e) { rs.initiate({_id:'rs0', members:[{_id:0, host:'127.0.0.1:27017'}]}) }"

for _ in $(seq 1 60); do
  PRIMARY="$(docker exec "$NAME" mongosh -u "$ROOT_USER" -p "$ROOT_PASS" --authenticationDatabase admin --quiet --eval 'db.isMaster().ismaster')"
  if [ "$PRIMARY" = "true" ]; then
    echo "Test MongoDB ready: mongodb://${ROOT_USER}:***@127.0.0.1:${PORT}/?authSource=admin&replicaSet=rs0"
    exit 0
  fi
  sleep 1
done

echo "Failed to elect PRIMARY" >&2
exit 1
