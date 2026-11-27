# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/verifier.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: verification of chain, digests, per head chains and replayed state
#          against a full scan
# Covers:  FR12 to FR17; acceptance tests AC01, AC03 to AC06 (see docs/D.4.1)
# ============================================================================
"""Verification logic, independent of MongoDB.

The verifier answers one question for an auditor: does the protected data
still match its recorded history, and has that history itself been left
intact? It runs four checks, each producing findings:

1. check_chain     blocks link correctly, block hashes and Merkle roots match
                   their entries, every published digest matches its block,
                   and the ledger is not behind the digest log (rollback).
2. check_s_chains  Design S per head hash chains are contiguous and intact.
3. replay          walking the entries in order, every change starts from the
                   state the ledger last recorded for that document.
4. compare_state   the documents in the database equal the state the ledger
                   implies (modified, missing and unexpected documents).

cross_check additionally compares a Design A ledger with a Design S ledger
over the same data: a change seen in the change stream but never written
through the library is a write that bypassed the application.

Every function here works on plain Python objects (Block, Entry, dicts), so
the whole verifier is unit tested without a database.

Finding kinds and severities
----------------------------
alert    something is wrong and needs investigation (the report is not ok)
warning  the ledger cannot vouch for something, but nothing is proven wrong
         (a gap block, a change whose before image was unavailable)
info     useful context (blocks sealed but not yet published, pending entries)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from .blocks import block_root
from .model import DDL_OPS, GENESIS, UNAVAILABLE, Block, Entry

ALERT, WARNING, INFO = "alert", "warning", "info"
Key = tuple  # (ns, key): identifies one protected document


@dataclass
class Finding:
    """One problem or observation reported by the verifier.

    Attributes:
        kind: machine readable name, for example ``modified_document``.
        severity: ``alert``, ``warning`` or ``info``.
        detail: context such as the namespace, document key or block id.
    """

    kind: str
    severity: str
    detail: dict = field(default_factory=dict)


@dataclass
class Report:
    """Result of one verification run."""

    findings: list[Finding]
    stats: dict

    @property
    def alerts(self) -> list[Finding]:
        """Only the findings with severity alert."""
        return [f for f in self.findings if f.severity == ALERT]

    @property
    def ok(self) -> bool:
        """True when there is no alert (warnings and info do not fail a run)."""
        return not self.alerts

    def kinds(self, severity: Optional[str] = None) -> Counter:
        """Count findings by kind, optionally for one severity only."""
        return Counter(f.kind for f in self.findings if severity is None or f.severity == severity)

    def to_dict(self) -> dict:
        """JSON serializable form printed by ``verify``."""
        return {"ok": self.ok, "stats": self.stats, "findings": [asdict(f) for f in self.findings]}


def check_chain(blocks: Sequence[Block], entries_by_block: Mapping[int, Sequence[Entry]],
                digests: Sequence[dict]) -> list[Finding]:
    """Check the block chain and compare it with the external digest log.

    Args:
        blocks: every stored block.
        entries_by_block: stored entries keyed by block id.
        digests: records read from the digest log.

    Returns:
        Findings: block_sequence, broken_link, block_hash_mismatch,
        block_content_mismatch, digest_without_block and digest_mismatch
        (alerts); gap (warning); unpublished_tail (info).
    """
    out: list[Finding] = []
    prev, expected_id = GENESIS, 0
    for b in sorted(blocks, key=lambda x: x.block_id):
        # Block numbers must be contiguous from 0: a missing number means a
        # block was deleted from the middle of the chain.
        if b.block_id != expected_id:
            out.append(Finding("block_sequence", ALERT, {"expected": expected_id, "found": b.block_id}))
        # Each header must name the hash of the block before it.
        if b.prev_hash != prev:
            out.append(Finding("broken_link", ALERT, {"block_id": b.block_id}))
        # The stored hash must match the header as it is stored now.
        if b.compute_hash() != b.block_hash:
            out.append(Finding("block_hash_mismatch", ALERT, {"block_id": b.block_id}))
        # The stored entries must rebuild exactly the stored Merkle root.
        ents = entries_by_block.get(b.block_id, [])
        if len(ents) != b.entry_count or block_root(ents).hex() != b.root:
            out.append(Finding("block_content_mismatch", ALERT, {"block_id": b.block_id}))
        if b.kind == "gap":
            out.append(Finding("gap", WARNING, {"block_id": b.block_id, **(b.gap or {})}))
        prev, expected_id = b.block_hash, b.block_id + 1

    # The digest log is the part the administrator cannot rewrite. A digest
    # whose block is gone means the ledger was rolled back; a digest that
    # differs means the block was rewritten after publication, even if the
    # rewrite was internally consistent (hashes recomputed).
    by_id = {b.block_id: b for b in blocks}
    for d in digests:
        b = by_id.get(d["block_id"])
        if b is None:
            out.append(Finding("digest_without_block", ALERT, {"block_id": d["block_id"],
                               "hint": "ledger is behind the digest log: rollback, restore or deletion"}))
        elif b.block_hash != d["block_hash"]:
            out.append(Finding("digest_mismatch", ALERT, {"block_id": d["block_id"]}))
    # Blocks sealed after the last published digest are protected by the
    # chain only, not yet by the outside record: report the exposure window.
    last_pub = max((d["block_id"] for d in digests), default=-1)
    tail = [b for b in blocks if b.block_id > last_pub]
    if tail:
        out.append(Finding("unpublished_tail", INFO, {"blocks": len(tail),
                           "entries": sum(b.entry_count for b in tail)}))
    return out


def check_s_chains(entries: Iterable[Entry], heads: Mapping[int, dict]) -> list[Finding]:
    """Check every Design S chain head and the links between its entries.

    Each entry on head h carries ``seq`` (1, 2, 3, ...), ``prev`` (the chain
    hash of the entry before it) and ``chain`` (its own link). The head
    document stores the newest ``seq`` and ``hash``. Together they let the
    verifier detect a removed, reordered or edited entry even before the
    entry has been sealed into a block.

    Args:
        entries: sealed entries followed by pending entries.
        heads: ``{head_number: {"seq": n, "hash": hex}}`` from the heads collection.

    Returns:
        Findings s_chain_gap, s_chain_link, s_chain_hash, s_head_mismatch (alerts).
    """
    out: list[Finding] = []
    per_head: dict[int, list[Entry]] = {}
    for e in entries:
        if e.head is not None:
            per_head.setdefault(e.head, []).append(e)
    for h in sorted(set(per_head) | set(heads)):
        chain = sorted(per_head.get(h, []), key=lambda e: e.seq)
        prev, expected = GENESIS, 1
        for e in chain:
            if e.seq != expected:
                out.append(Finding("s_chain_gap", ALERT, {"head": h, "expected_seq": expected, "found": e.seq}))
            if e.prev != prev:
                out.append(Finding("s_chain_link", ALERT, {"head": h, "seq": e.seq}))
            if e.chain != e.chain_hash(e.prev or GENESIS):
                out.append(Finding("s_chain_hash", ALERT, {"head": h, "seq": e.seq}))
            prev, expected = e.chain, (e.seq or 0) + 1
        # The head must point at the last entry found; otherwise entries were
        # removed from the end of the chain (or added without updating it).
        head = heads.get(h)
        if head is not None and (head.get("hash") != prev or head.get("seq") != expected - 1):
            out.append(Finding("s_head_mismatch", ALERT, {"head": h}))
    return out


def replay(entries: Iterable[Entry]) -> tuple[dict[Key, Optional[str]], list[Finding]]:
    """Replay the ledger to compute the state every protected document should have.

    Walking the entries in commit order, each change must start from the
    state the ledger last recorded for that document (its ``pre_hash`` must
    equal the previous ``post_hash``). A mismatch means the document was
    changed between two recorded versions by something the ledger did not see.

    Returns:
        ``(expected_state, findings)`` where expected_state maps each
        document to its expected hash. Findings: insert_over_existing,
        history_break and protected_collection_<op> (alerts);
        image_unavailable (warning).
    """
    state: dict[Key, Optional[str]] = {}
    out: list[Finding] = []
    for e in entries:
        if e.op in DDL_OPS:
            # Dropping or renaming a protected collection removes every
            # document of it from the expected state and is always an alert.
            prefix = e.ns + "." if e.op == "dropDatabase" else None
            for k in [k for k in state if (k[0] == e.ns if prefix is None else k[0].startswith(prefix))]:
                del state[k]
            out.append(Finding("protected_collection_" + e.op, ALERT, {"op": e.op, "ns": e.ns, "ts": e.ts}))
            continue
        k = (e.ns, e.key)
        if e.op == "baseline":
            # Starting state recorded at setup: nothing to compare against.
            state[k] = e.post_hash
            continue
        if e.op == "insert":
            if k in state:
                out.append(Finding("insert_over_existing", ALERT, {"ns": e.ns, "key": e.key, "txn": e.txn}))
        elif e.pre_hash == UNAVAILABLE:
            # MongoDB could not supply the before image, so this step cannot
            # be checked; the after image still sets the new expected state.
            out.append(Finding("image_unavailable", WARNING, {"ns": e.ns, "key": e.key, "op": e.op}))
        elif k not in state:
            out.append(Finding("history_break", ALERT, {"ns": e.ns, "key": e.key, "op": e.op,
                               "reason": "change to a document the ledger never saw"}))
        elif e.pre_hash != state[k]:
            out.append(Finding("history_break", ALERT, {"ns": e.ns, "key": e.key, "op": e.op,
                               "reason": "document changed between two recorded versions"}))
        if e.op == "delete":
            state.pop(k, None)
        else:
            state[k] = e.post_hash
    return state, out


def compare_state(expected: Mapping[Key, Optional[str]], actual: Mapping[Key, str],
                  namespaces: Optional[set] = None) -> list[Finding]:
    """Compare the replayed state with a full scan of the database.

    This is the check that catches changes made without any change event or
    ledger entry at all (for example a write made while the node ran outside
    the replica set).

    Args:
        expected: result of ``replay``.
        actual: result of ``scan.scan_state``.
        namespaces: limit the comparison to these ``db.collection`` names.

    Returns:
        Findings missing_document, modified_document, unexpected_document
        (alerts) and unverifiable_document (warning).
    """
    out: list[Finding] = []
    in_scope = (lambda k: k[0] in namespaces) if namespaces else (lambda k: True)
    for k, h in expected.items():
        if not in_scope(k):
            continue
        if k not in actual:
            out.append(Finding("missing_document", ALERT, {"ns": k[0], "key": k[1]}))
        elif h == UNAVAILABLE:
            out.append(Finding("unverifiable_document", WARNING, {"ns": k[0], "key": k[1]}))
        elif actual[k] != h:
            out.append(Finding("modified_document", ALERT, {"ns": k[0], "key": k[1]}))
    for k in actual:
        if in_scope(k) and k not in expected:
            out.append(Finding("unexpected_document", ALERT, {"ns": k[0], "key": k[1]}))
    return out


def cross_check(entries_a: Iterable[Entry], entries_s: Iterable[Entry]) -> list[Finding]:
    """Changes seen by the change stream (A) that the library (S) never wrote.

    Both ledgers describe the same writes when every write goes through the
    library. Each change is matched by its signature (namespace, key,
    operation, before hash, after hash); a Design A change with no unused
    Design S partner is reported as ``write_outside_library``.
    """
    sig = lambda e: (e.ns, e.key, e.op, e.pre_hash, e.post_hash)
    written = Counter(sig(e) for e in entries_s if e.op not in ("baseline",) + DDL_OPS)
    out: list[Finding] = []
    for e in entries_a:
        if e.op in ("baseline",) + DDL_OPS:
            continue
        s = sig(e)
        if written[s]:
            written[s] -= 1       # consume one matching library write
        else:
            out.append(Finding("write_outside_library", ALERT, {"ns": e.ns, "key": e.key, "op": e.op,
                               "ts": e.ts, "wall": e.wall}))
    return out


def verify(blocks: Sequence[Block], entries_by_block: Mapping[int, Sequence[Entry]], digests: Sequence[dict],
           actual_state: Mapping[Key, str], pending: Sequence[Entry] = (), heads: Optional[Mapping[int, dict]] = None,
           namespaces: Optional[set] = None) -> Report:
    """Run every check and combine the findings into one Report.

    Args:
        blocks, entries_by_block: the stored ledger (``LedgerStore.load``).
        digests: records from the external digest log.
        actual_state: full scan of the protected collections.
        pending: Design S entries committed but not yet sealed.
        heads: Design S head documents; None for Design A.
        namespaces: protected ``db.collection`` names.

    Returns:
        A Report whose ``ok`` is False when any alert was found.
    """
    ordered = [e for b in sorted(blocks, key=lambda x: x.block_id) for e in entries_by_block.get(b.block_id, [])]
    ordered += list(pending)
    findings = check_chain(blocks, entries_by_block, digests)
    if heads is not None:
        findings += check_s_chains(ordered, heads)
    expected, replay_findings = replay(ordered)
    findings += replay_findings
    findings += compare_state(expected, actual_state, namespaces)
    if pending:
        findings.append(Finding("pending_entries", INFO, {"entries": len(pending)}))
    stats = {
        "blocks": len(blocks), "entries": len(ordered), "digests": len(digests),
        "documents_scanned": len(actual_state), "documents_expected": len(expected),
        "last_published_block": max((d["block_id"] for d in digests), default=None),
    }
    return Report(findings, stats)
