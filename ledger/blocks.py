# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/blocks.py
# Owner:   Rohith Nelluri (database lead)
# Purpose: grouping entries by transaction, sealing and chaining blocks
# Covers:  FR05, FR07, FR18; acceptance test AC09 (see docs/D.4.1)
# ============================================================================
"""Building blocks: entries grouped by transaction, two level Merkle tree, chaining.

Tree layout
-----------
A block is a Merkle tree of transactions, and each transaction is a Merkle
tree of its entries::

    block root = MTH([txn_root(T1), txn_root(T2), ...])
    txn_root(T) = MTH([leaf_bytes(e) for e in T])

Grouping by transaction keeps a multi document transaction together: an
auditor can prove that a transfer's debit and credit were committed as one
unit, and a single inclusion proof has two short paths (entry to transaction,
transaction to block) instead of one long one.
"""

from __future__ import annotations

import statistics
from typing import Iterable, Optional, Sequence

from .merkle import inclusion_proof, merkle_root, verify_inclusion
from .model import Block, Entry


def txn_groups(entries: Iterable[Entry]) -> list[tuple[str, list[Entry]]]:
    """Group entries by transaction identifier.

    Transactions keep the order in which they first appear and entries keep
    their order inside each transaction, so the same input always produces
    the same tree.

    Returns:
        A list of ``(txn_id, [entries])`` pairs.
    """
    order: list[str] = []
    groups: dict[str, list[Entry]] = {}
    for e in entries:
        if e.txn not in groups:
            groups[e.txn] = []
            order.append(e.txn)
        groups[e.txn].append(e)
    return [(t, groups[t]) for t in order]


def txn_root(group: Sequence[Entry]) -> bytes:
    """Merkle root over the leaves of the entries of one transaction."""
    return merkle_root([e.leaf_bytes() for e in group])


def block_root(entries: Sequence[Entry]) -> bytes:
    """Merkle root whose leaves are the per transaction roots."""
    return merkle_root([txn_root(g) for _, g in txn_groups(entries)])


def build_block(block_id: int, prev_hash: str, entries: Sequence[Entry], created_at: float,
                resume_token: Optional[str] = None, kind: str = "data", gap: Optional[dict] = None) -> Block:
    """Create and seal one block.

    Args:
        block_id: the next position in the chain.
        prev_hash: ``block_hash`` of the previous block (GENESIS for block 0).
        entries: the entries to cover, in commit order.
        created_at: sealing time in epoch seconds; also used to compute lag.
        resume_token: Design A only, the change stream position after the
            last entry, stored so a restarted service continues from here.
        kind: "data" or "gap".
        gap: for gap blocks, a dict describing the lost interval.

    Returns:
        The sealed Block (its ``block_hash`` is set).
    """
    stamped = [e.ts for e in entries if e.ts]
    # Lag is the protection delay: how long each change waited between its
    # commit (server wall time) and being sealed into this block.
    lags = [created_at - e.wall for e in entries if e.wall is not None]
    blk = Block(
        block_id=block_id,
        prev_hash=prev_hash,
        kind=kind,
        root=block_root(entries).hex(),
        txn_count=len(txn_groups(entries)),
        entry_count=len(entries),
        created_at=created_at,
        first_ts=stamped[0] if stamped else None,
        last_ts=stamped[-1] if stamped else None,
        resume_token=resume_token,
        gap=gap,
        lag_max=max(lags) if lags else None,
        lag_mean=statistics.fmean(lags) if lags else None,
    )
    return blk.seal()


def prove_entry(entries: Sequence[Entry], index: int) -> dict:
    """Inclusion proof that ``entries[index]`` belongs to the block built from ``entries``.

    The proof has two RFC 9162 audit paths: from the entry to its transaction
    root, and from that transaction root to the block root.

    Args:
        entries: all entries of the block, in stored order.
        index: position of the entry to prove.

    Returns:
        A JSON serializable dict with indexes, counts and both paths in hex.
    """
    target = entries[index]
    groups = txn_groups(entries)
    t_idx = next(i for i, (t, _) in enumerate(groups) if t == target.txn)
    group = groups[t_idx][1]
    e_idx = next(i for i, e in enumerate(group) if e is target)
    return {
        "txn_index": t_idx, "txn_count": len(groups),
        "entry_index": e_idx, "entry_count": len(group),
        "entry_path": [h.hex() for h in inclusion_proof(e_idx, [e.leaf_bytes() for e in group])],
        "txn_root": txn_root(group).hex(),
        "txn_path": [h.hex() for h in inclusion_proof(t_idx, [txn_root(g) for _, g in groups])],
    }


def verify_entry_proof(entry: Entry, proof: dict, block: Block) -> bool:
    """Check a proof from ``prove_entry`` against a block's stored root.

    Returns:
        True only if the entry hashes up to its transaction root and that
        transaction root hashes up to ``block.root``.
    """
    ok_entry = verify_inclusion(proof["entry_index"], proof["entry_count"], entry.leaf_bytes(),
                                [bytes.fromhex(h) for h in proof["entry_path"]], bytes.fromhex(proof["txn_root"]))
    ok_txn = verify_inclusion(proof["txn_index"], proof["txn_count"], bytes.fromhex(proof["txn_root"]),
                              [bytes.fromhex(h) for h in proof["txn_path"]], bytes.fromhex(block.root))
    return ok_entry and ok_txn
