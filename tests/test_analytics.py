# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/test_analytics.py
# Owner:   Yadu Pratap (deployment lead)
# Purpose: tests of the sample_analytics dataset and trading workload
# Covers:  AC12 (see docs/D.4.1)
# ============================================================================
"""sample_analytics dataset and trading workload through both designs (FR23, AC12)."""

import gzip
import random

import pytest

import ledger.cli as cli
from fake_mongo import FakeClient
from ledger.design_a import run_service
from ledger.design_s import SyncLedger
from ledger.digest_log import DigestLog
from ledger.mongo_store import LedgerStore
from workloads.analytics import BUCKET_SIZE, COLLECTIONS, DATASET_DIR, MIX, AnalyticsApp, load, read_dataset

ARGS = ["--data-db", "analytics", "--collections", ",".join(COLLECTIONS)]


def test_dataset_files_are_complete():
    """AC12: the three sample_analytics files are present with the expected document counts."""
    assert sum(1 for _ in read_dataset("accounts")) == 1746
    assert sum(1 for _ in read_dataset("customers")) == 500
    with gzip.open(DATASET_DIR / "transactions.json.gz", "rt") as fh:
        assert sum(1 for _ in fh) == 1746
    first = next(read_dataset("transactions"))
    assert {"account_id", "transaction_count", "bucket_start_date", "transactions"} <= set(first)
    assert first["transaction_count"] == len(first["transactions"])


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Load sample_analytics into the in memory server and set up both ledgers."""
    monkeypatch.chdir(tmp_path)
    c = FakeClient(watched=("analytics",))
    counts = load(c, "analytics", limit=40)
    assert counts == {"accounts": 40, "customers": 40, "transactions": 40}
    monkeypatch.setattr(cli, "_client", lambda args: c)
    assert cli.main(ARGS + ["setup", "--heads", "4"]) == 0
    return c


def _drive(app, n, steps=150, seed=3):
    """Issue ``steps`` random trading operations through ``app`` with a fixed seed."""
    rng = random.Random(seed)
    names, weights = zip(*MIX.items())
    for _ in range(steps):
        c1 = rng.randrange(n)
        getattr(app, rng.choices(names, weights)[0])(c1=c1, c2=(c1 + 1) % n, v=rng.randrange(1, 10_000), c=c1)


def test_workload_through_design_s_verifies_for_both_designs(env):
    """AC12: the trading workload through Design S leaves both ledgers clean."""
    c = env
    s = SyncLedger(c, "analytics", "ledger_s", heads=4)
    app = AnalyticsApp(c, "analytics", mode="S", ledger=s)
    _drive(app, len(app.ids))
    s.fold(LedgerStore(c, "ledger_s"), DigestLog("digests/design_s.jsonl"))
    # a larger interval batches the many events into few blocks (each digest is fsynced)
    run_service(c, "analytics", LedgerStore(c, "ledger_a"), DigestLog("digests/design_a.jsonl"),
                interval=0.05, max_seconds=1.5, idle_sleep=0.0)
    cols = list(COLLECTIONS)
    ra = cli.run_verify(c, "analytics", cols, "A", cross=True)
    rs = cli.run_verify(c, "analytics", cols, "S")
    assert ra.ok, ra.to_dict()["findings"][:3]
    assert rs.ok, rs.to_dict()["findings"][:3]
    assert ra.stats["entries"] > 40 * 3  # baseline plus the ledgered changes


def test_plain_writes_are_caught_by_design_s_and_recorded_by_design_a(env):
    """AC04: plain writes are recorded by Design A and reported as alerts by Design S."""
    c = env
    app = AnalyticsApp(c, "analytics")  # unprotected application
    app.add_product(c1=0, v=4)
    app.trade(c1=1, v=7)
    run_service(c, "analytics", LedgerStore(c, "ledger_a"), DigestLog("digests/design_a.jsonl"),
                interval=0.0, max_seconds=0.3, idle_sleep=0.0)
    cols = list(COLLECTIONS)
    assert cli.run_verify(c, "analytics", cols, "A").ok
    assert not cli.run_verify(c, "analytics", cols, "S").ok
    assert cli.run_verify(c, "analytics", cols, "A", cross=True).kinds("alert")["write_outside_library"] >= 1


def test_trade_opens_a_new_bucket_when_full(env):
    """A trade opens a new transaction bucket once the current one is full."""
    c = env
    app = AnalyticsApp(c, "analytics")
    aid = app.ids[0]
    c["analytics"]["transactions"].update_one({"account_id": aid}, {"$set": {"transaction_count": BUCKET_SIZE}})
    before = c["analytics"]["transactions"].count_documents({"account_id": aid})
    app.trade(c1=0, v=11)
    assert c["analytics"]["transactions"].count_documents({"account_id": aid}) == before + 1
    app.trade(c1=0, v=12)  # goes into the new, open bucket
    assert c["analytics"]["transactions"].count_documents({"account_id": aid}) == before + 1
    cust, acct, newest = app.portfolio(c1=0)
    assert acct["account_id"] == aid and newest is not None
