# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/test_design_a.py
# Owner:   Yadu Pratap (deployment lead)
# Purpose: unit tests of the Design A event processor
# Covers:  AC02, AC07, AC08 (see docs/D.4.1)
# ============================================================================
"""Design A: change events to entries, sealing on an interval, gap blocks."""

import uuid
from datetime import datetime, timezone

from bson.binary import Binary, UUID_SUBTYPE
from bson.timestamp import Timestamp

from ledger.canonical import canonical_json, doc_hash, key_string
from ledger.design_a import EventProcessor, entry_from_event
from ledger.model import GENESIS, UNAVAILABLE

LSID = {"id": Binary(uuid.uuid4().bytes, UUID_SUBTYPE)}


def _ev(op="update", _id=7, before=None, after=None, txn=None, t=1760000100, i=3, token="0001"):
    """Build a change event shaped as in the MongoDB manual."""
    ev = {"_id": {"_data": token}, "operationType": op, "ns": {"db": "bank", "coll": "accounts"},
          "documentKey": {"_id": _id}, "clusterTime": Timestamp(t, i), "wallTime": datetime(2026, 10, 7, 12, 0, 0)}
    if before is not None:
        ev["fullDocumentBeforeChange"] = before
    if after is not None:
        ev["fullDocument"] = after
    if txn is not None:
        ev["lsid"], ev["txnNumber"] = LSID, txn
    return ev


def test_entry_fields_from_update_event():
    """AC02: an update event becomes an entry with key, operation and both hashes."""
    before, after = {"_id": 7, "checking": 10}, {"_id": 7, "checking": 15}
    e = entry_from_event(_ev(before=before, after=after, txn=4), seq=1)
    assert e.ns == "bank.accounts" and e.key == key_string({"_id": 7}) and e.op == "update"
    assert e.pre_hash == doc_hash(before) and e.post_hash == doc_hash(after)
    assert e.txn.startswith("txn:") and e.txn.endswith(":4") and e.source == "A"
    assert e.ts == [1760000100, 3]
    assert e.wall == datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp()  # naive wallTime is UTC


def test_missing_images_are_marked_unavailable():
    """An event without images is recorded with its hashes marked unavailable."""
    upd = entry_from_event(_ev(op="update"), 1)
    assert upd.pre_hash == UNAVAILABLE and upd.post_hash == UNAVAILABLE
    dele = entry_from_event(_ev(op="delete"), 2)
    assert dele.pre_hash == UNAVAILABLE and dele.post_hash is None
    ins = entry_from_event(_ev(op="insert", after={"_id": 7}), 3)
    assert ins.pre_hash is None and ins.post_hash == doc_hash({"_id": 7})


def test_transaction_events_share_one_txn_id():
    """AC02: events with the same lsid and txnNumber share one transaction id."""
    a = entry_from_event(_ev(_id=1, txn=9), 1)
    b = entry_from_event(_ev(_id=2, txn=9), 2)
    c = entry_from_event(_ev(_id=3, txn=10), 3)
    assert a.txn == b.txn != c.txn
    x = entry_from_event(_ev(_id=4), 4)
    y = entry_from_event(_ev(_id=5), 5)
    assert x.txn.startswith("op:") and x.txn != y.txn


def test_ddl_and_invalidate_events():
    """Drop, rename and invalidate events are recorded and handled."""
    drop = entry_from_event({"operationType": "drop", "ns": {"db": "bank", "coll": "accounts"},
                             "clusterTime": Timestamp(1, 1)}, 1)
    assert drop.op == "drop" and drop.key == "" and drop.ns == "bank.accounts"
    assert entry_from_event({"operationType": "invalidate", "clusterTime": Timestamp(1, 2)}, 2) is None


def test_blocks_seal_on_interval_and_chain():
    """Blocks seal at the interval and link to the previous block."""
    now = [1000.0]
    p = EventProcessor(interval=5, clock=lambda: now[0])
    p.consume(_ev(_id=1, after={"_id": 1}, before={"_id": 1}, token="0001"))
    assert not p.due()
    now[0] += 5
    assert p.due()
    b0, ents = p.seal()
    assert b0.block_id == 0 and b0.prev_hash == GENESIS and len(ents) == 1
    assert b0.resume_token == canonical_json({"_data": "0001"})
    p.consume(_ev(_id=2, after={"_id": 2}, before={"_id": 2}, token="0002"))
    now[0] += 5
    b1, _ = p.seal()
    assert b1.block_id == 1 and b1.prev_hash == b0.block_hash
    assert p.seal() is None  # nothing pending


def test_gap_block_records_lost_range():
    """AC08: a gap block records the reason and the lost range."""
    p = EventProcessor(interval=0, clock=lambda: 50.0)
    p.consume(_ev(_id=1, after={"_id": 1}, before={"_id": 1}, t=1760000200, i=7))
    p.seal()
    gap, ents = p.seal_gap("history lost (code 286)", resumed_at=[1760000900, 1])
    assert gap.kind == "gap" and ents == [] and gap.entry_count == 0
    assert gap.gap == {"reason": "history lost (code 286)", "last_seen_ts": [1760000200, 7],
                       "resumed_at_ts": [1760000900, 1]}
    assert gap.block_id == 1 and gap.resume_token is None


def test_a_transaction_is_never_split_across_blocks():
    """Sealing in the middle of a transaction's events keeps that transaction together (FR05)."""
    p = EventProcessor(interval=0, clock=lambda: 100.0)
    lsid = {"id": "session-1"}
    mk = lambda i, txn: {"_id": {"_data": f"{i:04x}"}, "operationType": "update",
                         "ns": {"db": "bank", "coll": "accounts"}, "documentKey": {"_id": i},
                         "fullDocument": {"_id": i, "v": 2}, "fullDocumentBeforeChange": {"_id": i, "v": 1},
                         "lsid": lsid, "txnNumber": txn, "clusterTime": None}
    p.consume(mk(1, 7))
    p.consume(mk(2, 8))                 # transaction 8 may still have more events
    blk, ents = p.seal(final=False)
    assert [e.key for e in ents] == [key_string({"_id": 1})]
    assert blk.resume_token == canonical_json({"_data": "0001"})  # restart re-reads transaction 8
    p.consume(mk(3, 8))
    assert p.seal(final=False) is None  # still only transaction 8
    blk, ents = p.seal(final=True)
    assert {e.txn for e in ents} == {ents[0].txn} and len(ents) == 2
