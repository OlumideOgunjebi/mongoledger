# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/model.py
# Owner:   Rohith Nelluri (database lead)
# Purpose: the two ledger record types shared by both designs: Entry and Block
# Covers:  FR02, FR07 (see docs/D.4.1)
# ============================================================================
"""Ledger records: entries (one per document change) and blocks.

Both designs produce exactly these two record types, which is what lets one
verifier serve Design S and Design A.

Entry
    One change to one protected document. Its ``leaf_bytes`` are the Merkle
    leaf, so changing any covered field changes the block root.

Block
    A sealed group of entries. The header holds the Merkle root, counts, time
    range and the hash of the previous block; ``block_hash`` is the SHA256 of
    the header and is what the external digest log publishes. Because each
    header contains the previous block hash, the blocks form a hash chain.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .canonical import plain_json

# prev_hash of block 0 and of the first entry on every Design S chain head.
GENESIS = "0" * 64

# Change event operation types that modify one document.
DATA_OPS = ("insert", "update", "replace", "delete")
# Operations that affect a whole protected collection or database; the
# verifier always raises an alert for these.
DDL_OPS = ("drop", "rename", "dropDatabase")
# Marker stored instead of a hash when MongoDB could not supply an image
# (for example when before images were not yet enabled for the collection).
UNAVAILABLE = "UNAVAILABLE"


@dataclass
class Entry:
    """One change to one protected document.

    Attributes:
        ns: ``database.collection`` of the document.
        key: canonical JSON of the documentKey, for example
            ``{"_id":{"$numberInt":"7"}}``.
        op: insert, update, replace, delete, baseline, drop, rename or
            dropDatabase.
        pre_hash: hash of the document before the change (None for insert and
            baseline, UNAVAILABLE when the image was missing).
        post_hash: hash of the document after the change (None for delete).
        txn: transaction identifier shared by every entry of one transaction.
        source: "S" (synchronous library), "A" (change stream service) or "B"
            (baseline taken at setup).
        ts: oplog clusterTime as [seconds, increment] when known (Design A).
        wall: server wall clock time in epoch seconds when known; used to
            measure the delay between commit and sealing (lag).
        head, seq, prev, chain: per head hash chain bookkeeping used only by
            Design S. ``chain = sha256(prev || leaf_bytes)``.
    """

    ns: str
    key: str
    op: str
    pre_hash: Optional[str]
    post_hash: Optional[str]
    txn: str
    source: str
    ts: Optional[list] = None
    wall: Optional[float] = None
    head: Optional[int] = None
    seq: Optional[int] = None
    prev: Optional[str] = None
    chain: Optional[str] = None

    def core(self) -> dict:
        """Fields covered by the Merkle leaf.

        Chain bookkeeping (head, seq, prev, chain) is excluded because it is
        checked separately by the Design S chain verification, and Design A
        entries do not have it at all.
        """
        return {
            "ns": self.ns, "key": self.key, "op": self.op, "pre_hash": self.pre_hash,
            "post_hash": self.post_hash, "txn": self.txn, "source": self.source,
            "ts": self.ts, "wall": self.wall,
        }

    def leaf_bytes(self) -> bytes:
        """The Merkle leaf for this entry: ``b"entry:" + plain_json(core)``."""
        return b"entry:" + plain_json(self.core())

    def chain_hash(self, prev: str) -> str:
        """Design S chain link: SHA256 of the previous link followed by this leaf.

        Args:
            prev: hexadecimal chain hash of the previous entry on the same head
                (GENESIS for the first entry).
        """
        return hashlib.sha256(bytes.fromhex(prev) + self.leaf_bytes()).hexdigest()

    def to_doc(self) -> dict:
        """Plain dict for storage in MongoDB."""
        return asdict(self)

    @classmethod
    def from_doc(cls, d: dict) -> "Entry":
        """Rebuild an Entry from a stored document, ignoring extra fields
        such as ``_id``, ``block_id`` and ``idx`` that the store adds."""
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: d.get(k) for k in names})


@dataclass
class Block:
    """A sealed block of ledger entries.

    Attributes:
        block_id: position in the chain, starting at 0 (the baseline block).
        prev_hash: ``block_hash`` of the previous block (GENESIS for block 0).
        kind: "data" for normal blocks, "gap" when Design A lost history.
        root: hexadecimal Merkle root over the per transaction roots.
        txn_count, entry_count: number of transactions and entries covered.
        created_at: epoch seconds when the block was sealed.
        first_ts, last_ts: oplog times of the first and last entry (Design A).
        resume_token: change stream token to restart from after this block.
        gap: for gap blocks, the reason and the point where capture resumed.
        lag_max, lag_mean: seconds between commit (entry.wall) and sealing.
        block_hash: SHA256 of the header; set by ``seal``.
    """

    block_id: int
    prev_hash: str
    kind: str                      # "data" or "gap"
    root: str                      # hex Merkle root over transaction roots
    txn_count: int
    entry_count: int
    created_at: float
    first_ts: Optional[list] = None
    last_ts: Optional[list] = None
    resume_token: Optional[str] = None
    gap: Optional[dict] = None
    lag_max: Optional[float] = None
    lag_mean: Optional[float] = None
    block_hash: str = field(default="")

    def header(self) -> dict:
        """Every field except ``block_hash``; this is what gets hashed."""
        d = asdict(self)
        d.pop("block_hash")
        return d

    def compute_hash(self) -> str:
        """SHA256 of ``b"block:" + plain_json(header)`` in hexadecimal."""
        return hashlib.sha256(b"block:" + plain_json(self.header())).hexdigest()

    def seal(self) -> "Block":
        """Fix the block by storing its header hash; returns the block itself."""
        self.block_hash = self.compute_hash()
        return self

    def to_doc(self) -> dict:
        """Plain dict for storage in MongoDB."""
        return asdict(self)

    @classmethod
    def from_doc(cls, d: dict) -> "Block":
        """Rebuild a Block from a stored document, ignoring ``_id``."""
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: d.get(k) for k in names})


def as_plain(value: Any) -> Any:
    """Convert a BSON Timestamp to ``[t, i]``; leave plain values untouched.

    Change events carry ``clusterTime`` as a BSON Timestamp, which plain JSON
    cannot encode. Storing it as a two element list keeps the leaf bytes
    deterministic and still sorts correctly.
    """
    if value is None:
        return None
    if hasattr(value, "time") and hasattr(value, "inc"):
        return [int(value.time), int(value.inc)]
    return value
