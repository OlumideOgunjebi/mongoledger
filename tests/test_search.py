# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/test_search.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: tests of the history, blocks, digests and prove commands
# Covers:  AC09, AC10 (see docs/D.4.1)
# ============================================================================
"""Auditor search commands: history, blocks, digests and prove (acceptance criterion AC10)."""

import json

import pytest
from bson.objectid import ObjectId

import ledger.cli as cli
from fake_mongo import FakeClient
from ledger.canonical import key_string
from ledger.design_a import run_service
from ledger.design_s import SyncLedger
from ledger.digest_log import DigestLog
from ledger.model import Entry
from ledger.mongo_store import LedgerStore
from ledger.search import block_rows, format_table, history, key_for, parse_doc_id, parse_time

BASE = ["--collections", "accounts"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Five accounts, both ledgers set up, three ledgered updates to account 1, service run once."""
    monkeypatch.chdir(tmp_path)
    c = FakeClient(watched=("bank",))
    c["bank"]["accounts"].insert_many([{"_id": i, "checking": 100, "savings": 100} for i in range(5)])
    monkeypatch.setattr(cli, "_client", lambda args: c)
    assert cli.main(BASE + ["setup", "--heads", "2"]) == 0
    s = SyncLedger(c, "bank", "ledger_s", heads=2)
    for v in (1, 2, 3):
        s.update_one("accounts", {"_id": 1}, {"$inc": {"checking": v}})
    run_service(c, "bank", LedgerStore(c, "ledger_a"), DigestLog("digests/design_a.jsonl"),
                interval=0.0, max_seconds=0.3, idle_sleep=0.0)
    return c


def _json(capsys, argv):
    """Run the command line with ``argv`` and parse its JSON output."""
    capsys.readouterr()
    code = cli.main(BASE + argv)
    return code, json.loads(capsys.readouterr().out)


def test_history_lists_every_version_of_one_document_in_order(env, capsys):
    """AC10: history lists every version of one document in commit order."""
    code, rows = _json(capsys, ["history", "--design", "A", "--key", "1", "--json"])
    assert code == 0
    assert [r["op"] for r in rows] == ["baseline", "update", "update", "update"]
    assert all(r["key"] == key_string({"_id": 1}) for r in rows)
    # each version starts where the previous one ended
    full = history(*LedgerStore(env, "ledger_a").load(), key=key_for("1"))
    assert [r["pre_hash"] for r in full[1:]] == [r["post_hash"] for r in full[:-1]]


def test_history_filters_by_operation_and_shows_design_s_pending(env, capsys):
    """AC10: the operation filter works and pending Design S entries are shown."""
    code, rows = _json(capsys, ["history", "--design", "A", "--op", "update", "--json"])
    assert code == 0 and len(rows) == 3
    code, rows = _json(capsys, ["history", "--design", "S", "--key", "1", "--json"])
    assert [r["block"] for r in rows] == [0, "pending", "pending", "pending"]


def test_history_time_and_block_filters():
    """AC10: time and block range filters select the right entries."""
    e = lambda w: Entry(ns="b.a", key="k", op="update", pre_hash="x", post_hash="y", txn="t", source="A", wall=w)
    by = {0: [e(None)], 1: [e(1_000.0), e(2_000.0)], 2: [e(3_000.0)]}
    rows = history([], by)  # blocks list empty: nothing to iterate
    assert rows == []
    from ledger.model import Block
    blocks = [Block(i, "0" * 64, "data", "", 1, len(by[i]), 0.0) for i in by]
    assert len(history(blocks, by, since=1_500.0)) == 2
    assert len(history(blocks, by, until=1_500.0)) == 1
    assert [r["block"] for r in history(blocks, by, block_from=1, block_to=1)] == [1, 1]
    assert len(history(blocks, by, limit=2)) == 2


def test_blocks_lists_every_block(env, capsys):
    """AC10: the blocks command lists every block with its counts."""
    code, rows = _json(capsys, ["blocks", "--design", "A", "--last", "0", "--json"])
    stored, _ = LedgerStore(env, "ledger_a").load()
    assert code == 0 and [r["block"] for r in rows] == [b.block_id for b in stored]
    assert block_rows(stored, last=1)[0]["block"] == stored[-1].block_id


def test_digests_flag_a_block_changed_after_publication(env, capsys):
    """AC10: the digests command flags a block changed after publication."""
    code, rows = _json(capsys, ["digests", "--design", "A", "--json"])
    assert code == 0 and {r["status"] for r in rows} == {"ok"}
    last = max(d["block_id"] for d in env["ledger_a"]["blocks"].docs.values())
    for d in env["ledger_a"]["blocks"].docs.values():
        if d["block_id"] == last:
            d["block_hash"] = "f" * 64
    code, rows = _json(capsys, ["digests", "--design", "A", "--json"])
    assert code == 2 and rows[-1]["status"] == "MISMATCH"
    for d in list(env["ledger_a"]["blocks"].docs):
        if env["ledger_a"]["blocks"].docs[d]["block_id"] == last:
            del env["ledger_a"]["blocks"].docs[d]
    code, rows = _json(capsys, ["digests", "--design", "A", "--json"])
    assert rows[-1]["status"] == "MISSING BLOCK"


def test_prove_newest_version_and_detect_an_altered_entry(env, capsys):
    """AC09: prove verifies the newest version and fails for an altered entry."""
    code, res = _json(capsys, ["prove", "--design", "A", "--key", "1"])
    assert code == 0 and res["verified"] and res["entry"]["op"] == "update"
    code, res = _json(capsys, ["prove", "--design", "A", "--block", str(res["block"]), "--idx", str(res["idx"])])
    assert code == 0 and res["verified"]
    for d in env["ledger_a"]["entries"].docs.values():
        if d["block_id"] == res["block"] and d["idx"] == res["idx"]:
            d["post_hash"] = "0" * 64
    code, res = _json(capsys, ["prove", "--design", "A", "--key", "1"])
    assert code == 2 and not res["verified"]


def test_prove_reports_unknown_document(env, capsys):
    """prove reports a document that has no ledger entry."""
    assert cli.main(BASE + ["prove", "--key", "999"]) == 1
    assert cli.main(BASE + ["prove"]) == 1


def test_table_output_and_parsing_helpers(env, capsys):
    """Table output and the key and time parsing helpers."""
    capsys.readouterr()
    assert cli.main(BASE + ["blocks"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("block") and "lag_mean_s" in out
    assert format_table([]) == "(no rows)"
    assert parse_doc_id("42") == 42 and parse_doc_id('"abc"') == "abc" and parse_doc_id("abc") == "abc"
    oid = ObjectId()
    assert parse_doc_id('{"$oid": "%s"}' % oid) == oid
    assert parse_time("1700000000") == 1700000000.0
    assert parse_time("2026-11-01") == parse_time("2026-11-01T00:00:00+00:00")
