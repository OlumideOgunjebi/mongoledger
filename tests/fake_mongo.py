# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/fake_mongo.py
# Owner:   All members (shared test support)
# Purpose: in memory stand in for the parts of pymongo the ledger uses
# Covers:  NFR11 (see docs/D.4.1)
# ============================================================================
"""A small in-memory stand in for the parts of pymongo this project uses.

It is not a MongoDB emulator. It exists so the ledger logic can be tested
without a server: single document CRUD with $set, $inc, $setOnInsert, $push and $addToSet,
transactions that roll back on error, an operation time, and change events
shaped as documented in the MongoDB manual (with before and after images and,
inside transactions, lsid and txnNumber). Events are emitted on commit.
Integration tests in tests/integration run the same code against a real
replica set.
"""

from __future__ import annotations

import copy
import itertools
import uuid
from datetime import datetime, timezone

from bson.binary import Binary, UUID_SUBTYPE
from bson.objectid import ObjectId
from bson.timestamp import Timestamp


class _Result:
    """Result object with the attributes pymongo results expose (inserted_id, matched_count, ...)."""
    def __init__(self, **kw):
        """Store every keyword argument as an attribute."""
        self.__dict__.update(kw)


def _match(doc, flt):
    """Return True when ``doc`` satisfies ``flt``.

    Supports equality, $ne, $in, $gte and $lt, and matching a scalar against array
    elements, which is all the ledger and its tests use.
    """
    for k, v in (flt or {}).items():
        val = doc.get(k)
        if isinstance(v, dict) and any(str(x).startswith("$") for x in v):
            for op, arg in v.items():
                if op == "$ne" and val == arg:
                    return False
                if op == "$in" and val not in arg:
                    return False
                if op == "$gte" and not (val is not None and val >= arg):
                    return False
                if op == "$lt" and not (val is not None and val < arg):
                    return False
        elif isinstance(val, list) and not isinstance(v, list):
            if v not in val:  # MongoDB matches a scalar against array elements
                return False
        elif val != v:
            return False
    return True


class FakeCursor:
    """Minimal cursor over a list of documents: sort, limit and iteration."""
    def __init__(self, docs):
        """Keep a list of the matching documents."""
        self.docs = docs

    def sort(self, key, direction=None):
        """Sort by one key, ascending (1) or descending (-1), as pymongo does."""
        keys = key if isinstance(key, list) else [(key, direction or 1)]
        for k, d in reversed(keys):
            self.docs.sort(key=lambda x: (x.get(k) is None, x.get(k)), reverse=d == -1)
        return self

    def limit(self, n):
        """Keep only the first ``n`` documents (0 means no limit)."""
        self.docs = self.docs[:n]
        return self

    def __iter__(self):
        """Yield deep copies, so callers cannot change stored documents by accident."""
        return iter(self.docs)


