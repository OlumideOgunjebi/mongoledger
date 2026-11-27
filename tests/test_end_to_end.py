# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/test_end_to_end.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: end to end tests: both designs, the verifier and every tampering
#          case
# Covers:  AC01, AC03 to AC06, AC08 (see docs/D.4.1)
# ============================================================================
"""Both designs end to end on the in memory stand in, including the insider attack scenarios."""

import pytest

from fake_mongo import FakeClient
from ledger.blocks import build_block
from ledger.design_a import EventProcessor
from ledger.design_s import SyncLedger
from ledger.digest_log import DigestLog
from ledger.model import GENESIS
from ledger.mongo_store import LedgerStore
from ledger.scan import baseline_entries_for_a, scan_state
from ledger.verifier import cross_check, verify

NS = {"bank.accounts"}


class Harness:
    """Ten accounts protected by both designs; pump() plays the Design A service and the folder."""

    def __init__(self, tmp_path):
        """Create an in memory server, load accounts and set up both designs."""
        self.c = FakeClient(watched=("bank",))
        self.c["bank"]["accounts"].insert_many([{"_id": i, "checking": 1000, "savings": 1000} for i in range(10)])
        # Design S: heads, baseline entries, block 0
        self.s = SyncLedger(self.c, "bank", "ledger_s", heads=2)
        self.s.setup()
        self.store_s, self.log_s = LedgerStore(self.c, "ledger_s"), DigestLog(str(tmp_path / "s.jsonl"))
        self.s.baseline(["accounts"])
        self.s.fold(self.store_s, self.log_s)
        # Design A: baseline block 0, then the event processor continues the chain
        self.store_a, self.log_a = LedgerStore(self.c, "ledger_a"), DigestLog(str(tmp_path / "a.jsonl"))
        base = baseline_entries_for_a(self.c, "bank", ["accounts"])
        b0 = build_block(0, GENESIS, base, 0.0)
        self.store_a.append_block(b0, base)
        self.log_a.publish(b0)
        self.proc = EventProcessor(interval=0, next_block_id=1, prev_hash=b0.block_hash)
        self.c.events.clear()

    def transfer(self, a, b, v):
        """A two document transaction through the Design S library."""
        def fn(t):
            """Debit one account and credit another in one ledgered transaction."""
            t.update_one("accounts", {"_id": a}, {"$inc": {"checking": -v}})
            t.update_one("accounts", {"_id": b}, {"$inc": {"checking": v}})
        self.s.transaction(fn)

    def pump(self):
        """Feed every pending change event to Design A, seal and publish; fold Design S."""
        for e in self.c.events:
            self.proc.consume(e)
        self.c.events.clear()
        sealed = self.proc.seal()
        if sealed:
            self.store_a.append_block(*sealed)
            self.log_a.publish(sealed[0])
        self.s.fold(self.store_s, self.log_s)

    def verify_a(self):
        """Run the Design A service to the end of the stream and verify Design A."""
        blocks, by = self.store_a.load()
        return verify(blocks, by, self.log_a.read(), scan_state(self.c, "bank", ["accounts"]), namespaces=NS)

    def verify_s(self):
        """Fold pending entries and verify Design S, including its chain heads."""
        blocks, by = self.store_s.load()
        return verify(blocks, by, self.log_s.read(), scan_state(self.c, "bank", ["accounts"]),
                      pending=self.s.pending_entries(), heads=self.s.head_state(), namespaces=NS)

    @staticmethod
    def all_entries(store):
        """Every sealed entry of a ledger, in block order."""
        blocks, by = store.load()
        return [e for b in blocks for e in by.get(b.block_id, [])]


@pytest.fixture
def h(tmp_path):
    """A fresh Harness for each test."""
    harness = Harness(tmp_path)
    for a, b in ((0, 1), (2, 3), (4, 5), (6, 7)):
        harness.transfer(a, b, 25)
    harness.pump()  # block 1 in both ledgers
    return harness


def test_clean_run_verifies_for_both_designs(h):
    """AC01, AC02: with no tampering both designs verify clean."""
    for a, b in ((1, 2), (3, 4), (8, 9)):
        h.transfer(a, b, 10)
    h.pump()
    ra, rs = h.verify_a(), h.verify_s()
    assert ra.ok, ra.to_dict()
    assert rs.ok, rs.to_dict()
    assert cross_check(h.all_entries(h.store_a), h.all_entries(h.store_s)) == []
    # a transfer is one transaction: its two entries share one subtree
    last = h.store_a.load()[0][-1]
    assert last.entry_count == 6 and last.txn_count == 3


