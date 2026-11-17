#!/bin/bash
# Verify named volume persists across Mongo container restart (development only).
# Does NOT delete volumes or reset the database.
set -euo pipefail

ROOT_USER="${MONGO_ROOT_USERNAME:-eternal}"
ROOT_PASS="${MONGO_ROOT_PASSWORD:-eternal_dev_password}"
MARKER="eternal-v4-persist-$(date +%s)"

echo "Ensuring compose Mongo is up (volume kept)..."
docker compose up -d mongo

echo "Waiting for healthy/PRIMARY..."
ok=0
for _ in $(seq 1 30); do
  st="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q mongo)" 2>/dev/null || echo starting)"
  if [ "$st" = "healthy" ]; then
    ok=1
    break
  fi
  sleep 2
done
if [ "$ok" != "1" ]; then
  echo "FAIL: mongo not healthy" >&2
  exit 1
fi

docker compose exec -T mongo mongosh -u "$ROOT_USER" -p "$ROOT_PASS" --authenticationDatabase admin --quiet --eval \
  "db.getSiblingDB('eternal_persist_check').markers.insertOne({_id: '${MARKER}', at: new Date()})"

echo "Restarting mongo container (named volume retained)..."
docker compose restart mongo

ok=0
for _ in $(seq 1 30); do
  st="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q mongo)" 2>/dev/null || echo starting)"
  if [ "$st" = "healthy" ]; then
    ok=1
    break
  fi
  sleep 2
done
if [ "$ok" != "1" ]; then
  echo "FAIL: mongo not healthy after restart" >&2
  exit 1
fi

FOUND="$(docker compose exec -T mongo mongosh -u "$ROOT_USER" -p "$ROOT_PASS" --authenticationDatabase admin --quiet --eval \
  "db.getSiblingDB('eternal_persist_check').markers.findOne({_id: '${MARKER}'})?._id")"

if [ "$FOUND" != "$MARKER" ]; then
  echo "FAIL: marker not found after restart" >&2
  exit 1
fi

docker compose exec -T mongo mongosh -u "$ROOT_USER" -p "$ROOT_PASS" --authenticationDatabase admin --quiet --eval \
  "db.getSiblingDB('eternal_persist_check').markers.deleteOne({_id: '${MARKER}'})" >/dev/null
echo "PASS: Mongo persistence after restart"
