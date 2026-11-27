#!/usr/bin/env bash
# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ycsb/run_ycsb.sh
# Owner:   Yadu Pratap (deployment lead)
# Purpose: YCSB workloads A and B on MongoDB and SQL Server
# Covers:  FR22; acceptance test AC12 (see docs/D.4.1)
# ============================================================================

# bash ycsb/run_ycsb.sh <mongo|mongo-a|sql> <a|b> [plain|ledger]
# Environment: YCSB_HOME (default ../ycsb-0.17.0), THREADS (16), RECORDS (1000000),
#              SECONDS_RUN (60), MSSQL_SA_PASSWORD, INTERVAL (5)
set -euo pipefail
TARGET="$1"; WL="$2"; TABLE_KIND="${3:-plain}"
YCSB_HOME="${YCSB_HOME:-../ycsb-0.17.0}"
THREADS="${THREADS:-16}"; RECORDS="${RECORDS:-1000000}"; SECONDS_RUN="${SECONDS_RUN:-60}"; INTERVAL="${INTERVAL:-5}"
OUT="results/ycsb"; mkdir -p "$OUT"
TAG="${TARGET}_${TABLE_KIND}_w${WL}_t${THREADS}_$(date +%Y%m%d_%H%M%S)"
COMMON=(-P "$YCSB_HOME/workloads/workload${WL}" -p recordcount="$RECORDS" -p operationcount=1000000000
        -p maxexecutiontime="$SECONDS_RUN" -threads "$THREADS" -s)

case "$TARGET" in
  mongo|mongo-a)
    URL="mongodb://localhost:27017/ycsb?replicaSet=rs0&directConnection=true&w=majority"
    docker compose exec -T mongo mongosh --quiet --eval "db.getSiblingDB('ycsb').dropDatabase(); db.getSiblingDB('ledger_a').dropDatabase()"
    rm -f digests/design_a.jsonl
    "$YCSB_HOME/bin/ycsb.sh" load mongodb "${COMMON[@]}" -p mongodb.url="$URL" > "$OUT/${TAG}_load.txt"
    SVC=""
    if [ "$TARGET" = "mongo-a" ]; then
      python -m ledger.cli --data-db ycsb --collections usertable setup
      python -m ledger.cli --data-db ycsb --collections usertable service --interval "$INTERVAL" &
      SVC=$!
    fi
    "$YCSB_HOME/bin/ycsb.sh" run mongodb "${COMMON[@]}" -p mongodb.url="$URL" > "$OUT/${TAG}_run.txt"
    if [ -n "$SVC" ]; then sleep $((${INTERVAL%.*} + 3)); kill "$SVC"; fi
    ;;
  sql)
    TABLE="usertable_${TABLE_KIND}"
    JDBC=(-p db.driver=com.microsoft.sqlserver.jdbc.SQLServerDriver
          -p "db.url=jdbc:sqlserver://localhost:1433;databaseName=LedgerBench;encrypt=false"
          -p db.user=sa -p "db.passwd=${MSSQL_SA_PASSWORD}" -p table="$TABLE" -p jdbc.autocommit=true)
    # empty the table first so the load does not collide with an earlier run
    python - "$TABLE" <<'PY'
import sys, pyodbc
from sqlserver.ledger_baseline import conn_str
pyodbc.connect(conn_str(), autocommit=True).execute(f"DELETE FROM dbo.{sys.argv[1]}")
PY
    "$YCSB_HOME/bin/ycsb.sh" load jdbc "${COMMON[@]}" "${JDBC[@]}" > "$OUT/${TAG}_load.txt"
    "$YCSB_HOME/bin/ycsb.sh" run jdbc "${COMMON[@]}" "${JDBC[@]}" > "$OUT/${TAG}_run.txt"
    ;;
  *) echo "unknown target $TARGET"; exit 1 ;;
esac
grep -E "Throughput|\[UPDATE\], 95th|\[READ\], 95th" "$OUT/${TAG}_run.txt" || true
