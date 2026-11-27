# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/test_smallbank.py
# Owner:   Yadu Pratap (deployment lead)
# Purpose: tests of the SmallBank workload in plain and Design S modes
# Covers:  AC12 (see docs/D.4.1)
# ============================================================================
"""SmallBank operations through both MongoDB modes, verified by both ledgers."""

import random

from fake_mongo import FakeClient
from ledger.blocks import build_block
from ledger.design_a import EventProcessor
from ledger.design_s import SyncLedger
from ledger.digest_log import DigestLog
from ledger.model import GENESIS
from ledger.mongo_store import LedgerStore
from ledger.scan import baseline_entries_for_a, scan_state
from ledger.verifier import verify
from workloads.smallbank import MIX, START_BALANCE, MongoBank

N = 20


def _drive(bank, rng, steps=300):
    """Issue ``steps`` random SmallBank transactions with a fixed seed."""
    for _ in range(steps):
        op = rng.choice(list(MIX))
        c1 = rng.randrange(N)
        c2 = (c1 + 1 + rng.randrange(N - 1)) % N
        getattr(bank, op)(c1=c1, c2=c2, v=rng.randrange(1, 5000), c=c1)


def test_smallbank_conserves_money_and_verifies(tmp_path):
    """AC12: SmallBank through Design S conserves money and verifies for both designs."""
    c = FakeClient(watched=("bank",))
    MongoBank(c, "bank").load(N)
    c.events.clear()
    s = SyncLedger(c, "bank", "ledger_s", heads=4)
    s.setup()
    store_s, log_s = LedgerStore(c, "ledger_s"), DigestLog(str(tmp_path / "s.jsonl"))
    s.baseline(["accounts"])
    s.fold(store_s, log_s)
    store_a, log_a = LedgerStore(c, "ledger_a"), DigestLog(str(tmp_path / "a.jsonl"))
    base = baseline_entries_for_a(c, "bank", ["accounts"])
    b0 = build_block(0, GENESIS, base, 0.0)
    store_a.append_block(b0, base)
    log_a.publish(b0)
    proc = EventProcessor(interval=0, next_block_id=1, prev_hash=b0.block_hash)
    c.events.clear()

    _drive(MongoBank(c, "bank", mode="S", ledger=s), random.Random(1))
    for e in c.events:
        proc.consume(e)
    store_a.append_block(*proc.seal())
    log_a.publish(store_a.last_block())
    s.fold(store_s, log_s)

    actual = scan_state(c, "bank", ["accounts"])
    ns = {"bank.accounts"}
    blocks, by = store_a.load()
    assert verify(blocks, by, log_a.read(), actual, namespaces=ns).ok
    blocks, by = store_s.load()
    assert verify(blocks, by, log_s.read(), actual, pending=s.pending_entries(), heads=s.head_state(), namespaces=ns).ok
    # money only moves between accounts, except deposits, savings transactions and check penalties
    total = sum(d["savings"] + d["checking"] for d in c["bank"]["accounts"].find({}))
    assert total != 0 and len(actual) == N


def test_plain_mode_runs_without_ledger():
    """The plain mode runs without any ledger."""
    c = FakeClient(watched=())
    bank = MongoBank(c, "bank")
    bank.load(N)
    _drive(bank, random.Random(2), steps=100)
    assert bank.balance(c=0) >= -10_000_000
    assert c["bank"]["accounts"].count_documents({}) == N
    assert START_BALANCE == 1_000_000
