# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/cli.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: the mongoledger command line: setup, service, fold, verify,
#          history, blocks, digests, prove
# Covers:  FR06, FR12 to FR20; acceptance tests AC01 to AC10 (see docs/D.4.1)
# ============================================================================
"""Command line entry points (installed as ``mongoledger``; also ``python -m ledger.cli``).

Operating the ledger::

  mongoledger --collections accounts setup --heads 4     enable images, indexes, baselines
  mongoledger service  --interval 5                      Design A change stream service
  mongoledger fold     --heads 4 --every 5               Design S sealing loop
  mongoledger verify   --design A|S [--cross]            verify, print a JSON report

Searching and inspecting (auditor tools)::

  mongoledger history  --design A --key 42 [--op update] [--since 2026-11-01] [--json]
  mongoledger blocks   --design A [--last 20] [--json]
  mongoledger digests  --design A [--json]
  mongoledger prove    --design A --key 42   (or --block 3 --idx 0)

Connection settings come from flags or the environment:
  MONGO_URI   default mongodb://localhost:27017/?replicaSet=rs0&directConnection=true
  DATA_DB     default bank

Exit codes: 0 success; 2 verify found at least one alert (or prove failed);
1 usage or lookup error. Errors from the server (for example a refused
connection) are printed with their MongoDB error code.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from pymongo import MongoClient

from .blocks import build_block
from .design_a import enable_images, run_service
from .design_s import SyncLedger
from .digest_log import DigestLog, GitWitness
from .model import GENESIS
from .mongo_store import LedgerStore
from .scan import baseline_entries_for_a, scan_state
from .search import block_rows, digest_rows, format_table, history, key_for, parse_time, prove
from .verifier import cross_check, verify

DEFAULT_URI = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true"


def _client(args) -> MongoClient:
    """Open a client for ``--uri`` (replaced by an in memory stand in in tests)."""
    return MongoClient(args.uri)


def _digests(args, design: str) -> DigestLog:
    """Digest log for a design: ``--digest-log`` or ``digests/design_<a|s>.jsonl``."""
    path = args.digest_log or f"digests/design_{design.lower()}.jsonl"
    witness = GitWitness(args.git_witness) if getattr(args, "git_witness", None) else None
    return DigestLog(path, witness)


def cmd_setup(args) -> None:
    """Prepare both designs for the protected collections (safe to repeat).

    1. Enable before and after images so change events carry full documents.
    2. Design A: write baseline blocks (10,000 documents per block) with the
       current state of every document, publish their digests, and record
       the cluster time from which the change stream must start.
    3. Design S: create k chain heads, record baseline entries and fold
       them into block 0.
    Existing ledgers are left untouched.
    """
    c = _client(args)
    db = c[args.data_db]
    enable_images(db, args.collections)
    # Design A: baseline block, then remember the cluster time to start the stream from
    store_a = LedgerStore(c, "ledger_a")
    store_a.setup()
    if store_a.last_block() is None:
        # The operation time is taken BEFORE the scan, so a write that happens
        # during the scan is seen by the stream (never lost between the two).
        with c.start_session() as s:
            db.command("ping", session=s)
            start = s.operation_time
        base = baseline_entries_for_a(c, args.data_db, args.collections)
        log, prev, chunk = _digests(args, "A"), GENESIS, 10_000
        for n, i in enumerate(range(0, max(len(base), 1), chunk)):
            blk = build_block(n, prev, base[i:i + chunk], time.time())
            store_a.append_block(blk, base[i:i + chunk])
            log.publish(blk)
            prev = blk.block_hash
        store_a.set_start_at(start)
        print(f"Design A: baseline of {len(base)} documents in {n + 1} block(s); stream starts at {start}")
    # Design S: heads and baseline entries
    s = SyncLedger(c, args.data_db, "ledger_s", heads=args.heads)
    s.setup()
    store_s = LedgerStore(c, "ledger_s")
    store_s.setup()
    if store_s.last_block() is None and not s.pending_entries():
        n = s.baseline(args.collections)
        s.fold(store_s, _digests(args, "S"))
        print(f"Design S: {args.heads} chain heads, baseline of {n} documents folded into block 0")


def cmd_service(args) -> None:
    """Run the Design A service until interrupted (or for ``--seconds``)."""
    c = _client(args)
    run_service(c, args.data_db, LedgerStore(c, "ledger_a"), _digests(args, "A"),
                interval=args.interval, max_seconds=args.seconds)


def cmd_fold(args) -> None:
    """Seal Design S pending entries every ``--every`` seconds, and once more on exit."""
    c = _client(args)
    s = SyncLedger(c, args.data_db, "ledger_s", heads=args.heads)
    store = LedgerStore(c, "ledger_s")
    log = _digests(args, "S")
    end = time.time() + args.seconds if args.seconds else None
    while end is None or time.time() < end:
        s.fold(store, log)
        time.sleep(args.every)
    s.fold(store, log)


def run_verify(c, data_db: str, collections, design: str, digest_path: str | None = None, cross: bool = False):
    """Load one ledger, scan the data and run every check.

    Args:
        c: connected client.
        data_db: protected database.
        collections: protected collection names.
        design: "A" or "S".
        digest_path: digest log to compare with (default per design).
        cross: also cross check the Design A ledger against Design S.

    Returns:
        The verifier Report, with ``verify_seconds`` added to its stats.
    """
    design = design.upper()
    store = LedgerStore(c, f"ledger_{design.lower()}")
    blocks, by_block = store.load()
    digests = DigestLog(digest_path or f"digests/design_{design.lower()}.jsonl").read()
    actual = scan_state(c, data_db, collections)
    namespaces = {f"{data_db}.{n}" for n in collections}
    pending, heads = (), None
    if design == "S":
        s = SyncLedger(c, data_db, "ledger_s")
        pending, heads = s.pending_entries(), s.head_state()
    t0 = time.perf_counter()
    report = verify(blocks, by_block, digests, actual, pending=pending, heads=heads, namespaces=namespaces)
    if cross:
        a_blocks, a_by = LedgerStore(c, "ledger_a").load()
        s_blocks, s_by = LedgerStore(c, "ledger_s").load()
        ents = lambda bl, by: [e for b in bl for e in by.get(b.block_id, [])]
        s_all = ents(s_blocks, s_by) + list(SyncLedger(c, data_db, "ledger_s").pending_entries())
        report.findings += cross_check(ents(a_blocks, a_by), s_all)
    report.stats["verify_seconds"] = round(time.perf_counter() - t0, 4)
    return report


def cmd_verify(args) -> int:
    """Print the JSON report; exit 0 when clean, 2 when any alert was found."""
    report = run_verify(_client(args), args.data_db, args.collections, args.design, args.digest_log, args.cross)
    print(json.dumps(report.to_dict(), indent=2, default=str))
    return 0 if report.ok else 2


def _load(args):
    """Load the ledger chosen by ``--design`` plus Design S pending entries."""
    c = _client(args)
    design = args.design.upper()
    blocks, by_block = LedgerStore(c, f"ledger_{design.lower()}").load()
    pending = SyncLedger(c, args.data_db, "ledger_s").pending_entries() if design == "S" else []
    return blocks, by_block, pending


def _emit(args, rows, columns=None) -> None:
    """Print rows as JSON (``--json``) or as a text table."""
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        print(format_table(rows, columns))


def cmd_history(args) -> int:
    """List ledger entries, filtered by document, collection, operation, time and block."""
    blocks, by_block, pending = _load(args)
    rows = history(blocks, by_block, pending,
                   key=key_for(args.key) if args.key is not None else None,
                   ns=f"{args.data_db}.{args.collection}" if args.collection else None,
                   op=args.op, since=parse_time(args.since), until=parse_time(args.until),
                   block_from=args.from_block, block_to=args.to_block, limit=args.limit)
    cols = None if args.full else ["block", "idx", "time", "ns", "key", "op", "post_hash", "source"]
    if not args.full:
        for r in rows:  # shorten hashes for the terminal; --full shows them whole
            r["post_hash"] = (r["post_hash"] or "")[:16]
    _emit(args, rows, cols)
    return 0


def cmd_blocks(args) -> int:
    """List blocks with counts, sealing time and commit to block lag."""
    blocks, _, _ = _load(args)
    _emit(args, block_rows(blocks, args.last))
    return 0


def cmd_digests(args) -> int:
    """List published digests and whether each still matches the ledger."""
    blocks, _, _ = _load(args)
    rows = digest_rows(blocks, _digests(args, args.design).read())
    _emit(args, rows)
    return 0 if all(r["status"] == "ok" for r in rows) else 2


def cmd_prove(args) -> int:
    """Print an inclusion proof for one sealed entry and whether it verifies."""
    blocks, by_block, _ = _load(args)
    try:
        res = prove(blocks, by_block, key=key_for(args.key) if args.key is not None else None,
                    block_id=args.block, idx=args.idx)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, default=str))
    return 0 if res["verified"] else 2


def build_parser() -> argparse.ArgumentParser:
    """Define the global options and one subcommand per operation."""
    p = argparse.ArgumentParser(prog="mongoledger", description="Change stream ledger for MongoDB")
    p.add_argument("--uri", default=os.environ.get("MONGO_URI", DEFAULT_URI), help="MongoDB connection string")
    p.add_argument("--data-db", default=os.environ.get("DATA_DB", "bank"), help="protected database")
    p.add_argument("--collections", type=lambda v: [x for x in v.split(",") if x], default=["accounts"],
                   help="comma separated protected collections, for example accounts,customers")
    p.add_argument("--digest-log", default=None, help="digest log file (default digests/design_<a|s>.jsonl)")
    p.add_argument("--git-witness", default=None, help="path of a git repository to push digests to")
    p.add_argument("-v", "--verbose", action="store_true", help="log every sealed block")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="enable images, create indexes, write baselines")
    sp.add_argument("--heads", type=int, default=1, help="number of Design S chain heads k")
    sp = sub.add_parser("service", help="run the Design A change stream service")
    sp.add_argument("--interval", type=float, default=5.0, help="seconds between blocks")
    sp.add_argument("--seconds", type=float, help="stop after this many seconds")
    sp = sub.add_parser("fold", help="seal Design S pending entries periodically")
    sp.add_argument("--heads", type=int, default=1)
    sp.add_argument("--every", type=float, default=5.0, help="seconds between folds")
    sp.add_argument("--seconds", type=float, help="stop after this many seconds")
    sp = sub.add_parser("verify", help="verify a ledger against digests and data")
    sp.add_argument("--design", choices=["A", "S", "a", "s"], required=True)
    sp.add_argument("--cross", action="store_true", help="report writes that bypassed the Design S library")

    for name, text in (("history", "search ledger entries"), ("blocks", "list blocks"),
                       ("digests", "list published digests"), ("prove", "inclusion proof for an entry")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("--design", choices=["A", "S", "a", "s"], default="A")
        if name in ("history", "prove"):
            sp.add_argument("--key", help='document _id: 42, "text" or {"$oid": "..."}')
        if name == "history":
            sp.add_argument("--collection", help="restrict to one protected collection")
            sp.add_argument("--op", choices=["insert", "update", "replace", "delete", "baseline",
                                             "drop", "rename", "dropDatabase"])
            sp.add_argument("--since", help="ISO date or epoch seconds (UTC)")
            sp.add_argument("--until", help="ISO date or epoch seconds (UTC)")
            sp.add_argument("--from-block", type=int)
            sp.add_argument("--to-block", type=int)
            sp.add_argument("--limit", type=int, help="newest N rows only")
            sp.add_argument("--full", action="store_true", help="show every field and whole hashes")
        if name == "blocks":
            sp.add_argument("--last", type=int, default=20, help="newest N blocks (0 for all)")
        if name == "prove":
            sp.add_argument("--block", type=int)
            sp.add_argument("--idx", type=int)
        if name != "prove":
            sp.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return p


COMMANDS = {"setup": cmd_setup, "service": cmd_service, "fold": cmd_fold, "verify": cmd_verify,
            "history": cmd_history, "blocks": cmd_blocks, "digests": cmd_digests, "prove": cmd_prove}


def main(argv=None) -> int:
    """Parse arguments, configure logging and run one command; returns the exit code."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(message)s")
    if args.cmd == "prove" and args.key is None and (args.block is None or args.idx is None):
        print("error: give --key, or both --block and --idx", file=sys.stderr)
        return 1
    try:
        return COMMANDS[args.cmd](args) or 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # report server errors with their code instead of a traceback
        code = getattr(exc, "code", None)
        print(f"error{f' {code}' if code is not None else ''}: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
