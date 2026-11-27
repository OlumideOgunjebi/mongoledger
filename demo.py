# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    demo.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: two minute live demonstration: clean run, verification and three
#          attacks
# Covers:  FR26; acceptance test AC14 (see docs/D.4.1)
# ============================================================================
"""Two minute end to end demonstration on a live replica set.

  docker compose up -d mongo
  python demo.py                      (uses MONGO_URI, default localhost replica set)

Steps: load 1,000 SmallBank customers, set up both ledgers, run 20 seconds of
SmallBank through the Design S library with the Design A service and the
Design S folder running, verify both ledgers, then run three attacks (a write
that bypasses the library, an edit of a sealed ledger entry, a ledger rollback)
and print what each design detects. The output ends with a short summary of
the run. Everything happens in the database "demo_bank".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from pymongo import DESCENDING, MongoClient

from ledger.cli import DEFAULT_URI, run_verify
from ledger.design_s import SyncLedger
from ledger.mongo_store import LedgerStore
from workloads.smallbank import MongoBank, run

URI = os.environ.get("MONGO_URI", DEFAULT_URI)
DB = "demo_bank"
CUSTOMERS, CLIENTS, SECONDS, INTERVAL = 1000, 8, 20, 2.0


def cli(*args):
    """Build the argument list that runs one ``mongoledger`` command on the demo database.

    The command runs as a separate process (``python -m ledger.cli``), exactly as an
    operator would run it, so the demonstration exercises the real command line.
    """
    return [sys.executable, "-m", "ledger.cli", "--uri", URI, "--data-db", DB, "--collections", "accounts", *args]


def show(title, report):
    """Print one verification result on a single line and return its alerts.

    Args:
        title: label printed before the result.
        report: verifier Report returned by ``run_verify``.

    Returns:
        Mapping of alert kind to count; empty when the ledger verified clean.
    """
    alerts = dict(report.kinds("alert"))
    print(f"  {title:44s} {'clean' if report.ok else 'ALERT ' + json.dumps(alerts)}")
    return alerts


def main() -> int:
    """Run the six steps of the demonstration and print a summary (FR26, AC14).

    Steps 4 to 6 are attacks that are expected to raise alerts, so the result of
    each step is printed rather than turned into the exit code; 0 means the
    script itself completed.
    """
    import shutil
    c = MongoClient(URI)
    # start clean: drop the demo data, both ledgers and old digest logs
    for d in (DB, "ledger_a", "ledger_s"):
        c.drop_database(d)
    shutil.rmtree("digests", ignore_errors=True)
    print(f"1. loading {CUSTOMERS} customers into {DB}.accounts")
    MongoBank(c, DB).load(CUSTOMERS)
    # baselines for both designs, 4 Design S chain heads
    subprocess.run(cli("setup", "--heads", "4"), check=True)

    print(f"2. SmallBank through Design S for {SECONDS}s with {CLIENTS} clients; Design A service running")
    # Design A service and Design S folder run as separate processes, as in production
    svc = subprocess.Popen(cli("service", "--interval", str(INTERVAL)))
    fold = subprocess.Popen(cli("fold", "--heads", "4", "--every", str(INTERVAL)))
    ledger = SyncLedger(c, DB, "ledger_s", heads=4)
    # 8 client threads issue SmallBank transactions through the Design S library
    res = run(lambda: MongoBank(c, DB, mode="S", ledger=ledger), CUSTOMERS, CLIENTS, SECONDS, warmup=2)
    # give both ledger processes time to seal and publish the last block
    time.sleep(INTERVAL + 3)
    print(f"   {res['throughput_ops_s']} transactions/s, p95 {res['p95_ms']} ms, errors {res['errors']}")

    print("3. verification after the clean run")
    ra, rs = run_verify(c, DB, ["accounts"], "A"), run_verify(c, DB, ["accounts"], "S")
    show("Design A", ra)
    show("Design S", rs)
    blocks, _ = LedgerStore(c, "ledger_a").load()
    # entry weighted mean of the commit to block lag over every Design A block
    lags = [b for b in blocks if b.lag_mean is not None]
    lag = sum(b.lag_mean * b.entry_count for b in lags) / max(1, sum(b.entry_count for b in lags))

    print("4. attack: write that bypasses the Design S library")
    # an administrator changes a balance directly, without the library
    victim = c[DB]["accounts"].find_one(sort=[("_id", DESCENDING)])["_id"]
    c[DB]["accounts"].update_one({"_id": victim}, {"$inc": {"checking": 100_000}})
    time.sleep(INTERVAL + 2)
    a1 = show("Design A (with cross check against S)", run_verify(c, DB, ["accounts"], "A", cross=True))
    s1 = show("Design S", run_verify(c, DB, ["accounts"], "S"))

    print("5. attack: edit a sealed ledger entry in each design")
    # an administrator edits the newest sealed entry of each ledger
    for d in ("a", "s"):
        e = c[f"ledger_{d}"]["entries"].find_one({"block_id": {"$gte": 1}}, sort=[("block_id", DESCENDING)])
        c[f"ledger_{d}"]["entries"].update_one({"_id": e["_id"]}, {"$set": {"post_hash": "f" * 64}})
    a2 = show("Design A", run_verify(c, DB, ["accounts"], "A"))
    s2 = show("Design S", run_verify(c, DB, ["accounts"], "S"))

    print("6. attack: roll the Design A ledger back by deleting its newest block")
    # an administrator deletes the newest block and its entries to hide recent history
    last = LedgerStore(c, "ledger_a").last_block().block_id
    c["ledger_a"]["blocks"].delete_many({"block_id": last})
    c["ledger_a"]["entries"].delete_many({"block_id": last})
    a3 = show("Design A", run_verify(c, DB, ["accounts"], "A"))

    svc.terminate()
    fold.terminate()
    print("\nSummary of the run")
    print(f"  SmallBank via Design S, {CLIENTS} clients, {CUSTOMERS} customers: {res['throughput_ops_s']} tx/s, "
          f"p95 {res['p95_ms']} ms")
    print(f"  Design A mean commit to block lag at {INTERVAL}s interval: {lag:.3f} s over {len(blocks)} blocks")
    print(f"  Clean run verified: A {'yes' if ra.ok else 'no'}, S {'yes' if rs.ok else 'no'} "
          f"(verify time A {ra.stats['verify_seconds']} s, S {rs.stats['verify_seconds']} s)")
    print(f"  Bypass write: A {sorted(a1) or 'recorded, no alert'}, S {sorted(s1)}")
    print(f"  Edited sealed entry: A {sorted(a2)}, S {sorted(s2)}")
    print(f"  Ledger rollback: A {sorted(a3)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
