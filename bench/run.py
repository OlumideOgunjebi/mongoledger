# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    bench/run.py
# Owner:   Yadu Pratap (deployment lead)
# Purpose: experiment harness: one summary row per run in results/summary.csv
# Covers:  FR24; acceptance tests AC12, AC15 (see docs/D.4.1)
# ============================================================================
"""SmallBank experiment harness.

  python -m bench.run --target mongo-plain --clients 1 4 16 32 --seconds 60 --repeats 5
  python -m bench.run --target mongo-a --interval 1 5 30 --clients 16
  python -m bench.run --target mongo-s --heads 1 4 16 --clients 1 4 16 32
  python -m bench.run --target sql-plain sql-ledger --clients 1 4 16 32
  python -m bench.run --workload analytics --target mongo-plain mongo-a mongo-s --clients 8

Every run resets its data, loads the customers, starts the ledger process the
target needs (the Design A service or the Design S folder, each as a separate
process), measures a closed loop SmallBank run, then verifies the ledger. One
summary row per run is appended to results/summary.csv; raw latencies go to
results/raw/.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient

from ledger.cli import DEFAULT_URI, run_verify
from ledger.design_s import SyncLedger
from ledger.digest_log import DigestLog
from ledger.mongo_store import LedgerStore
from workloads import analytics
from workloads.smallbank import MongoBank, SqlBank, run

RESULTS = Path("results")
SUMMARY = RESULTS / "summary.csv"
FIELDS = ["run_id", "workload", "target", "clients", "heads", "interval", "repeat", "customers", "seconds", "ops",
          "throughput_ops_s", "p50_ms", "p95_ms", "p99_ms", "mean_ms", "errors", "lag_mean_s", "lag_max_s",
          "blocks", "verify_ok", "verify_seconds", "ledger_mb", "data_mb", "started_at"]


def _db_mb(client, name: str) -> float:
    """Storage size of a database in megabytes (0 if unavailable)."""
    try:
        return round(client[name].command("dbStats")["storageSize"] / 1e6, 2)
    except Exception:
        return 0.0


def reset_mongo(client, data_db: str) -> None:
    """Drop the data and both ledger databases and delete old digest logs."""
    for db in (data_db, "ledger_a", "ledger_s"):
        client.drop_database(db)
    shutil.rmtree("digests", ignore_errors=True)


def ledger_cli(*args: str) -> list[str]:
    """Command line for a ledger subprocess, using the same Python interpreter."""
    return [sys.executable, "-m", "ledger.cli", *args]


def one_run(a, target: str, clients: int, heads: int, interval: float, repeat: int) -> dict:
    """Reset, load, start the ledger process, measure, stop it and verify; returns one summary row."""
    uri, data_db = a.uri, a.data_db
    is_analytics = a.workload == "analytics"
    colls = list(analytics.COLLECTIONS) if is_analytics else ["accounts"]
    row = {"workload": a.workload, "target": target, "clients": clients, "heads": heads if target == "mongo-s" else "",
           "interval": interval if target in ("mongo-a", "mongo-s") else "", "repeat": repeat,
           "customers": a.customers, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # proc is the ledger process that runs beside the workload (service or folder), if any
    proc = None
    if target.startswith("mongo"):
        client = MongoClient(uri)
        # every run starts from fresh data and empty ledgers, so runs are independent
        reset_mongo(client, data_db)
        if is_analytics:
            analytics.load(client, data_db)
        else:
            MongoBank(client, data_db).load(a.customers)
        # arguments shared by every ledger subprocess for this run
        common = ["--uri", uri, "--data-db", data_db, "--collections", ",".join(colls)]
        # Design A: the application writes directly; a separate service reads the change stream
        if target == "mongo-a":
            subprocess.run(ledger_cli(*common, "setup"), check=True, stdout=subprocess.DEVNULL)
            proc = subprocess.Popen(ledger_cli(*common, "service", "--interval", str(interval)))
            make = (lambda: analytics.AnalyticsApp(client, data_db)) if is_analytics else (lambda: MongoBank(client, data_db))
        # Design S: setup creates k heads, a folder seals blocks, and every write goes through the library
        elif target == "mongo-s":
            subprocess.run(ledger_cli(*common, "setup", "--heads", str(heads)), check=True, stdout=subprocess.DEVNULL)
            proc = subprocess.Popen(ledger_cli(*common, "fold", "--heads", str(heads), "--every", str(interval)))
            ledger = SyncLedger(client, data_db, "ledger_s", heads=heads)
            make = ((lambda: analytics.AnalyticsApp(client, data_db, mode="S", ledger=ledger)) if is_analytics
                    else (lambda: MongoBank(client, data_db, mode="S", ledger=ledger)))
        else:
            make = (lambda: analytics.AnalyticsApp(client, data_db)) if is_analytics else (lambda: MongoBank(client, data_db))
    else:
        # SQL Server: the same SmallBank operations on a plain table or an updatable ledger table
        from sqlserver.ledger_baseline import conn_str, connect, generate_digest
        table = "dbo.bank_ledger" if target == "sql-ledger" else "dbo.bank_plain"
        SqlBank(conn_str(), table).load(a.customers)
        if target == "sql-ledger":
            shutil.rmtree("digests", ignore_errors=True)
            # first digest before the run, then one every interval from a separate process
            generate_digest(connect())
            proc = subprocess.Popen([sys.executable, "-m", "sqlserver.ledger_baseline", "digest",
                                     "--every", str(interval), "--seconds", str(a.seconds + a.warmup + 30)])
        make = lambda: SqlBank(conn_str(), table)

    raw = RESULTS / "raw" / f"{a.workload}_{target}_c{clients}_h{heads}_i{interval}_r{repeat}.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    # sample_analytics picks among its accounts; SmallBank among --customers customers
    population = len(make().ids) if is_analytics and target.startswith("mongo") else a.customers
    row["customers"] = population
    mix = analytics.MIX if is_analytics else None
    row.update(run(make, population, clients, a.seconds, seed=repeat, raw_csv=str(raw), warmup=a.warmup,
                   **({"mix": mix} if mix else {})))
    # measurements end here; the rest of the run checks what the ledger recorded
    time.sleep(interval + 2)  # let the last block seal and its digest publish
    if proc:
        proc.terminate()
        proc.wait(timeout=30)

    # commit to block lag, ledger size and verification time come from the stored ledger
    if target.startswith("mongo"):
        design = "A" if target == "mongo-a" else ("S" if target == "mongo-s" else None)
        row["data_mb"] = _db_mb(client, data_db)
        if design:
            if design == "S":  # fold anything committed after the folder stopped
                SyncLedger(client, data_db, "ledger_s", heads=heads).fold(
                    LedgerStore(client, "ledger_s"), DigestLog("digests/design_s.jsonl"))
            store = LedgerStore(client, f"ledger_{design.lower()}")
            blocks, _ = store.load()
            measured = [b for b in blocks if b.lag_mean is not None]
            if measured:
                total = sum(b.entry_count for b in measured)
                row["lag_mean_s"] = round(sum(b.lag_mean * b.entry_count for b in measured) / total, 4)
                row["lag_max_s"] = round(max(b.lag_max for b in measured), 4)
            row["blocks"] = len(blocks)
            report = run_verify(client, data_db, colls, design)
            row["verify_ok"] = report.ok
            row["verify_seconds"] = report.stats["verify_seconds"]
            row["ledger_mb"] = _db_mb(client, f"ledger_{design.lower()}")
    elif target == "sql-ledger":
        # SQL Server verifies its own ledger against every digest taken during the run
        from sqlserver.ledger_baseline import connect, generate_digest, verify
        conn = connect()
        generate_digest(conn)
        ok, _, secs = verify(conn)
        row["verify_ok"], row["verify_seconds"] = ok, round(secs, 4)
    return row


def main(argv=None) -> int:
    """Run every combination of target, heads, interval, clients and repeat."""
    p = argparse.ArgumentParser(prog="bench.run")
    p.add_argument("--target", nargs="+", required=True,
                   choices=["mongo-plain", "mongo-a", "mongo-s", "sql-plain", "sql-ledger"])
    p.add_argument("--clients", nargs="+", type=int, default=[1, 4, 16, 32])
    p.add_argument("--heads", nargs="+", type=int, default=[1])
    p.add_argument("--interval", nargs="+", type=float, default=[5.0])
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--warmup", type=float, default=10)
    p.add_argument("--customers", type=int, default=100_000)
    p.add_argument("--uri", default=os.environ.get("MONGO_URI", DEFAULT_URI))
    p.add_argument("--data-db", default=None, help="default: bank for SmallBank, analytics for sample_analytics")
    p.add_argument("--workload", choices=["smallbank", "analytics"], default="smallbank")
    a = p.parse_args(argv)
    a.data_db = a.data_db or os.environ.get("DATA_DB") or ("analytics" if a.workload == "analytics" else "bank")
    if a.workload == "analytics" and any(t.startswith("sql") for t in a.target):
        p.error("the analytics workload runs on MongoDB targets only")

    # append to the summary so repeated invocations accumulate one table of results
    RESULTS.mkdir(exist_ok=True)
    new = not SUMMARY.exists()
    with open(SUMMARY, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for target in a.target:
            heads = a.heads if target == "mongo-s" else [1]
            intervals = a.interval if target in ("mongo-a", "mongo-s", "sql-ledger") else [0]
            for h, i, c, r in itertools.product(heads, intervals, a.clients, range(1, a.repeats + 1)):
                row = one_run(a, target, c, h, i, r)
                row["run_id"] = f"{target}-c{c}-h{h}-i{i}-r{r}-{int(time.time())}"
                w.writerow(row)
                fh.flush()
                print(f"{target:12s} clients={c:<3d} heads={h:<3d} interval={i:<5} repeat={r}: "
                      f"{row['throughput_ops_s']} ops/s, p95 {row['p95_ms']} ms, verify_ok={row.get('verify_ok', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
