# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/design_s.py
# Owner:   Rohith Nelluri (database lead)
# Purpose: Design S: ledger entry written inside the application transaction,
#          k chain heads, fold into blocks
# Covers:  FR01, FR03, FR06, FR07, FR15; acceptance tests AC01, AC03 (see
#          docs/D.4.1)
# ============================================================================
"""Design S: synchronous ledger written inside the application's own transaction.

Every protected write goes through SyncLedger. Inside one multi document
transaction (majority write concern, snapshot read concern) the library reads
the before image, applies the write, reads the after image, appends a ledger
entry linked to one of k chain heads, and advances that head. Either the data
change and its entry both commit, or neither does.

Two transactions that advance the same head write the same head document, so
MongoDB aborts one with a TransientTransactionError and with_transaction
retries it. Partitioning the chain into k heads (chosen by hashing the
document key) spreads this contention; k is an experiment parameter.

A background fold() seals the entries committed since the last fold into a
Merkle block and publishes its digest.

Collections in the ledger database (default ``ledger_s``)::

    heads    {_id: h, seq: n, hash: hex}      newest link of chain head h
    pending  Entry documents committed but not yet sealed
    blocks / entries / meta                   sealed ledger (see mongo_store.py)

Example::

    ledger = SyncLedger(client, "bank", heads=4)
    def transfer(t):
        t.update_one("accounts", {"_id": 1}, {"$inc": {"checking": -500}})
        t.update_one("accounts", {"_id": 2}, {"$inc": {"checking": 500}})
    ledger.transaction(transfer)

Limitation (by design): a write that does not go through this library is not
recorded by Design S. The verifier still catches it, because the scanned
document no longer matches the replayed ledger (``modified_document``).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Callable, Mapping, Optional

from pymongo import ASCENDING
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import ReadPreference
from pymongo.write_concern import WriteConcern

from .blocks import build_block
from .canonical import doc_hash, key_string
from .model import GENESIS, Entry


def head_for(key: str, k: int) -> int:
    """Chain head for a document: first 8 bytes of SHA256(key) modulo k.

    Hashing spreads documents evenly over the heads, and the same document
    always lands on the same head, so all versions of one document stay in
    commit order on one chain.
    """
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % k


class _Txn:
    """Operations bound to one transaction attempt; all share one txn id.

    An instance is created for each attempt of ``with_transaction``. If
    MongoDB aborts and retries the transaction, a new instance (and a new
    transaction id) is created, so entries of an aborted attempt can never
    leak into the ledger.
    """

    def __init__(self, ledger: "SyncLedger", session):
        """Bind this attempt to the ledger and session and give it a fresh transaction id."""
        self.l = ledger
        self.s = session
        self.txn = "s:" + uuid.uuid4().hex
        self.entries: list[Entry] = []

    def _coll(self, name: str):
        """Protected data collection by name."""
        return self.l.data[name]

    def _record(self, coll: str, op: str, doc_id: Any, before: Optional[Mapping], after: Optional[Mapping]):
        """Append one ledger entry and advance its chain head, inside the transaction.

        Args:
            coll: protected collection name.
            op: insert, update, replace, delete or baseline.
            doc_id: the document ``_id``.
            before, after: full document images (None where not applicable).
        """
        ns = f"{self.l.data.name}.{coll}"
        key = key_string({"_id": doc_id})
        h = head_for(f"{ns}|{key}", self.l.k)
        # Reading the head inside the transaction puts it in the transaction's
        # snapshot; a concurrent writer to the same head causes a write
        # conflict and a retry instead of a forked chain.
        head = self.l.heads.find_one({"_id": h}, session=self.s)
        e = Entry(ns=ns, key=key, op=op, pre_hash=doc_hash(before), post_hash=doc_hash(after),
                  txn=self.txn, source="S", wall=time.time(), head=h, seq=head["seq"] + 1, prev=head["hash"])
        e.chain = e.chain_hash(e.prev)
        self.l.pending.insert_one(e.to_doc(), session=self.s)
        # Conditional update on the old seq: a second guard against two
        # writers advancing the same head from the same position.
        self.l.heads.update_one({"_id": h, "seq": head["seq"]}, {"$set": {"seq": e.seq, "hash": e.chain}}, session=self.s)
        self.entries.append(e)

    def insert_one(self, coll: str, doc: dict) -> Any:
        """Insert a document and record it; returns the new ``_id``."""
        res = self._coll(coll).insert_one(doc, session=self.s)
        # Read back the stored document so the hash covers server side
        # conversions (generated _id, millisecond dates).
        after = self._coll(coll).find_one({"_id": res.inserted_id}, session=self.s)
        self._record(coll, "insert", res.inserted_id, None, after)
        return res.inserted_id

    def update_one(self, coll: str, flt: dict, update: dict) -> Optional[dict]:
        """Update the first matching document and record before and after.

        Returns:
            The document after the update, or None if nothing matched.
        """
        before = self._coll(coll).find_one(flt, session=self.s)
        if before is None:
            return None
        self._coll(coll).update_one({"_id": before["_id"]}, update, session=self.s)
        after = self._coll(coll).find_one({"_id": before["_id"]}, session=self.s)
        self._record(coll, "update", before["_id"], before, after)
        return after

    def replace_one(self, coll: str, flt: dict, replacement: dict) -> Optional[dict]:
        """Replace the first matching document and record before and after."""
        before = self._coll(coll).find_one(flt, session=self.s)
        if before is None:
            return None
        self._coll(coll).replace_one({"_id": before["_id"]}, replacement, session=self.s)
        after = self._coll(coll).find_one({"_id": before["_id"]}, session=self.s)
        self._record(coll, "replace", before["_id"], before, after)
        return after

    def delete_one(self, coll: str, flt: dict) -> bool:
        """Delete the first matching document and record its last version."""
        before = self._coll(coll).find_one(flt, session=self.s)
        if before is None:
            return False
        self._coll(coll).delete_one({"_id": before["_id"]}, session=self.s)
        self._record(coll, "delete", before["_id"], before, None)
        return True

    def find_one(self, coll: str, flt: dict) -> Optional[dict]:
        """Read inside the transaction snapshot (reads are not ledgered)."""
        return self._coll(coll).find_one(flt, session=self.s)


class SyncLedger:
    """Public API of Design S."""

    def __init__(self, client, data_db: str, ledger_db: str = "ledger_s", heads: int = 1):
        """
        Args:
            client: connected MongoClient for a replica set (transactions need one).
            data_db: database holding the protected collections.
            ledger_db: database for heads, pending entries and sealed blocks.
            heads: number of chain heads k (must match the value used at setup).
        """
        self.client = client
        self.data = client[data_db]
        self.k = heads
        self.ldb = client[ledger_db]
        self.heads = self.ldb["heads"]
        self.pending = self.ldb["pending"]

    def setup(self) -> None:
        """Create k chain heads at GENESIS (existing heads are kept) and the pending index."""
        for h in range(self.k):
            self.heads.update_one({"_id": h}, {"$setOnInsert": {"seq": 0, "hash": GENESIS}}, upsert=True)
        self.pending.create_index([("head", ASCENDING), ("seq", ASCENDING)], unique=True)

    def transaction(self, fn: Callable[[_Txn], Any]) -> Any:
        """Run ``fn(txn)`` atomically; every write through ``txn`` is ledgered with one txn id.

        ``with_transaction`` retries the callback on transient errors such as
        write conflicts on a chain head, so ``fn`` may run more than once and
        must not have side effects outside the database.

        Returns:
            Whatever ``fn`` returned on the attempt that committed.
        """
        result: dict = {}

        def callback(session):
            """One attempt of the transaction: a new _Txn, then the application function."""
            t = _Txn(self, session)
            result["value"] = fn(t)

        with self.client.start_session() as s:
            s.with_transaction(callback, read_concern=ReadConcern("snapshot"),
                               write_concern=WriteConcern("majority"), read_preference=ReadPreference.PRIMARY)
        return result.get("value")

    # single operation conveniences: each runs in its own transaction
    def insert_one(self, coll: str, doc: dict):
        """Insert one document in its own ledgered transaction."""
        return self.transaction(lambda t: t.insert_one(coll, doc))

    def update_one(self, coll: str, flt: dict, update: dict):
        """Update one document in its own ledgered transaction."""
        return self.transaction(lambda t: t.update_one(coll, flt, update))

    def replace_one(self, coll: str, flt: dict, replacement: dict):
        """Replace one document in its own ledgered transaction."""
        return self.transaction(lambda t: t.replace_one(coll, flt, replacement))

    def delete_one(self, coll: str, flt: dict):
        """Delete one document in its own ledgered transaction."""
        return self.transaction(lambda t: t.delete_one(coll, flt))

    def baseline(self, collections, batch: int = 500) -> int:
        """Record existing documents once, before measured writes begin.

        Documents are recorded in transactions of ``batch`` entries to stay
        well below MongoDB's transaction size limits.

        Returns:
            Number of documents recorded.
        """
        n = 0
        for name in collections:
            docs = list(self.data[name].find({}))
            for i in range(0, len(docs), batch):
                chunk = docs[i:i + batch]

                def cb(session, chunk=chunk, name=name):
                    """Record one chunk of current documents as baseline entries in one transaction."""
                    t = _Txn(self, session)
                    t.txn = "baseline"
                    for doc in chunk:
                        t._record(name, "baseline", doc["_id"], None, doc)

                with self.client.start_session() as s:
                    s.with_transaction(cb, write_concern=WriteConcern("majority"))
                n += len(chunk)
        return n

    def fold(self, store, digest_log) -> Optional[int]:
        """Seal committed pending entries into one block and publish its digest.

        Moving the entries into the block and deleting them from ``pending``
        happen in one transaction, so an entry is always in exactly one
        place. The digest is published only after that transaction commits.

        Args:
            store: LedgerStore of the Design S ledger database.
            digest_log: DigestLog that receives the new block's digest.

        Returns:
            The new block id, or None when nothing was pending.
        """
        docs = list(self.pending.find({}).sort([("head", ASCENDING), ("seq", ASCENDING)]))
        if not docs:
            return None
        entries = [Entry.from_doc(d) for d in docs]
        last = store.last_block()
        blk = build_block((last.block_id + 1) if last else 0, last.block_hash if last else GENESIS,
                          entries, time.time())
        ids = [d["_id"] for d in docs]

        def write(session):
            """Move pending entries into a sealed block atomically: insert entries and header, delete pending."""
            store.entries.insert_many([{**e.to_doc(), "block_id": blk.block_id, "idx": i}
                                       for i, e in enumerate(entries)], session=session)
            store.blocks.insert_one(blk.to_doc(), session=session)
            self.pending.delete_many({"_id": {"$in": ids}}, session=session)

        with self.client.start_session() as s:
            s.with_transaction(write, write_concern=WriteConcern("majority"))
        digest_log.publish(blk)
        return blk.block_id

    def head_state(self) -> dict[int, dict]:
        """Current head documents, keyed by head number (used by the verifier)."""
        return {d["_id"]: d for d in self.heads.find({})}

    def pending_entries(self) -> list[Entry]:
        """Entries committed but not yet sealed, in head and sequence order."""
        return [Entry.from_doc(d) for d in self.pending.find({}).sort([("head", ASCENDING), ("seq", ASCENDING)])]