class FakeCollection:
    """One collection: a dictionary of documents keyed by _id that emits change events."""
    def __init__(self, db, name):
        """Create an empty collection inside ``db``."""
        self.db, self.name = db, name
        self.docs: dict = {}

    @property
    def client(self):
        """The FakeClient that owns this collection."""
        return self.db.client

    def _emit(self, op, doc_id, before, after, session):
        """Queue a change event (inside a transaction) or publish it at once."""
        self.client._event(self.db.name, self.name, op, doc_id, before, after, session)

    def create_index(self, *a, **k):
        """Accept index definitions; uniqueness is not enforced by the stand in."""
        return "idx"

    def drop(self, session=None):
        """Remove every document of the collection."""
        self.docs = {}

    def insert_one(self, doc, session=None):
        """Insert one document (an ObjectId _id is added when missing) and emit an insert event."""
        doc = copy.deepcopy(doc)
        doc.setdefault("_id", ObjectId())
        if doc["_id"] in self.docs:
            raise ValueError("duplicate key")
        self.docs[doc["_id"]] = doc
        self._emit("insert", doc["_id"], None, doc, session)
        return _Result(inserted_id=doc["_id"])

    def insert_many(self, docs, session=None):
        """Insert each document in turn with insert_one."""
        return _Result(inserted_ids=[self.insert_one(d, session=session).inserted_id for d in docs])

    def find(self, flt=None, projection=None, session=None):
        """Return a cursor over matching documents, optionally projected."""
        out = []
        for d in self.docs.values():
            if _match(d, flt):
                d = copy.deepcopy(d)
                if projection and projection.get("_id") == 0:
                    d.pop("_id", None)
                out.append(d)
        return FakeCursor(out)

    def find_one(self, flt=None, projection=None, sort=None, session=None):
        """Return the first matching document after an optional sort, or None."""
        cur = self.find(flt, projection)
        if sort:
            cur.sort(sort)
        return next(iter(cur), None)

    def _apply(self, doc, update, inserting=False):
        """Apply $set, $inc, $setOnInsert, $push and $addToSet to a copy of ``doc``."""
        for op, fields in update.items():
            for k, v in fields.items():
                if op == "$set" or (op == "$setOnInsert" and inserting):
                    doc[k] = v
                elif op == "$inc":
                    doc[k] = doc.get(k, 0) + v
                elif op == "$push":
                    doc.setdefault(k, []).append(copy.deepcopy(v))
                elif op == "$addToSet":
                    if v not in doc.setdefault(k, []):
                        doc[k].append(copy.deepcopy(v))

    def update_one(self, flt, update, upsert=False, session=None):
        """Update the first match (or upsert) and emit an update or insert event."""
        target = next((d for d in self.docs.values() if _match(d, flt)), None)
        if target is None:
            if not upsert:
                return _Result(matched_count=0, modified_count=0)
            new = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            self._apply(new, update, inserting=True)
            return _Result(matched_count=0, modified_count=0, upserted_id=self.insert_one(new, session).inserted_id)
        before = copy.deepcopy(target)
        self._apply(target, update)
        self._emit("update", target["_id"], before, target, session)
        return _Result(matched_count=1, modified_count=1)

    def replace_one(self, flt, replacement, session=None):
        """Replace the first match and emit a replace event."""
        target = next((d for d in self.docs.values() if _match(d, flt)), None)
        if target is None:
            return _Result(matched_count=0)
        before = copy.deepcopy(target)
        new = copy.deepcopy(replacement)
        new["_id"] = target["_id"]
        self.docs[target["_id"]] = new
        self._emit("replace", new["_id"], before, new, session)
        return _Result(matched_count=1)

    def delete_one(self, flt, session=None):
        """Delete the first match and emit a delete event."""
        target = next((d for d in self.docs.values() if _match(d, flt)), None)
        if target is None:
            return _Result(deleted_count=0)
        del self.docs[target["_id"]]
        self._emit("delete", target["_id"], target, None, session)
        return _Result(deleted_count=1)

    def delete_many(self, flt, session=None):
        """Delete every match, emitting one delete event each."""
        ids = [d["_id"] for d in self.docs.values() if _match(d, flt)]
        for i in ids:
            self.delete_one({"_id": i}, session=session)
        return _Result(deleted_count=len(ids))

    def count_documents(self, flt=None, session=None):
        """Count the documents that match ``flt``."""
        return sum(1 for d in self.docs.values() if _match(d, flt))


class FakeDB:
    """One database: a dictionary of FakeCollection objects plus command and watch."""
    def __init__(self, client, name):
        """Create an empty database owned by ``client``."""
        self.client, self.name = client, name
        self.colls: dict = {}

    def __getitem__(self, name):
        """Return the named collection, creating it on first use as MongoDB does."""
        return self.colls.setdefault(name, FakeCollection(self, name))

    def list_collection_names(self):
        """Names of the collections created so far."""
        return list(self.colls)

    def create_collection(self, name, **kw):
        """Create a collection; options such as changeStreamPreAndPostImages are accepted."""
        return self[name]

    def command(self, *a, session=None, **k):
        """Answer ping, collMod and similar commands; advance the session operation time."""
        if session is not None:
            session.operation_time = self.client._tick()
        return {"ok": 1, "operationTime": self.client._now()}

    def watch(self, resume_after=None, start_at_operation_time=None, **kw):
        """Open a change stream on this database from a resume token or a cluster time."""
        return FakeChangeStream(self.client, self.name, resume_after, start_at_operation_time)


