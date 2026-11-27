# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/test_service_and_cli.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: tests of setup, the Design A service, restart, gap blocks and the
#          cross check
# Covers:  AC04, AC07, AC08 (see docs/D.4.1)
# ============================================================================
"""The Design A service loop and the command line paths, over the in-memory stand in."""

import json

import pytest

import ledger.cli as cli
from fake_mongo import FakeClient
from ledger.design_a import run_service
from ledger.design_s import SyncLedger
from ledger.digest_log import DigestLog
from ledger.mongo_store import LedgerStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    """In memory server with loaded accounts, patched into the command line."""
    monkeypatch.chdir(tmp_path)
    c = FakeClient(watched=("bank",))
    c["bank"]["accounts"].insert_many([{"_id": i, "checking": 100, "savings": 100} for i in range(5)])
    monkeypatch.setattr(cli, "_client", lambda args: c)
    assert cli.main(["--collections", "accounts", "setup", "--heads", "2"]) == 0
    return c


def _service(c, seconds=0.3):
    """Run the Design A service briefly through the command line."""
    run_service(c, "bank", LedgerStore(c, "ledger_a"), DigestLog("digests/design_a.jsonl"),
                interval=0.0, max_seconds=seconds, idle_sleep=0.0)


def test_setup_creates_baselines_and_verify_passes(env, capsys):
    """AC01: setup writes both baselines and verify then reports clean."""
    c = env
    SyncLedger(c, "bank", "ledger_s", heads=2).update_one("accounts", {"_id": 1}, {"$inc": {"checking": 5}})
    _service(c)
    capsys.readouterr()
    assert cli.main(["--collections", "accounts", "verify", "--design", "A"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["stats"]["documents_scanned"] == 5
    assert cli.main(["--collections", "accounts", "verify", "--design", "S"]) == 0


def test_service_resumes_after_its_last_block(env):
    """AC07: after a restart the service continues from its stored resume token."""
    c = env
    acc = c["bank"]["accounts"]
    acc.update_one({"_id": 1}, {"$inc": {"checking": 1}})
    _service(c)
    first = LedgerStore(c, "ledger_a").last_block().block_id
    acc.update_one({"_id": 2}, {"$inc": {"checking": 1}})
    _service(c)  # new process: continues from the stored resume token, no duplicates
    blocks, by = LedgerStore(c, "ledger_a").load()
    changed = [e.key for b in blocks[first:] for e in by.get(b.block_id, [])]
    assert len(changed) == 2
    assert cli.run_verify(c, "bank", ["accounts"], "A").ok


def test_history_lost_produces_gap_block_and_restart_from_now(env):
    """AC08: when the resume point is gone the service seals a gap block and restarts."""
    c = env
    acc = c["bank"]["accounts"]
    acc.update_one({"_id": 1}, {"$inc": {"checking": 1}})
    _service(c)
    acc.update_one({"_id": 3}, {"$inc": {"checking": 99}})  # happens while the service is down
    c.oplog_floor = len(c.events)                            # ...and then leaves the oplog
    _service(c)
    blocks, _ = LedgerStore(c, "ledger_a").load()
    assert blocks[-1].kind == "gap" and "286" in blocks[-1].gap["reason"]
    r = cli.run_verify(c, "bank", ["accounts"], "A")
    assert r.kinds("warning")["gap"] == 1
    assert r.kinds("alert") == {"modified_document": 1}  # the lost change is still caught by the scan
    acc.update_one({"_id": 4}, {"$inc": {"checking": 1}})
    _service(c)  # after the gap the stream continues from the recorded restart point
    assert LedgerStore(c, "ledger_a").last_block().kind == "data"


def test_cross_check_flags_write_outside_library(env):
    """AC04: the cross option flags a write that bypassed the Design S library."""
    c = env
    SyncLedger(c, "bank", "ledger_s", heads=2).update_one("accounts", {"_id": 0}, {"$inc": {"savings": 1}})
    c["bank"]["accounts"].update_one({"_id": 4}, {"$set": {"savings": 0}})  # bypass
    _service(c)
    r = cli.run_verify(c, "bank", ["accounts"], "A", cross=True)
    assert r.kinds("alert") == {"write_outside_library": 1}
