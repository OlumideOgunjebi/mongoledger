# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/search.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: auditor searches: history, blocks, digests and inclusion proofs
# Covers:  FR18 to FR20; acceptance tests AC09, AC10 (see docs/D.4.1)
# ============================================================================
"""Searching the ledger: document history, block listings, digests and proofs.

These functions back the ``history``, ``blocks``, ``digests`` and ``prove``
commands. They work on what ``LedgerStore.load`` returns, so they are pure
and unit tested without a database. Each returns plain dictionaries that the
command line prints either as a table or as JSON.

Typical auditor workflow after ``verify`` reports a finding::

    mongoledger verify --design A               finding: modified_document, key {"_id": 42}
    mongoledger history --design A --key 42     every recorded version of document 42
    mongoledger prove --design A --key 42       proof that the last version is sealed
    mongoledger digests --design A              which blocks are published outside
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence

from bson import json_util

from .blocks import prove_entry, verify_entry_proof
from .canonical import key_string
from .model import Block, Entry


def parse_doc_id(text: str):
    """Turn a document id typed on the command line into the stored value.

    ``42`` becomes the integer 42, ``"abc"`` (with quotes) the string abc,
    and ``{"$oid": "5ca4bbc7a2dd94ee5816238c"}`` an ObjectId. Anything that is
    not valid Extended JSON is taken as a plain string.
    """
    try:
        return json_util.loads(text)
    except Exception:
        return text


def key_for(text: str) -> str:
    """Ledger key string for a document id typed by the user."""
    return key_string({"_id": parse_doc_id(text)})


def parse_time(text: Optional[str]) -> Optional[float]:
    """Accept epoch seconds or an ISO 8601 date (UTC assumed) and return epoch seconds."""
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()


def iso(epoch: Optional[float]) -> Optional[str]:
    """Epoch seconds as a readable UTC time with milliseconds, or None."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _all_entries(blocks: Sequence[Block], by_block: Mapping[int, Sequence[Entry]],
                 pending: Sequence[Entry] = ()) -> Iterable[tuple]:
    """Yield ``(block_id, idx, entry)`` in ledger order; pending entries get block None."""
    for b in sorted(blocks, key=lambda x: x.block_id):
        for i, e in enumerate(by_block.get(b.block_id, [])):
            yield b.block_id, i, e
    for i, e in enumerate(pending):
        yield None, i, e


def history(blocks: Sequence[Block], by_block: Mapping[int, Sequence[Entry]], pending: Sequence[Entry] = (),
            key: Optional[str] = None, ns: Optional[str] = None, op: Optional[str] = None,
            since: Optional[float] = None, until: Optional[float] = None,
            block_from: Optional[int] = None, block_to: Optional[int] = None,
            limit: Optional[int] = None) -> list[dict]:
    """Ledger entries matching every filter given, oldest first.

    Args:
        blocks, by_block: the stored ledger.
        pending: Design S entries not yet sealed (shown with block "pending").
        key: ledger key string of one document (see ``key_for``).
        ns: ``db.collection`` to restrict to.
        op: operation name, for example update or delete.
        since, until: commit time window in epoch seconds (uses entry.wall;
            baseline entries have no commit time and are excluded by it).
        block_from, block_to: inclusive range of block ids.
        limit: keep only the newest ``limit`` rows.

    Returns:
        One dict per entry with block, idx, time, ns, key, op, hashes and txn.
    """
    rows = []
    for bid, idx, e in _all_entries(blocks, by_block, pending):
        if key is not None and e.key != key:
            continue
        if ns is not None and e.ns != ns:
            continue
        if op is not None and e.op != op:
            continue
        if since is not None and (e.wall is None or e.wall < since):
            continue
        if until is not None and (e.wall is None or e.wall > until):
            continue
        if block_from is not None and (bid is None or bid < block_from):
            continue
        if block_to is not None and (bid is None or bid > block_to):
            continue
        rows.append({
            "block": bid if bid is not None else "pending", "idx": idx, "time": iso(e.wall),
            "ns": e.ns, "key": e.key, "op": e.op,
            "pre_hash": e.pre_hash, "post_hash": e.post_hash, "txn": e.txn, "source": e.source,
        })
    return rows[-limit:] if limit else rows


def block_rows(blocks: Sequence[Block], last: Optional[int] = None) -> list[dict]:
    """Summary of each block (newest ``last`` blocks when given), oldest first."""
    ordered = sorted(blocks, key=lambda x: x.block_id)
    if last:
        ordered = ordered[-last:]
    return [{
        "block": b.block_id, "kind": b.kind, "entries": b.entry_count, "txns": b.txn_count,
        "sealed": iso(b.created_at),
        "lag_mean_s": round(b.lag_mean, 3) if b.lag_mean is not None else None,
        "lag_max_s": round(b.lag_max, 3) if b.lag_max is not None else None,
        "hash": b.block_hash[:16],
        "note": (b.gap or {}).get("reason") if b.kind == "gap" else "",
    } for b in ordered]


def digest_rows(blocks: Sequence[Block], digests: Sequence[dict]) -> list[dict]:
    """Each published digest with its status against the stored ledger.

    Status is ``ok`` when the stored block still has the published hash,
    ``MISMATCH`` when the block was changed after publication, and
    ``MISSING BLOCK`` when the block no longer exists (rollback or deletion).
    """
    by_id = {b.block_id: b for b in blocks}
    rows = []
    for d in digests:
        b = by_id.get(d["block_id"])
        status = "MISSING BLOCK" if b is None else ("ok" if b.block_hash == d["block_hash"] else "MISMATCH")
        rows.append({"block": d["block_id"], "kind": d.get("kind"), "published": iso(d.get("published_at")),
                     "hash": d["block_hash"][:16], "status": status})
    return rows


def prove(blocks: Sequence[Block], by_block: Mapping[int, Sequence[Entry]], key: Optional[str] = None,
          block_id: Optional[int] = None, idx: Optional[int] = None) -> dict:
    """Build and check an inclusion proof for one sealed entry.

    Select the entry either by document (``key``: its newest sealed entry)
    or by position (``block_id`` and ``idx``).

    Returns:
        A dict with the entry, its block, the proof and ``verified`` (bool).

    Raises:
        LookupError: if no matching sealed entry exists.
    """
    if key is not None:
        found = [(bid, i) for bid, i, e in _all_entries(blocks, by_block) if e.key == key]
        if not found:
            raise LookupError("no sealed entry for this document")
        block_id, idx = found[-1]
    blk = next((b for b in blocks if b.block_id == block_id), None)
    ents = list(by_block.get(block_id, []))
    if blk is None or idx is None or not 0 <= idx < len(ents):
        raise LookupError("no such block or entry")
    proof = prove_entry(ents, idx)
    entry = ents[idx]
    return {
        "block": block_id, "idx": idx, "block_root": blk.root, "block_hash": blk.block_hash,
        "entry": {"ns": entry.ns, "key": entry.key, "op": entry.op, "post_hash": entry.post_hash, "txn": entry.txn},
        "proof": proof, "verified": verify_entry_proof(entry, proof, blk),
    }


def format_table(rows: Sequence[dict], columns: Optional[Sequence[str]] = None) -> str:
    """Render rows as a fixed width text table for the terminal."""
    if not rows:
        return "(no rows)"
    cols = list(columns or rows[0].keys())
    text = [[("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in rows]
    widths = [max(len(c), *(len(t[i]) for t in text)) for i, c in enumerate(cols)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths)).rstrip()
    return "\n".join([line(cols), line(["=" * w for w in widths])] + [line(t) for t in text])
