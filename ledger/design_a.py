# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/design_a.py
# Owner:   Yadu Pratap (deployment lead)
# Purpose: Design A: change stream service with resume tokens, transaction
#          grouping and gap blocks
# Covers:  FR04, FR05, FR07, FR10, FR11; acceptance tests AC02, AC07, AC08
#          (see docs/D.4.1)
# ============================================================================
"""Design A: asynchronous ledger built from MongoDB change streams.

EventProcessor is pure: it turns change events (as documented in the MongoDB
manual) into ledger entries and seals blocks. run_service connects it to a
real replica set through pymongo.

Change streams report only majority committed writes, so a write that is later
rolled back never reaches the ledger. Events of one multi document transaction
are grouped by lsid and txnNumber, which the MongoDB 8.0 documentation names as
the way to identify a transaction's events. If the resume token has already
left the oplog, the missing events cannot be recovered; the service then seals
an explicit gap block instead of failing silently.

Processing loop (run_service)::

    position = store.start_position()            # token, time, or now
    open change stream at position with before and after images
    repeat:
        event = stream.try_next()
        if event: processor.consume(event)        # event -> Entry
        if interval elapsed: seal block, store it, publish digest
    on history lost (code 286 or 280):
        seal what is pending, seal a gap block, continue from current time

Unlike Design S, the application does not change at all: every committed
write appears in the change stream, including writes that bypass the
application. The price is a delay (lag) between commit and sealing.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .blocks import build_block
from .canonical import canonical_json, doc_hash, key_string
from .model import DATA_OPS, DDL_OPS, GENESIS, UNAVAILABLE, Block, Entry, as_plain

log = logging.getLogger("ledger.design_a")

# ChangeStreamHistoryLost (286) and ChangeStreamFatalError (280): the resume
# point is no longer in the oplog, so the stream cannot continue from it.
HISTORY_LOST_CODES = {286, 280}


def _epoch(value) -> Optional[float]:
    """Convert a change event ``wallTime`` (naive UTC datetime) to epoch seconds."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    return None


def txn_id_of(ev: dict, seq: int) -> str:
    """Transaction identifier for a change event.

    Events from one multi document transaction carry the same ``lsid``
    (logical session id) and ``txnNumber``; together they identify the
    transaction. The lsid is hashed to keep the id short. Events outside a
    transaction get a unique id built from their cluster time and position.

    Returns:
        ``"txn:<lsid hash>:<txnNumber>"`` or ``"op:<t>:<i>:<seq>"``.
    """
    if "txnNumber" in ev and "lsid" in ev:
        lsid = hashlib.sha256(canonical_json(ev["lsid"]).encode()).hexdigest()[:16]
        return f"txn:{lsid}:{int(ev['txnNumber'])}"
    ts = as_plain(ev.get("clusterTime")) or [0, 0]
    return f"op:{ts[0]}:{ts[1]}:{seq}"


def entry_from_event(ev: dict, seq: int) -> Optional[Entry]:
    """Turn one change event into a ledger entry.

    Args:
        ev: change event document as returned by pymongo.
        seq: running event number, used for ids of events outside transactions.

    Returns:
        An Entry, or None for events that change no document (invalidate).
        Missing before or after images are recorded as UNAVAILABLE rather
        than guessed, so the verifier can report them honestly.
    """
    op = ev.get("operationType")
    ns = ev.get("ns") or {}
    ns_str = f"{ns.get('db', '')}.{ns.get('coll', '')}" if ns.get("coll") else ns.get("db", "")
    common = dict(txn=txn_id_of(ev, seq), source="A", ts=as_plain(ev.get("clusterTime")), wall=_epoch(ev.get("wallTime")))
    if op in DDL_OPS:
        # Dropping or renaming a protected collection is recorded as an entry
        # without a document key; the verifier raises an alert for it.
        return Entry(ns=ns_str, key="", op=op, pre_hash=None, post_hash=None, **common)
    if op not in DATA_OPS:
        return None  # invalidate and expanded events carry no document change
    key = key_string(ev["documentKey"])
    pre = None
    if op in ("update", "replace", "delete"):
        pre = doc_hash(ev.get("fullDocumentBeforeChange")) or UNAVAILABLE
    post = None
    if op != "delete":
        post = doc_hash(ev.get("fullDocument")) or UNAVAILABLE
    return Entry(ns=ns_str, key=key, op=op, pre_hash=pre, post_hash=post, **common)


