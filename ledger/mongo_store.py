# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/mongo_store.py
# Owner:   Rohith Nelluri (database lead)
# Purpose: storing blocks and entries in the ledger database (ledger_a,
#          ledger_s)
# Covers:  FR06, FR07, FR10 (see docs/D.4.1)
# ============================================================================
"""Persisting blocks and entries in a ledger database on the same replica set.

Collections of one ledger database (``ledger_a`` or ``ledger_s``):

``blocks``
    One document per sealed Block (see model.py), unique on ``block_id``.
``entries``
    One document per sealed Entry plus ``block_id`` and ``idx`` (its position
    inside the block), unique on ``(block_id, idx)``. The position matters:
    the Merkle root depends on entry order.
``meta``
    Bookkeeping, currently only ``{_id: "start", ts: Timestamp}``: the cluster
    time from which the Design A change stream starts after setup.

Design S adds ``heads`` and ``pending`` collections, managed in design_s.py.

The ledger lives on the same replica set as the data, so an administrator can
edit it. That is expected: the ledger's job is to make such edits detectable
once the block digests are published outside the database (digest_log.py).
"""

from __future__ import annotations

from typing import Optional

from bson import json_util
from bson.timestamp import Timestamp
from pymongo import ASCENDING, DESCENDING
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .model import Block, Entry


class LedgerStore:
    """Read and write access to one ledger database.

    All writes use majority write concern and all reads majority read
    concern, so a block that a replica set election rolls back can never be
    published or verified.
    """

    def __init__(self, client, ledger_db: str):
        """
        Args:
            client: a connected ``pymongo.MongoClient`` (or the test stand in).
            ledger_db: name of the ledger database, ``ledger_a`` or ``ledger_s``.
        """
        self.client = client
        self.db = client.get_database(ledger_db, write_concern=WriteConcern("majority"),
                                      read_concern=ReadConcern("majority"))
        self.blocks = self.db["blocks"]
        self.entries = self.db["entries"]
        self.meta = self.db["meta"]

    def setup(self) -> None:
        """Create the unique indexes; safe to call more than once."""
        self.blocks.create_index([("block_id", ASCENDING)], unique=True)
        self.entries.create_index([("block_id", ASCENDING), ("idx", ASCENDING)], unique=True)

    def append_block(self, block: Block, entries: list[Entry]) -> None:
        """Store a sealed block and its entries atomically.

        Entries and their block header are written in one transaction, so a
        crash can never leave a block without its entries or entries without
        their block. The unique index on ``block_id`` also stops two writers
        from appending the same block number twice.
        """
        docs = [{**e.to_doc(), "block_id": block.block_id, "idx": i} for i, e in enumerate(entries)]

        def write(session):
            """Insert the entries and the block header inside the caller's transaction."""
            if docs:
                self.entries.insert_many(docs, session=session)
            self.blocks.insert_one(block.to_doc(), session=session)

        with self.client.start_session() as s:
            s.with_transaction(write, write_concern=WriteConcern("majority"))

    def last_block(self) -> Optional[Block]:
        """The block with the highest ``block_id``, or None for an empty ledger."""
        d = self.blocks.find_one(sort=[("block_id", DESCENDING)], projection={"_id": 0})
        return Block.from_doc(d) if d else None

    def start_position(self) -> tuple[str, object] | None:
        """Where the Design A change stream should continue.

        Returns:
            ``("token", resume_token)`` after a data block, so no event is
            missed or repeated; ``("time", Timestamp)`` after a gap block (the
            point where capture resumed) or before the first block (the time
            recorded at setup); or None to start from now.
        """
        last = self.last_block()
        if last is not None and last.kind == "gap":
            t = (last.gap or {}).get("resumed_at_ts")
            return ("time", Timestamp(t[0], t[1])) if t else None
        if last is not None and last.resume_token:
            # Resume tokens are stored as Extended JSON text in the block.
            return ("token", json_util.loads(last.resume_token))
        d = self.meta.find_one({"_id": "start"})
        return ("time", d["ts"]) if d else None

    def set_start_at(self, ts) -> None:
        """Record the cluster time from which Design A must read (set at setup)."""
        self.meta.update_one({"_id": "start"}, {"$set": {"ts": ts}}, upsert=True)

    def load(self) -> tuple[list[Block], dict[int, list[Entry]]]:
        """Read the whole ledger in order.

        Returns:
            ``(blocks, entries_by_block)`` where blocks are sorted by
            ``block_id`` and each entry list is in stored ``idx`` order.
        """
        blocks = [Block.from_doc(d) for d in self.blocks.find({}, {"_id": 0}).sort("block_id", ASCENDING)]
        by_block: dict[int, list[Entry]] = {}
        for d in self.entries.find({}, {"_id": 0}).sort([("block_id", ASCENDING), ("idx", ASCENDING)]):
            by_block.setdefault(d["block_id"], []).append(Entry.from_doc(d))
        return blocks, by_block
