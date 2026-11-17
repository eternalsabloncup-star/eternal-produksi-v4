#!/bin/bash
set -euo pipefail

# Prepare replica-set keyfile (dev only — never reuse in production).
mkdir -p /data/configdb
cp /tmp/mongo-dev.key /data/configdb/keyfile
chmod 400 /data/configdb/keyfile
chown mongodb:mongodb /data/configdb/keyfile

exec docker-entrypoint.sh "$@"
