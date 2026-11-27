# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/integration/test_live_mongo.py
# Owner:   All members (shared test support)
# Purpose: integration tests against a live MongoDB replica set
# Covers:  AC01, AC02, AC06, AC16 (see docs/D.4.1)
# ============================================================================
"""Integration tests against a real replica set (skipped unless MONGO_URI is set).

  docker compose up -d mongo
  MONGO_URI="mongodb://localhost:27017/?replicaSet=rs0&directConnection=true" pytest tests/integration -v

They use the databases it_bank, it_ledger_a and it_ledger_s and drop them first.
"""

import os
import threading
import time

import pytest

URI = os.environ.get("MONGO_URI")
pytestmark = pytest.mark.skipif(not URI, reason="MONGO_URI not set; start docker compose and export it")

DB = "it_bank"


@pytest.fixture
def env(tmp_path):
    """Fresh data and ledger databases on the live replica set, plus a temporary digest folder."""
    from pymongo import MongoClient

    from ledger.blocks import build_block
    from ledger.design_a import enable_images
    from ledger.design_s import SyncLedger
    from ledger.digest_log import DigestLog
    from ledger.model import GENESIS
    from ledger.mongo_store import LedgerStore
    from ledger.scan import baseline_entries_for_a

    c = MongoClient(URI)
    for d in (DB, "it_ledger_a", "it_ledger_s"):
        c.drop_database(d)
    c[DB]["accounts"].insert_many([{"_id": i, "checking": 1000, "savings": 1000} for i in range(50)])
    enable_images(c[DB], ["accounts"])
    store_a = LedgerStore(c, "it_ledger_a")
    store_a.setup()
    log_a = DigestLog(str(tmp_path / "a.jsonl"))
    with c.start_session() as s:
        c[DB].command("ping", session=s)
        start = s.operation_time
    base = baseline_entries_for_a(c, DB, ["accounts"])
    b0 = build_block(0, GENESIS, base, time.time())
    store_a.append_block(b0, base)
    log_a.publish(b0)
    store_a.set_start_at(start)
    s = SyncLedger(c, DB, "it_ledger_s", heads=4)
    s.setup()
    store_s = LedgerStore(c, "it_ledger_s")
    store_s.setup()
    log_s = DigestLog(str(tmp_path / "s.jsonl"))
    s.baseline(["accounts"])
    s.fold(store_s, log_s)
    return dict(c=c, s=s, store_a=store_a, store_s=store_s, log_a=log_a, log_s=log_s)


def _service(env, seconds):
    """Run the Design A service for ``seconds`` against the live server."""
    from ledger.design_a import run_service
    t = threading.Thread(target=run_service, args=(env["c"], DB, env["store_a"], env["log_a"]),
                         kwargs=dict(interval=0.5, max_seconds=seconds), daemon=True)
    t.start()
    return t


def _verify(env, design):
    """Verify one design against the live data and return the report."""
    from ledger.scan import scan_state
    from ledger.verifier import verify
    store = env["store_a"] if design == "A" else env["store_s"]
    blocks, by = store.load()
    log = env["log_a"] if design == "A" else env["log_s"]
    kw = {}
    if design == "S":
        kw = dict(pending=env["s"].pending_entries(), heads=env["s"].head_state())
    return verify(blocks, by, log.read(), scan_state(env["c"], DB, ["accounts"]), namespaces={f"{DB}.accounts"}, **kw)


def test_both_designs_verify_after_concurrent_transfers(env):
    """AC01, AC02: concurrent transfers through Design S leave both ledgers clean on a real server."""
    from concurrent.futures import ThreadPoolExecutor

    svc = _service(env, seconds=15)

    def transfer(i):
        """Move one unit between two random accounts through the Design S library."""
        a, b = i % 50, (i * 7 + 1) % 50
        if a == b:
            return

        def fn(t):
            """Debit and credit inside one ledgered transaction."""
            t.update_one("accounts", {"_id": a}, {"$inc": {"checking": -1}})
            t.update_one("accounts", {"_id": b}, {"$inc": {"checking": 1}})
        env["s"].transaction(fn)

    with ThreadPoolExecutor(8) as pool:  # contention on the chain heads exercises retries
        list(pool.map(transfer, range(300)))
    env["s"].fold(env["store_s"], env["log_s"])
    svc.join()
    ra, rs = _verify(env, "A"), _verify(env, "S")
    assert ra.ok, ra.to_dict()
    assert rs.ok, rs.to_dict()
    blocks, by = env["store_a"].load()
    txns = {e.txn for b in blocks[1:] for e in by.get(b.block_id, [])}
    assert all(t.startswith("txn:") for t in txns)  # grouped by lsid and txnNumber


def test_bypass_write_is_recorded_by_a_and_flagged_for_s(env):
    """AC04: a write outside the library is recorded by Design A and flagged for Design S."""
    from ledger.verifier import cross_check

    svc = _service(env, seconds=4)
    env["c"][DB]["accounts"].update_one({"_id": 3}, {"$inc": {"checking": 500}})
    svc.join()
    assert _verify(env, "A").ok
    assert _verify(env, "S").kinds("alert") == {"modified_document": 1}
    ents = lambda st: [e for b in st.load()[0] for e in st.load()[1].get(b.block_id, [])]
    assert [f.kind for f in cross_check(ents(env["store_a"]), ents(env["store_s"]))] == ["write_outside_library"]


def test_rolled_back_ledger_fails_against_digests(env):
    """AC06: deleting the newest block on a real server is caught by the digest log."""
    svc = _service(env, seconds=3)
    env["s"].update_one("accounts", {"_id": 1}, {"$inc": {"savings": 1}})
    svc.join()
    last = env["store_a"].last_block().block_id
    env["store_a"].blocks.delete_many({"block_id": last})
    env["store_a"].entries.delete_many({"block_id": last})
    assert "digest_without_block" in _verify(env, "A").kinds("alert")
