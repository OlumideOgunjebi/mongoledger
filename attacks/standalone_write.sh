#!/usr/bin/env bash
# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    attacks/standalone_write.sh
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: attack 2: restart mongod outside replica set mode and change a
#          document without an oplog entry
# Covers:  FR25; acceptance test AC13 (see docs/D.4.1)
# ============================================================================

# Attack: restart MongoDB outside replica set mode, change a protected document,
# then restart it normally. Writes made while standalone are not replicated, and
# this attack tests whether they ever reach the change stream.
#
#   bash attacks/standalone_write.sh [document_id]
#
# Expected: the change stream never reports the write, so Design A records no
# entry, but the next verify reports modified_document for both designs.
set -euo pipefail
DOC_ID="${1:-1}"

echo "stopping the replica set member"
docker compose stop mongo

echo "starting the same data directory as a standalone server on port 27018"
docker compose run --rm --no-deps -d --name mongo_standalone -p 27018:27017 mongo \
  mongod --dbpath /data/db --port 27017 --bind_ip_all
sleep 8

echo "changing bank.accounts document ${DOC_ID}"
docker exec mongo_standalone mongosh --quiet --eval \
  "db.getSiblingDB('bank').accounts.updateOne({_id: ${DOC_ID}}, {\$inc: {checking: 4242}})"

echo "restarting as a replica set member"
docker stop mongo_standalone >/dev/null
docker compose start mongo
sleep 10

mkdir -p results
python -c "import json, time; open('results/attacks.jsonl', 'a').write(json.dumps({'at': time.time(), 'kind': 'attack', 'name': 'standalone_write', 'doc': ${DOC_ID}}) + '\n')"
echo "done; now run: python -m attacks.run_attacks report"