class EventProcessor:
    """Accumulates entries and seals a block every ``interval`` seconds.

    The processor has no database access, so its behaviour (grouping,
    sealing, gap handling) is unit tested with plain dictionaries.
    """

    def __init__(self, interval: float = 5.0, clock: Callable[[], float] = time.time,
                 next_block_id: int = 0, prev_hash: str = GENESIS):
        """
        Args:
            interval: seconds between seals (the digest interval).
            clock: time source, replaceable in tests.
            next_block_id: id for the next block (last stored id plus one).
            prev_hash: hash of the last stored block (GENESIS if none).
        """
        self.interval = interval
        self.clock = clock
        self.next_block_id = next_block_id
        self.prev_hash = prev_hash
        self.pending: list[Entry] = []
        self._tokens: list[Optional[str]] = []   # resume token after each pending entry
        self.last_token: Optional[str] = None
        self.last_ts: Optional[list] = None
        self._seq = 0
        self._opened = clock()

    def consume(self, ev: dict, token: Optional[dict] = None) -> Optional[Entry]:
        """Add one change event; remembers the resume token after it.

        Args:
            ev: change event.
            token: the stream's resume token after this event (defaults to
                the event's ``_id``, which is the same token).

        Returns:
            The entry created, or None for events without a document change.
        """
        self._seq += 1
        e = entry_from_event(ev, self._seq)
        tok = token if token is not None else ev.get("_id")
        if tok is not None:
            self.last_token = canonical_json(tok)
        if ev.get("clusterTime") is not None:
            self.last_ts = as_plain(ev["clusterTime"])
        if e is not None:
            self.pending.append(e)
            self._tokens.append(self.last_token)
        return e

    def due(self) -> bool:
        """True when entries are waiting and the interval has elapsed."""
        return bool(self.pending) and self.clock() - self._opened >= self.interval

    def _advance(self, blk: Block) -> Block:
        """Move the chain forward after sealing ``blk``."""
        self.next_block_id += 1
        self.prev_hash = blk.block_hash
        self._opened = self.clock()
        return blk

    def seal(self, final: bool = True) -> Optional[tuple[Block, list[Entry]]]:
        """Seal pending entries into a data block.

        A multi document transaction must never be split across two blocks.
        Its events arrive one after another in the stream, so when
        ``final`` is False (the stream may still be delivering events) the
        trailing entries of a transaction that might still be open are kept
        pending for the next block. With ``final`` True (the stream is idle
        or the service is stopping) everything is sealed.

        The block stores the resume token of the last event it covers, so a
        restarted service continues exactly after it: entries held back are
        read again rather than lost.

        Returns:
            ``(block, entries)``, or None when nothing could be sealed.
        """
        if not self.pending:
            self._opened = self.clock()
            return None
        cut = len(self.pending)
        if not final and self.pending[-1].txn.startswith("txn:"):
            last = self.pending[-1].txn
            while cut > 0 and self.pending[cut - 1].txn == last:
                cut -= 1
            if cut == 0:
                return None  # only one possibly unfinished transaction: wait for more events
        ents, self.pending = self.pending[:cut], self.pending[cut:]
        token, self._tokens = self._tokens[cut - 1], self._tokens[cut:]
        blk = build_block(self.next_block_id, self.prev_hash, ents, self.clock(), resume_token=token)
        return self._advance(blk), ents

    def seal_gap(self, reason: str, resumed_at: Optional[list] = None) -> tuple[Block, list[Entry]]:
        """Seal an empty gap block that records lost history.

        Args:
            reason: why history was lost, for example ``history lost (code 286)``.
            resumed_at: cluster time ``[t, i]`` from which capture continues.

        Returns:
            ``(gap_block, [])``.
        """
        gap = {"reason": reason, "last_seen_ts": self.last_ts, "resumed_at_ts": resumed_at}
        blk = build_block(self.next_block_id, self.prev_hash, [], self.clock(), resume_token=None, kind="gap", gap=gap)
        self.last_token = None
        return self._advance(blk), []


