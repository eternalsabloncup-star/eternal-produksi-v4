#!/bin/bash
# Manual/optional RS init helper.
# Prefer compose healthcheck (scripts/mongo-healthcheck.sh) which runs on localhost
# inside the mongo container. Cross-container mongosh to hostname `mongo` can hang.
set -euo pipefail

ROOT_USER="${MONGO_ROOT_USERNAME:-eternal}"
ROOT_PASS="${MONGO_ROOT_PASSWORD:-eternal_dev_password}"
RS_HOST="${MONGO_RS_HOST:-mongo:27017}"

echo "Initiating/checking replica set via docker compose exec (localhost inside mongo)..."
docker compose exec -T mongo bash -lc \
  "MONGO_INITDB_ROOT_USERNAME='${ROOT_USER}' MONGO_INITDB_ROOT_PASSWORD='${ROOT_PASS}' MONGO_RS_HOST='${RS_HOST}' bash /mongo-healthcheck.sh"
echo "mongo-healthcheck exit=$?"
