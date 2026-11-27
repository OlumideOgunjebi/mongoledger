# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    workloads/analytics.py
# Owner:   Yadu Pratap (deployment lead)
# Purpose: trading workload over the MongoDB sample_analytics dataset
# Covers:  FR23; acceptance test AC12 (see docs/D.4.1)
# ============================================================================
"""Trading workload over MongoDB's sample_analytics dataset.

The dataset
-----------
``sample_analytics`` is the synthetic financial services dataset that MongoDB
publishes for Atlas ("Load Sample Data"). It models a brokerage:

``accounts``      1,746 documents: account_id, limit, products (list)
``customers``       500 documents: name, address, birthdate, email, the list
                    of account ids the customer owns, tier details
``transactions``  1,746 documents in the bucket pattern: one document per
                  account holds up to 100 trades in an array, with
                  transaction_count and the bucket's start and end dates

Source: MongoDB Atlas sample data
(https://www.mongodb.com/docs/atlas/sample-data/sample-analytics/), taken as
the JSON export in https://github.com/neelabalan/mongodb-sample-dataset
(folder sample_analytics). The files are stored gzipped, one canonical
Extended JSON document per line, in ``datasets/sample_analytics/``. All people
in it are fictitious.

Why this workload
-----------------
SmallBank documents are small and flat. These documents are nested and grow:
a trade appends to an array inside a bucket of up to 100 trades, so every
ledgered update hashes a large document. That exposes the cost of hashing
full before and after images, which SmallBank cannot show.

Operation mix (weights)::

    trade         50  append a trade to the account's open bucket, or open a
                      new bucket when the current one holds 100 trades
    move_limit    15  move credit limit between two accounts (two documents,
                      one transaction)
    add_product   10  add a product to an account ($addToSet)
    portfolio     25  read the customer, the account and its newest bucket

Usage::

    python -m workloads.analytics load --db analytics
    python -m workloads.analytics run --db analytics --mode S --heads 4 --clients 8 --seconds 30
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from bson import json_util

DATASET_DIR = Path(__file__).resolve().parents[1] / "datasets" / "sample_analytics"
COLLECTIONS = ("accounts", "customers", "transactions")
MIX = {"trade": 50, "move_limit": 15, "add_product": 10, "portfolio": 25}
SYMBOLS = ("aapl", "adbe", "amd", "amzn", "crm", "csco", "ebay", "goog", "ibm", "intc",
           "msft", "nflx", "nvda", "sap", "team", "znga")
PRODUCTS = ("Brokerage", "Commodity", "CurrencyService", "Derivatives", "InvestmentFund", "InvestmentStock")
BUCKET_SIZE = 100


def read_dataset(name: str, path: Path = DATASET_DIR, limit: Optional[int] = None) -> Iterator[dict]:
    """Yield documents of one collection from ``<path>/<name>.json.gz``.

    Each line is canonical Extended JSON, so ``json_util.loads`` restores the
    original BSON types (ObjectId, int32, dates).
    """
    with gzip.open(Path(path) / f"{name}.json.gz", "rt", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if limit is not None and i >= limit:
                break
            if line.strip():
                yield json_util.loads(line)


def load(client, db: str = "analytics", path: Path = DATASET_DIR, limit: Optional[int] = None,
         batch: int = 500) -> dict:
    """Drop and reload the three collections, then create the lookup indexes.

    Args:
        client: connected MongoClient.
        db: target database.
        path: folder holding the gzipped files.
        limit: load only the first N documents of each collection (tests).
        batch: documents per insert_many call.

    Returns:
        ``{collection: documents_loaded}``.
    """
    counts = {}
    for name in COLLECTIONS:
        coll = client[db][name]
        coll.drop()
        buf, n = [], 0
        for doc in read_dataset(name, path, limit):
            buf.append(doc)
            if len(buf) >= batch:
                coll.insert_many(buf)
                n, buf = n + len(buf), []
        if buf:
            coll.insert_many(buf)
            n += len(buf)
        counts[name] = n
    # Indexes for the lookups the workload performs on every operation.
    client[db]["accounts"].create_index("account_id")
    client[db]["transactions"].create_index([("account_id", 1), ("transaction_count", 1)])
    client[db]["customers"].create_index("accounts")
    return counts


class _Plain:
    """Plain pymongo operations with the same signatures as the Design S ``_Txn``."""

    def __init__(self, db, session):
        """Bind the operations to a database and an optional session."""
        self.db, self.s = db, session

    def find_one(self, coll, flt):
        """Read one document of ``coll`` that matches ``flt``."""
        return self.db[coll].find_one(flt, session=self.s)

    def update_one(self, coll, flt, update):
        """Update one document of ``coll``."""
        return self.db[coll].update_one(flt, update, session=self.s)

    def insert_one(self, coll, doc):
        """Insert one document into ``coll``."""
        return self.db[coll].insert_one(doc, session=self.s).inserted_id


class AnalyticsApp:
    """The trading application, either unprotected ("plain") or through Design S ("S").

    Methods accept the keyword arguments that ``workloads.smallbank.run``
    passes (c1, c2, v, c), so the same closed loop harness drives both
    workloads. ``c1`` and ``c2`` index into the sorted list of account ids.
    """

    def __init__(self, client, db: str = "analytics", mode: str = "plain", ledger=None):
        """
        Args:
            client: connected MongoClient.
            db: database loaded with ``load``.
            mode: "plain" (Design A observes these writes) or "S".
            ledger: a SyncLedger for the same database when mode is "S".
        """
        from pymongo.write_concern import WriteConcern
        if mode == "S" and ledger is None:
            raise ValueError("mode S needs a SyncLedger")
        self.client, self.mode, self.ledger = client, mode, ledger
        self.db = client.get_database(db, write_concern=WriteConcern("majority"))
        self.ids = sorted({d["account_id"] for d in self.db["accounts"].find({}, {"account_id": 1})})

    def _txn(self, fn):
        """Run ``fn(ops)`` in one transaction (ledgered in mode S)."""
        if self.mode == "S":
            return self.ledger.transaction(fn)
        with self.client.start_session() as s:
            return s.with_transaction(lambda sess: fn(_Plain(self.db, sess)))

    def _account(self, i: int) -> int:
        """Map a random index to one of the loaded account ids."""
        return self.ids[i % len(self.ids)]

    def trade(self, c1, v, **_):
        """Append one trade to the account's open bucket, or open a new bucket."""
        aid = self._account(c1)
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, as pymongo returns dates
        price = 10 + (v % 9000) / 100
        amount = 1 + v % 500
        trade = {"date": now, "amount": amount, "transaction_code": "buy" if v % 2 else "sell",
                 "symbol": SYMBOLS[v % len(SYMBOLS)], "price": f"{price:.4f}", "total": f"{price * amount:.4f}"}

        def fn(o):
            """Append the trade to the account's open bucket inside one transaction."""
            bucket = o.find_one("transactions", {"account_id": aid, "transaction_count": {"$lt": BUCKET_SIZE}})
            if bucket is None:
                # Bucket pattern: a full bucket stays as it is and a new one starts.
                o.insert_one("transactions", {"account_id": aid, "transaction_count": 1, "bucket_start_date": now,
                                              "bucket_end_date": now, "transactions": [trade]})
            else:
                o.update_one("transactions", {"_id": bucket["_id"]},
                             {"$push": {"transactions": trade}, "$inc": {"transaction_count": 1},
                              "$set": {"bucket_end_date": now}})
        return self._txn(fn)

    def move_limit(self, c1, c2, v, **_):
        """Move credit limit between two accounts in one transaction (if covered)."""
        a, b = self._account(c1), self._account(c2)
        amount = 100 * (1 + v % 10)

        def fn(o):
            """Move limit between two accounts inside one transaction."""
            src = o.find_one("accounts", {"account_id": a})
            if src is not None and a != b and src["limit"] >= amount:
                o.update_one("accounts", {"_id": src["_id"]}, {"$inc": {"limit": -amount}})
                o.update_one("accounts", {"account_id": b}, {"$inc": {"limit": amount}})
        return self._txn(fn)

    def add_product(self, c1, v, **_):
        """Add a product to an account; no change if it already has it."""
        aid = self._account(c1)
        return self._txn(lambda o: o.update_one("accounts", {"account_id": aid},
                                                {"$addToSet": {"products": PRODUCTS[v % len(PRODUCTS)]}}))

    def portfolio(self, c1, **_):
        """Read only: customer, account and newest bucket (never ledgered)."""
        aid = self._account(c1)
        o = _Plain(self.db, None)
        cust = o.find_one("customers", {"accounts": aid})
        acct = o.find_one("accounts", {"account_id": aid})
        newest = self.db["transactions"].find_one({"account_id": aid}, sort=[("bucket_end_date", -1)])
        return cust, acct, newest