# ---------------------------------------------------------------------------
# pymongo adapter
# ---------------------------------------------------------------------------

def enable_images(db, collections) -> None:
    """Turn on before and after images (MongoDB 6.0 or later) for each collection.

    Without before images, update and delete events would carry no copy of
    the old document and the verifier could not check the history chain.
    Missing collections are created with the option already enabled.
    """
    existing = set(db.list_collection_names())
    for name in collections:
        if name not in existing:
            db.create_collection(name, changeStreamPreAndPostImages={"enabled": True})
        else:
            db.command("collMod", name, changeStreamPreAndPostImages={"enabled": True})


def run_service(client, data_db: str, store, digest_log, interval: float = 5.0,
                max_seconds: Optional[float] = None, idle_sleep: float = 0.02) -> None:
    """Consume change events forever (or for ``max_seconds``), sealing and publishing blocks.

    Args:
        client: connected MongoClient for the replica set.
        data_db: protected database to watch (all its collections).
        store: LedgerStore of the Design A ledger database.
        digest_log: DigestLog for publishing block digests.
        interval: seconds between sealed blocks.
        max_seconds: stop after this long (None runs until interrupted).
        idle_sleep: pause when no event is waiting, to avoid a busy loop.
    """
    from pymongo.errors import OperationFailure

    # Continue the chain from the last stored block.
    last = store.last_block()
    proc = EventProcessor(interval,
                          next_block_id=(last.block_id + 1) if last else 0,
                          prev_hash=last.block_hash if last else GENESIS)
    pos = store.start_position()
    resume = pos[1] if pos and pos[0] == "token" else None
    start_at = pos[1] if pos and pos[0] == "time" else None
    deadline = time.time() + max_seconds if max_seconds else None
    # "whenAvailable" asks the server for before and after images and returns
    # the event without them (instead of failing) if they are missing.
    opts = dict(full_document="whenAvailable", full_document_before_change="whenAvailable")

    def commit(sealed):
        # Store first, publish second: a digest is never published for a
        # block that is not durably stored.
        """Store a sealed block and its entries, then publish its digest (FR07, FR08)."""
        if sealed:
            blk, ents = sealed
            store.append_block(blk, ents)
            digest_log.publish(blk)
            log.info("block %s sealed: %s entries, lag max %.3fs", blk.block_id, blk.entry_count, blk.lag_max or 0)

    while deadline is None or time.time() < deadline:
        try:
            kwargs = dict(opts)
            if resume is not None:
                kwargs["resume_after"] = resume
            elif start_at is not None:
                kwargs["start_at_operation_time"] = start_at
            with client[data_db].watch(**kwargs) as stream:
                while stream.alive and (deadline is None or time.time() < deadline):
                    ev = stream.try_next()
                    if ev is not None:
                        proc.consume(ev, token=stream.resume_token)
                        resume = stream.resume_token
                    if proc.due():
                        # seal everything only when the stream is idle; otherwise
                        # keep a possibly unfinished transaction for the next block
                        commit(proc.seal(final=ev is None))
                    if ev is None:
                        time.sleep(idle_sleep)
                commit(proc.seal())
                if not stream.alive:  # invalidate (collection or database dropped)
                    resume, start_at = None, None
        except OperationFailure as exc:
            if exc.code not in HISTORY_LOST_CODES:
                raise
            # The oplog no longer holds our resume point: seal what we have,
            # record the gap explicitly, and continue from the current time.
            commit(proc.seal())
            now = client.admin.command("ping")  # any command; operationTime is the cluster time
            commit(proc.seal_gap(f"history lost (code {exc.code})", as_plain(now.get("operationTime"))))
            resume, start_at = None, now.get("operationTime")
            log.warning("change stream history lost; recorded gap block and resumed from now")
    commit(proc.seal())
