# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    attacks/run_attacks.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: the seven insider actions of D.4.1 Table 2 and their verification
#          outcomes
# Covers:  FR25; acceptance test AC13 (see docs/D.4.1)
# ============================================================================
"""Insider attack suite for the MongoDB designs (run as an administrator).

  python -m attacks.run_attacks bypass        write that skips the Design S library
  python -m attacks.run_attacks edit-ledger   change a sealed ledger entry in each design
  python -m attacks.run_attacks tail          rewrite Design S entries before they are folded
  python -m attacks.run_attacks churn       fill the oplog until older history is gone (service stopped)
  python -m attacks.run_attacks report        verify both designs and record the outcome

Two attacks need the container itself and are scripted in attacks/*.sh:
standalone_write.sh (restart outside replica set mode) and restore_backup.sh.

Every action and every verification result is appended to results/attacks.jsonl,
which feeds the attack matrix in the report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from bson import json_util
from pymongo import DESCENDING, MongoClient

from ledger.cli import DEFAULT_URI, run_verify
from ledger.model import Entry

LOG = Path("results/attacks.jsonl")


def record(kind: str, **detail) -> None:
    """Append one JSON line (time, kind, details) to results/attacks.jsonl."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(), "kind": kind, **detail}, default=str) + "\n")


def bypass(c, db: str) -> None:
    """Attack 1: change the newest SmallBank account directly, skipping the Design S library.

    Expected: Design S reports modified_document; Design A records the change
    and the cross check reports write_outside_library.
    """
    target = c[db]["accounts"].find_one(sort=[("_id", DESCENDING)])
    c[db]["accounts"].update_one({"_id": target["_id"]}, {"$inc": {"checking": 1_000_00}})
    record("attack", name="bypass_write", doc=target["_id"])


def edit_ledger(c) -> None:
    """Attack 3: overwrite the after hash of the newest sealed entry in each ledger.

    Expected: block_content_mismatch for both designs, because the stored
    entries no longer rebuild the block's Merkle root.
    """
    for design in ("a", "s"):
        e = c[f"ledger_{design}"]["entries"].find_one({"block_id": {"$gte": 1}}, sort=[("block_id", DESCENDING)])
        if e:
            c[f"ledger_{design}"]["entries"].update_one({"_id": e["_id"]}, {"$set": {"post_hash": "f" * 64}})
            record("attack", name="edit_sealed_entry", design=design.upper(), block_id=e["block_id"])


def rewrite_tail(c, db: str) -> None:
    """Attack: change a document and rewrite its not yet folded Design S entry consistently.

    The attacker recomputes the entry's hash and the chain head, so the
    chain itself still checks. What remains is the time between commit and
    fold: this attack measures that exposure window, and the result shows
    whether the earlier history (before image) still contradicts the change.
    """
    pend = c["ledger_s"]["pending"]
    d = pend.find_one({"op": "update"}, sort=[("seq", DESCENDING)])
    if not d:
        print("no pending Design S entry: run the workload with the folder stopped, then retry")
        return
    e = Entry.from_doc(d)
    if d["seq"] != c["ledger_s"]["heads"].find_one({"_id": e.head})["seq"]:
        print("entry is not the head of its chain; retry")
        return
    _id = json_util.loads(e.key)["_id"]
    c[db]["accounts"].update_one({"_id": _id}, {"$inc": {"savings": 7_777}})
    from ledger.canonical import doc_hash
    e.post_hash = doc_hash(c[db]["accounts"].find_one({"_id": _id}))
    e.chain = e.chain_hash(e.prev)
    pend.update_one({"_id": d["_id"]}, {"$set": {"post_hash": e.post_hash, "chain": e.chain}})
    c["ledger_s"]["heads"].update_one({"_id": e.head}, {"$set": {"hash": e.chain}})
    record("attack", name="tamper_before_digest", design="S", doc=_id)


def churn(c, mb: int) -> None:
    """Attack 6: insert and delete documents until the oplog has dropped all earlier history.

    Run it while the Design A service is stopped. The attack records the
    current cluster time, then writes and deletes 1,000 documents of about
    1 KB at a time until the oldest oplog entry is newer than that time, so
    the service's resume token is certainly gone when it restarts (error 286)
    and it must seal a gap block; changes made meanwhile are caught by the scan.

    MongoDB trims the oplog only after it has passed its size cap and only
    while writes continue, so a fixed volume is not a reliable stopping rule
    (an earlier version stopped after a fixed count and the oplog sometimes
    kept the old history). ``mb`` is now a safety limit on the volume written.
    """
    coll = c["scratch"]["churn"]
    oplog = c["local"]["oplog.rs"]
    start = c.admin.command("hello")["operationTime"]
    pad = "x" * 1000
    per_batch = 1000 * (len(pad) + 30)   # BSON size of 1,000 inserted documents
    written, rolled = 0, False
    while written < mb * 1_000_000:
        coll.insert_many([{"p": pad} for _ in range(1000)])
        coll.delete_many({})
        written += per_batch
        if written % (50 * per_batch) == 0:   # check every ~50 MB
            first = oplog.find_one(sort=[("$natural", 1)])
            if first and first["ts"] > start:
                rolled = True
                break
    first = oplog.find_one(sort=[("$natural", 1)])
    print(f"wrote about {written // 1_000_000} MB; oplog history before {start} "
          f"{'is gone' if rolled else 'is still present: raise --mb and run again'}")
    record("attack", name="oplog_rollover", megabytes=written // 1_000_000, rolled_over=rolled,
           start_ts=str(start), oldest_oplog_ts=str(first["ts"]) if first else None)


def report(c, db: str) -> None:
    """Verify both designs after an attack and log the outcome for the attack matrix."""
    for design in ("A", "S"):
        r = run_verify(c, db, ["accounts"], design, cross=(design == "A"))
        record("verify", design=design, ok=r.ok, alerts=dict(r.kinds("alert")), warnings=dict(r.kinds("warning")),
               seconds=r.stats["verify_seconds"])
        print(design, "ok" if r.ok else "ALERT", dict(r.kinds("alert")), dict(r.kinds("warning")))


def main(argv=None) -> int:
    """Command line entry: run one attack action or the report."""
    p = argparse.ArgumentParser(prog="attacks.run_attacks")
    p.add_argument("action", choices=["bypass", "edit-ledger", "tail", "churn", "report"])
    p.add_argument("--mb", type=int, default=6000, help="safety limit for churn, in MB")
    p.add_argument("--uri", default=os.environ.get("MONGO_URI", DEFAULT_URI))
    p.add_argument("--data-db", default=os.environ.get("DATA_DB", "bank"))
    a = p.parse_args(argv)
    c = MongoClient(a.uri)
    {"bypass": lambda: bypass(c, a.data_db), "edit-ledger": lambda: edit_ledger(c),
     "tail": lambda: rewrite_tail(c, a.data_db), "churn": lambda: churn(c, a.mb),
     "report": lambda: report(c, a.data_db)}[a.action]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
