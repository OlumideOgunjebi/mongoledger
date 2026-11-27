#!/usr/bin/env bash
# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    scripts/health_check.sh
# Owner:   Yadu Pratap (deployment lead)
# Purpose: post deployment health check on Linux and macOS
# Covers:  NFR07, NFR10 (see docs/D.4.1)
# ============================================================================

# Post deployment health check (bash scripts/health_check.sh [A|S]).
#
# Prints PASS or FAIL (or SKIP) for each check and exits 1 if any check fails:
#   1. the mongo container is healthy            (skipped where Docker is not available, e.g. inside Codespaces)
#   2. the server is a writable replica set PRIMARY (transactions and change streams need it)
#   3. before and after images are enabled on the first protected collection
#   4. the ledger has blocks
#   5. every published digest still matches its block
#   6. verify reports no alert
# Environment: MONGO_URI, DATA_DB (default bank), COLLECTIONS (default accounts).
set -uo pipefail
cd "$(dirname "$0")/.."
DESIGN="${1:-A}"
export DB="${DATA_DB:-bank}"
COLLS="${COLLECTIONS:-accounts}"
export FIRST="${COLLS%%,*}"
export MONGO_URI="${MONGO_URI:-mongodb://localhost:27017/?replicaSet=rs0&directConnection=true}"
PY="$(command -v python || command -v python3)"
fail=0
check() { if eval "$2" >/dev/null 2>&1; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }

if command -v docker >/dev/null 2>&1 && [ -n "$(docker compose ps -q mongo 2>/dev/null)" ]; then
  check "mongo container healthy" "[ \"\$(docker inspect -f '{{.State.Health.Status}}' \$(docker compose ps -q mongo))\" = healthy ]"
else
  echo "SKIP  mongo container healthy (Docker not available here)"
fi
check "server is a writable replica set PRIMARY" \
  "$PY -c \"import os,pymongo; h=pymongo.MongoClient(os.environ['MONGO_URI'], serverSelectionTimeoutMS=3000).admin.command('hello'); assert h.get('isWritablePrimary') and h.get('setName')\""
check "before and after images enabled on $DB.$FIRST" \
  "$PY -c \"import os,pymongo; c=pymongo.MongoClient(os.environ['MONGO_URI'], serverSelectionTimeoutMS=3000); i=next(c[os.environ['DB']].list_collections(filter={'name': os.environ['FIRST']})); assert i['options']['changeStreamPreAndPostImages']['enabled']\""
check "ledger $DESIGN has blocks" "$PY -m ledger.cli --data-db $DB --collections $COLLS blocks --design $DESIGN --json | grep -q '\"block\"'"
check "every published digest matches (design $DESIGN)" "$PY -m ledger.cli --data-db $DB --collections $COLLS digests --design $DESIGN"
check "verify reports no alert (design $DESIGN)" "$PY -m ledger.cli --data-db $DB --collections $COLLS verify --design $DESIGN"
exit $fail