def main(argv=None) -> int:
    """Command line: ``load`` the dataset or ``run`` the workload for a while."""
    from pymongo import MongoClient

    from ledger.cli import DEFAULT_URI
    from ledger.design_s import SyncLedger
    from workloads.smallbank import run

    p = argparse.ArgumentParser(prog="workloads.analytics")
    p.add_argument("action", choices=["load", "run"])
    p.add_argument("--uri", default=os.environ.get("MONGO_URI", DEFAULT_URI))
    p.add_argument("--db", default="analytics")
    p.add_argument("--path", default=str(DATASET_DIR), help="folder with the .json.gz files")
    p.add_argument("--limit", type=int, help="load only the first N documents per collection")
    p.add_argument("--mode", choices=["plain", "S"], default="plain")
    p.add_argument("--heads", type=int, default=1)
    p.add_argument("--clients", type=int, default=8)
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--warmup", type=float, default=5)
    a = p.parse_args(argv)
    client = MongoClient(a.uri)
    if a.action == "load":
        print(json.dumps(load(client, a.db, Path(a.path), a.limit)))
        return 0
    ledger = SyncLedger(client, a.db, "ledger_s", heads=a.heads) if a.mode == "S" else None
    n = len(AnalyticsApp(client, a.db, a.mode, ledger).ids)
    res = run(lambda: AnalyticsApp(client, a.db, a.mode, ledger), n, a.clients, a.seconds, warmup=a.warmup, mix=MIX)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
