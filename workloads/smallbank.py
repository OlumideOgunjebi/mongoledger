# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    workloads/smallbank.py
# Owner:   Yadu Pratap (deployment lead)
# Purpose: SmallBank for MongoDB (plain and Design S) and SQL Server, and the
#          closed loop driver
# Covers:  FR22; acceptance test AC12 (see docs/D.4.1)
# ============================================================================
"""SmallBank workload (Alomari et al., ICDE 2008) for MongoDB and SQL Server.

One record per customer holds savings and checking balances in cents. The
transaction mix follows the common OLTPBench defaults: Amalgamate 15,
Balance 15, DepositChecking 15, SendPayment 25, TransactSavings 15,
WriteCheck 15. Amalgamate and SendPayment touch two customers and therefore
exercise multi document transactions.

Targets
  MongoBank(mode="plain")  pymongo, transactions only for two customer operations
  MongoBank(mode="S")      every write through the Design S library
  SqlBank(table)           pyodbc against dbo.bank_plain or dbo.bank_ledger
Design A uses MongoBank(mode="plain") while the change stream service runs.
"""

from __future__ import annotations

import csv
import random
import statistics
import threading
import time
from typing import Callable

MIX = {"amalgamate": 15, "balance": 15, "deposit_checking": 15,
       "send_payment": 25, "transact_savings": 15, "write_check": 15}
START_BALANCE = 1_000_000  # cents


class MongoBank:
    """SmallBank on MongoDB, one document per customer: {_id, name, savings, checking}.

    mode "plain" writes directly (Design A observes these writes); mode "S"
    routes every write through a SyncLedger so data and ledger commit together.
    """

    def __init__(self, client, db: str = "bank", coll: str = "accounts", mode: str = "plain", ledger=None):
        """Choose plain writes or the Design S library (``mode='S'`` needs ``ledger``)."""
        from pymongo.write_concern import WriteConcern
        self.client = client
        self.mode = mode
        self.ledger = ledger
        self.coll_name = coll
        self.coll = client.get_database(db, write_concern=WriteConcern("majority"))[coll]
        if mode == "S" and ledger is None:
            raise ValueError("mode S needs a SyncLedger")

    def load(self, n: int, batch: int = 5000) -> None:
        """Drop the collection and insert n customers with the starting balances."""
        self.coll.drop()
        for i in range(0, n, batch):
            self.coll.insert_many([{"_id": c, "name": f"cust{c:07d}", "savings": START_BALANCE,
                                    "checking": START_BALANCE} for c in range(i, min(n, i + batch))])

    # ---- helpers
    def _txn(self, fn):
        """Run fn(ops) in one multi document transaction (ledgered in mode S)."""
        if self.mode == "S":
            return self.ledger.transaction(lambda t: fn(_SOps(t, self.coll_name)))
        with self.client.start_session() as s:
            return s.with_transaction(lambda sess: fn(_POps(self.coll, sess)))

    def _single(self, fn):
        """Run a single document write; mode S still uses a transaction for its entry."""
        if self.mode == "S":
            return self._txn(fn)
        return fn(_POps(self.coll, None))

    # ---- SmallBank transactions
    def balance(self, c, **_):
        """Read only: total balance of customer c."""
        d = self.coll.find_one({"_id": c}, {"savings": 1, "checking": 1})
        return d["savings"] + d["checking"]

    def deposit_checking(self, c, v, **_):
        """Add v to the checking balance of customer c."""
        return self._single(lambda o: o.inc(c, "checking", v))

    def transact_savings(self, c, v, **_):
        """Add v (may be negative) to savings, unless that would make it negative."""
        def fn(o):
            """Apply the savings change unless it would make the balance negative."""
            d = o.get(c)
            if d["savings"] + v >= 0:
                o.inc(c, "savings", v)
        return self._txn(fn)

    def amalgamate(self, c1, c2, **_):
        """Move all funds of c1 into the checking account of c2 (two documents)."""
        def fn(o):
            """Zero both balances of c1 and add their total to the checking of c2."""
            a = o.get(c1)
            total = a["savings"] + a["checking"]
            o.set(c1, {"savings": 0, "checking": 0})
            o.inc(c2, "checking", total)
        return self._txn(fn)

    def write_check(self, c, v, **_):
        """Cash a check of v against checking; a 1.00 penalty applies if funds are short."""
        def fn(o):
            """Debit the check, with a 1.00 penalty when funds are short."""
            d = o.get(c)
            o.inc(c, "checking", -(v + 100) if d["savings"] + d["checking"] < v else -v)
        return self._txn(fn)

    def send_payment(self, c1, c2, v, **_):
        """Transfer v from the checking of c1 to c2 if c1 has enough (two documents)."""
        def fn(o):
            """Move v from c1 to c2 when c1 can cover it."""
            if o.get(c1)["checking"] >= v:
                o.inc(c1, "checking", -v)
                o.inc(c2, "checking", v)
        return self._txn(fn)