class FakeChangeStream:
    """Replays client.events for one database. Raises history lost (code 286)
    when the resume token is older than client.oplog_floor."""

    def __init__(self, client, db, resume_after, start_at):
        """Position the stream; raise code 286 if the resume point has left the oplog."""
        from pymongo.errors import OperationFailure
        self.client, self.db, self.alive = client, db, True
        self.pos = 0
        if resume_after is not None:
            idx = next((i for i, e in enumerate(client.events) if e["_id"] == resume_after), None)
            if idx is None or idx < client.oplog_floor:
                raise OperationFailure("resume point no longer in the oplog", code=286)
            self.pos = idx + 1
        elif start_at is not None:
            self.pos = next((i for i, e in enumerate(client.events) if e["clusterTime"] > start_at), len(client.events))
            self.pos = max(self.pos, client.oplog_floor)
        self.resume_token = None

    def __enter__(self):
        """Support ``with db.watch() as stream``."""
        return self

    def __exit__(self, *exc):
        """Close the stream; exceptions are not suppressed."""
        return False

    def try_next(self):
        """Return the next event of this database, or None when there is none yet."""
        while self.pos < len(self.client.events):
            ev = self.client.events[self.pos]
            self.pos += 1
            if ev["ns"]["db"] == self.db:
                self.resume_token = ev["_id"]
                return copy.deepcopy(ev)
        return None


class FakeSession:
    """Client session with a logical session id, a transaction number and buffered events."""
    def __init__(self, client):
        """Create a session with a random lsid and no open transaction."""
        self.client = client
        self.lsid = {"id": Binary(uuid.uuid4().bytes, UUID_SUBTYPE)}
        self.txn_number = 0
        self.in_txn = False
        self.buffer: list = []
        self.operation_time = None

    def __enter__(self):
        """Support ``with client.start_session() as s``."""
        return self

    def __exit__(self, *exc):
        """End the session; exceptions are not suppressed."""
        return False

    def with_transaction(self, callback, **kwargs):
        """Run ``callback`` as one transaction.

        On an exception every collection is restored from a snapshot (rollback); on
        success all buffered events are published with one shared commit time, as a
        real replica set reports a committed transaction.
        """
        snapshot = copy.deepcopy({n: {c: dict(coll.docs) for c, coll in db.colls.items()}
                                  for n, db in self.client.dbs.items()})
        self.txn_number += 1
        self.in_txn, self.buffer = True, []
        try:
            result = callback(self)
        except Exception:
            for n, db in self.client.dbs.items():
                for c, coll in db.colls.items():
                    coll.docs = snapshot.get(n, {}).get(c, {})
            self.in_txn, self.buffer = False, []
            raise
        self.in_txn = False
        ts = self.client._tick()
        for ev in self.buffer:  # one commit: every event shares the commit time
            ev["clusterTime"] = ts
            self.client._publish(ev)
        self.buffer = []
        self.operation_time = ts
        return result


class FakeClient:
    """In memory client: databases, the global event list (the oplog) and a logical clock."""
    def __init__(self, watched=("bank",)):
        """Create an empty client; only databases in ``watched`` emit change events."""
        self.dbs: dict = {}
        self.watched = set(watched)
        self.events: list = []
        self._t = 1_760_000_000
        self._i = itertools.count(1)
        self._tok = itertools.count(1)
        self.admin = FakeDB(self, "admin")
        self.oplog_floor = 0  # events before this index have left the oplog

    def __getitem__(self, name):
        """Return the named database, creating it on first use."""
        return self.dbs.setdefault(name, FakeDB(self, name))

    def get_database(self, name, **kw):
        """Same as indexing; options such as write concern are accepted."""
        return self[name]

    def start_session(self):
        """Start a new FakeSession."""
        return FakeSession(self)

    def _now(self):
        """Current cluster time as a BSON Timestamp."""
        return Timestamp(self._t, 1)

    def _tick(self):
        """Advance the logical clock and return the new cluster time."""
        self._t += 1
        return Timestamp(self._t, next(self._i))

    def _publish(self, ev):
        """Give an event a resume token and append it to the oplog."""
        ev["_id"] = {"_data": f"{next(self._tok):016x}"}
        self.events.append(ev)

    def _event(self, db, coll, op, doc_id, before, after, session):
        """Build a change event shaped as in the MongoDB manual, with images and transaction ids."""
        if db not in self.watched:
            return
        ev = {"operationType": op, "ns": {"db": db, "coll": coll}, "documentKey": {"_id": doc_id},
              "wallTime": datetime.now(timezone.utc).replace(tzinfo=None)}
        if after is not None:
            ev["fullDocument"] = copy.deepcopy(after)
        if before is not None:
            ev["fullDocumentBeforeChange"] = copy.deepcopy(before)
        if session is not None and session.in_txn:
            ev["lsid"] = session.lsid
            ev["txnNumber"] = session.txn_number
            session.buffer.append(ev)
        else:
            ev["clusterTime"] = self._tick()
            self._publish(ev)
