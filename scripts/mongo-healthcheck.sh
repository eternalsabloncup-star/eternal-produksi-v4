#!/bin/bash
# Runs INSIDE the mongo container on localhost (avoids cross-container mongosh hang).
set -euo pipefail

ROOT_USER="${MONGO_INITDB_ROOT_USERNAME:-eternal}"
ROOT_PASS="${MONGO_INITDB_ROOT_PASSWORD:-eternal_dev_password}"
RS_HOST="${MONGO_RS_HOST:-mongo:27017}"
URI="mongodb://${ROOT_USER}:${ROOT_PASS}@127.0.0.1:27017/admin?authSource=admin&directConnection=true&serverSelectionTimeoutMS=3000&connectTimeoutMS=3000"

mongosh "$URI" --quiet --eval "db.adminCommand('ping').ok" >/dev/null

STATUS="$(mongosh "$URI" --quiet --eval 'try { rs.status().ok } catch (e) { 0 }' || echo 0)"
if [ "$STATUS" != "1" ]; then
  mongosh "$URI" --quiet --eval "rs.initiate({_id: 'rs0', members: [{_id: 0, host: '${RS_HOST}'}]})" >/dev/null
  exit 1
fi

IS_MASTER="$(mongosh "$URI" --quiet --eval 'db.isMaster().ismaster' || true)"
if [ "$IS_MASTER" = "true" ]; then
  exit 0
fi
exit 1