class _POps:
    """Plain pymongo operations, optionally inside a session transaction."""

    def __init__(self, coll, session):
        """Bind the operations to a collection and an optional session."""
        self.coll, self.s = coll, session

    def get(self, c):
        """Read the document of customer ``c``."""
        return self.coll.find_one({"_id": c}, session=self.s)

    def inc(self, c, field, v):
        """Add ``v`` to one balance field of customer ``c``."""
        self.coll.update_one({"_id": c}, {"$inc": {field: v}}, session=self.s)

    def set(self, c, values):
        """Set several fields of customer ``c``."""
        self.coll.update_one({"_id": c}, {"$set": values}, session=self.s)


class _SOps:
    """The same operations through the Design S library."""

    def __init__(self, txn, coll):
        """Bind the operations to a Design S transaction and a collection name."""
        self.t, self.c = txn, coll

    def get(self, c):
        """Read customer ``c`` inside the ledgered transaction."""
        return self.t.find_one(self.c, {"_id": c})

    def inc(self, c, field, v):
        """Increment a balance through the library, which records the ledger entry."""
        self.t.update_one(self.c, {"_id": c}, {"$inc": {field: v}})

    def set(self, c, values):
        """Set fields through the library, which records the ledger entry."""
        self.t.update_one(self.c, {"_id": c}, {"$set": values})


class SqlBank:
    """SmallBank over pyodbc on dbo.bank_plain or dbo.bank_ledger.

    Rows are locked in key order (UPDLOCK) so two transfers between the same
    customers cannot deadlock each other; serialization failures are retried.
    """

    RETRY_STATES = {"40001", "40P01", "HY000"}

    def __init__(self, conn_str: str, table: str = "dbo.bank_plain"):
        """Store the connection string and the target table (plain or ledger)."""
        import pyodbc
        self.pyodbc = pyodbc
        self.conn = pyodbc.connect(conn_str, autocommit=False)
        self.t = table

    def load(self, n: int, batch: int = 1000) -> None:
        """Delete all rows and insert n customers with the starting balances."""
        cur = self.conn.cursor()
        cur.execute(f"DELETE FROM {self.t}")
        cur.fast_executemany = True
        rows = [(c, f"cust{c:07d}", START_BALANCE, START_BALANCE) for c in range(n)]
        for i in range(0, n, batch):
            cur.executemany(f"INSERT INTO {self.t} (custid, name, savings, checking) VALUES (?, ?, ?, ?)", rows[i:i + batch])
        self.conn.commit()

    def _run(self, fn):
        """Run fn(cursor) as one transaction, retrying deadlocks and serialization failures."""
        for attempt in range(10):
            try:
                cur = self.conn.cursor()
                out = fn(cur)
                self.conn.commit()
                return out
            except self.pyodbc.Error as exc:
                self.conn.rollback()
                if attempt == 9 or (exc.args and exc.args[0] not in self.RETRY_STATES):
                    raise
                time.sleep(0.005 * (attempt + 1))

    def _lock(self, cur, *ids):
        """Lock the given rows in ascending key order and return their balances."""
        rows = {}
        for c in sorted(ids):
            cur.execute(f"SELECT custid, savings, checking FROM {self.t} WITH (UPDLOCK, ROWLOCK) WHERE custid = ?", c)
            r = cur.fetchone()
            rows[r[0]] = {"savings": r[1], "checking": r[2]}
        return rows

    def balance(self, c, **_):
        """Read only: total balance of customer c."""
        return self._run(lambda cur: cur.execute(f"SELECT savings + checking FROM {self.t} WHERE custid = ?", c).fetchone()[0])

    def deposit_checking(self, c, v, **_):
        """Add v to the checking balance of customer c."""
        return self._run(lambda cur: cur.execute(f"UPDATE {self.t} SET checking = checking + ? WHERE custid = ?", v, c))

    def transact_savings(self, c, v, **_):
        """Add v to savings unless that would make it negative."""
        return self._run(lambda cur: cur.execute(
            f"UPDATE {self.t} SET savings = savings + ? WHERE custid = ? AND savings + ? >= 0", v, c, v))

    def amalgamate(self, c1, c2, **_):
        """Move all funds of c1 into the checking account of c2."""
        def fn(cur):
            """Lock both rows in key order, then move the funds."""
            r = self._lock(cur, c1, c2)
            total = r[c1]["savings"] + r[c1]["checking"]
            cur.execute(f"UPDATE {self.t} SET savings = 0, checking = 0 WHERE custid = ?", c1)
            cur.execute(f"UPDATE {self.t} SET checking = checking + ? WHERE custid = ?", total, c2)
        return self._run(fn)

    def write_check(self, c, v, **_):
        """Cash a check of v against checking, with a penalty when funds are short."""
        def fn(cur):
            """Lock the row, then debit the check and any penalty."""
            r = self._lock(cur, c)[c]
            cur.execute(f"UPDATE {self.t} SET checking = checking - ? WHERE custid = ?",
                        v + 100 if r["savings"] + r["checking"] < v else v, c)
        return self._run(fn)

    def send_payment(self, c1, c2, v, **_):
        """Transfer v from the checking of c1 to c2 if c1 has enough."""
        def fn(cur):
            """Lock both rows in key order, then transfer."""
            r = self._lock(cur, c1, c2)
            if r[c1]["checking"] >= v:
                cur.execute(f"UPDATE {self.t} SET checking = checking - ? WHERE custid = ?", v, c1)
                cur.execute(f"UPDATE {self.t} SET checking = checking + ? WHERE custid = ?", v, c2)
        return self._run(fn)