def test_bypass_write_mismatch_for_s_recorded_by_a(h):
    """AC04: a bypass write is a state mismatch for S and a recorded change for A."""
    h.c["bank"]["accounts"].update_one({"_id": 3}, {"$inc": {"checking": 500}})  # bypasses the library
    h.pump()
    rs = h.verify_s()
    assert not rs.ok and rs.kinds("alert") == {"modified_document": 1}
    ra = h.verify_a()
    assert ra.ok  # the change is in the A ledger, with its before and after images
    leaked = cross_check(h.all_entries(h.store_a), h.all_entries(h.store_s))
    assert [f.kind for f in leaked] == ["write_outside_library"]


def test_bypass_then_legitimate_update_breaks_s_history(h):
    """AC04: a later legitimate update after a bypass still exposes the break in S."""
    h.c["bank"]["accounts"].update_one({"_id": 4}, {"$set": {"checking": 0}})
    h.transfer(4, 5, 1)
    h.pump()
    assert "history_break" in h.verify_s().kinds("alert")


def test_write_without_change_event_is_a_state_mismatch_for_a(h):
    # standalone restart analogue: the document changes but no event reaches the stream
    """AC04: a change with no change event is caught by the full scan."""
    h.c["bank"]["accounts"].docs[2]["checking"] = 999_999
    h.pump()
    assert h.verify_a().kinds("alert") == {"modified_document": 1}


def test_editing_a_sealed_entry_is_detected(h):
    """AC05: editing a sealed entry breaks the block content check."""
    entries = h.store_a.entries
    target = next(d for d in entries.docs.values() if d["block_id"] == 1)
    target["post_hash"] = "f" * 64
    assert "block_content_mismatch" in h.verify_a().kinds("alert")


def test_rewriting_a_block_consistently_still_fails_the_digest(h):
    """AC06: a consistently rewritten block still fails against its published digest."""
    blocks, by = h.store_a.load()
    last = blocks[-1]
    ents = by[last.block_id]
    ents[0].post_hash = "f" * 64
    forged = build_block(last.block_id, last.prev_hash, ents, last.created_at, resume_token=last.resume_token)
    h.store_a.blocks.docs = {k: (forged.to_doc() if d["block_id"] == last.block_id else d)
                             for k, d in h.store_a.blocks.docs.items()}
    for d in h.store_a.entries.docs.values():
        if d["block_id"] == last.block_id and d["idx"] == 0:
            d["post_hash"] = "f" * 64
    assert "digest_mismatch" in h.verify_a().kinds("alert")


def test_rollback_of_the_ledger_is_detected(h):
    """AC06: deleting the newest block leaves a digest without a block."""
    last = h.store_a.load()[0][-1].block_id
    h.store_a.blocks.delete_many({"block_id": last})
    h.store_a.entries.delete_many({"block_id": last})
    assert "digest_without_block" in h.verify_a().kinds("alert")


def test_gap_block_is_reported_and_unrecorded_changes_are_caught(h):
    """AC08: a gap is reported and changes made during it are caught by the scan."""
    sealed = h.proc.seal_gap("history lost (code 286)", resumed_at=[1, 1])
    h.store_a.append_block(*sealed)
    h.log_a.publish(sealed[0])
    h.c["bank"]["accounts"].update_one({"_id": 1}, {"$set": {"savings": 0}})
    h.c.events.clear()  # lost with the oplog history
    r = h.verify_a()
    assert r.kinds("warning")["gap"] == 1
    assert r.kinds("alert") == {"modified_document": 1}


def test_s_chain_tampering_is_detected(h):
    """AC03: tampering with a Design S chain head or link is detected."""
    d = next(iter(h.store_s.entries.docs.values()))
    d["chain"] = "0" * 64
    kinds = h.verify_s().kinds("alert")
    assert "s_chain_hash" in kinds or "block_content_mismatch" in kinds


def test_unpublished_tail_is_reported_as_exposure(h):
    """Blocks sealed after the last published digest are reported as information."""
    h.transfer(0, 1, 1)
    for e in h.c.events:
        h.proc.consume(e)
    blk, ents = h.proc.seal()
    h.store_a.append_block(blk, ents)  # sealed but digest not yet published
    r = h.verify_a()
    assert r.ok and r.kinds("info")["unpublished_tail"] == 1