def _pick(rng: random.Random, mix: dict = MIX) -> str:
    """Choose the next transaction type with the weights of ``mix``."""
    names, weights = zip(*mix.items())
    return rng.choices(names, weights)[0]


def run(make_bank: Callable[[], object], customers: int, clients: int, seconds: float,
        seed: int = 7, raw_csv: str | None = None, warmup: float = 5.0, mix: dict = MIX) -> dict:
    """Closed loop benchmark: each client thread issues transactions back to back.

    Each worker builds its own target with ``make_bank`` and repeatedly picks
    an operation from ``mix``, two distinct customer numbers and an amount,
    then times the call. Measurement starts after ``warmup`` seconds so that
    connection setup and cold caches do not distort the results. Failed
    operations are counted but not timed.

    Args:
        make_bank: factory returning an object with one method per mix entry
            (MongoBank, SqlBank or workloads.analytics.AnalyticsApp).
        customers: number of records to choose from.
        clients: number of concurrent worker threads.
        seconds: length of the measured window.
        seed: base random seed; worker w uses seed * 1000 + w.
        raw_csv: optional file for every individual latency.
        warmup: seconds to run before measuring.
        mix: operation weights (SmallBank by default).

    Returns:
        Throughput, latency percentiles (ms), mean latency and error count.
    """
    stop = threading.Event()
    measuring = threading.Event()
    rows: list[tuple] = []
    errors = [0]
    lock = threading.Lock()

    def worker(wid: int):
        """One client thread: issue transactions back to back until the deadline."""
        bank = make_bank()
        rng = random.Random(seed * 1000 + wid)
        local, errs = [], 0
        while not stop.is_set():
            op = _pick(rng, mix)
            c1 = rng.randrange(customers)
            c2 = (c1 + 1 + rng.randrange(customers - 1)) % customers
            v = rng.randrange(1, 10_000)
            t0 = time.perf_counter()
            try:
                getattr(bank, op)(c1=c1, c2=c2, v=v, c=c1)
                ok = True
            except Exception:  # counted, not fatal: the run reports its error rate
                ok, errs = False, errs + 1
            dt = (time.perf_counter() - t0) * 1000
            if measuring.is_set():
                local.append((wid, op, round(dt, 3), ok))
        with lock:
            rows.extend(local)
            errors[0] += errs

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(clients)]
    for t in threads:
        t.start()
    time.sleep(warmup)
    measuring.set()
    t_start = time.perf_counter()
    time.sleep(seconds)
    measuring.clear()
    elapsed = time.perf_counter() - t_start
    stop.set()
    for t in threads:
        t.join()

    lat = sorted(r[2] for r in rows if r[3])
    q = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))] if lat else None
    if raw_csv:
        with open(raw_csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["worker", "op", "latency_ms", "ok"])
            w.writerows(rows)
    return {
        "clients": clients, "seconds": round(elapsed, 2), "ops": len(lat),
        "throughput_ops_s": round(len(lat) / elapsed, 1) if elapsed else 0,
        "p50_ms": q(0.50), "p95_ms": q(0.95), "p99_ms": q(0.99),
        "mean_ms": round(statistics.fmean(lat), 3) if lat else None,
        "errors": errors[0],
    }
