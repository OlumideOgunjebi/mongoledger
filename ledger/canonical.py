# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/canonical.py
# Owner:   Rohith Nelluri (database lead)
# Purpose: deterministic SHA256 hashing of MongoDB documents (canonical
#          Extended JSON)
# Covers:  FR02, NFR01; acceptance test AC01 (see docs/D.4.1)
# ============================================================================
"""Deterministic hashing of MongoDB documents.

Why this module exists
----------------------
The verifier compares two hashes of the same document that were computed at
different times and from different sources: one from a change event (or from
the document the application just wrote) and one from a later scan of the
collection. If two documents that MongoDB considers equal could serialize to
different bytes, the verifier would raise false alarms. Three things can make
logically equal documents serialize differently:

1. Field order. MongoDB preserves insertion order, and an update can append a
   field at the end. We sort field names recursively.
2. Precision lost on storage. A Python datetime carries microseconds but a BSON
   date stores milliseconds. We round trip every document through BSON before
   hashing, so a hash computed in the application equals the hash of the
   document the server returns.
3. Numeric types. An int32, an int64 and a double with the same value are
   different BSON values. Extended JSON canonical mode keeps the type tag
   ("$numberInt", "$numberLong", "$numberDouble"), so the hash reflects exactly
   what is stored rather than silently treating them as equal.

Domain separation
-----------------
Every hash in the project starts with a short ASCII prefix that names what is
being hashed ("doc:" here, "entry:" in model.py, "block:" for block headers).
A value of one kind can therefore never be presented as a value of another
kind with the same digest.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

import bson
from bson import json_util
from bson.json_util import CANONICAL_JSON_OPTIONS


def bson_roundtrip(doc: Mapping[str, Any]) -> dict:
    """Return the document exactly as MongoDB would store and return it.

    Encoding to BSON and decoding again applies the same conversions the
    server applies on insert: datetimes are truncated to milliseconds, tuples
    become lists and Python ints become int32 or int64 by size.

    Args:
        doc: any mapping that pymongo could insert.

    Returns:
        A new plain ``dict`` holding the stored representation.
    """
    return bson.decode(bson.encode(doc))


def canonical_json(value: Any) -> str:
    """Canonical Extended JSON with recursively sorted keys and no whitespace.

    Accepts a document or any single BSON value (for example an ObjectId used
    as a document key). A value that is not a document is wrapped as
    ``{"v": value}`` so that it can be BSON encoded, then unwrapped again.

    Args:
        value: a document or a BSON value.

    Returns:
        The canonical text, identical for every logically equal input.
    """
    if isinstance(value, Mapping):
        doc = bson_roundtrip(value)
    else:
        doc = bson_roundtrip({"v": value})["v"]
    # sort_keys orders fields at every nesting level; compact separators remove
    # whitespace so formatting can never change the bytes that are hashed.
    return json_util.dumps(doc, json_options=CANONICAL_JSON_OPTIONS, sort_keys=True, separators=(",", ":"))


def sha256_hex(data: bytes) -> str:
    """SHA256 of ``data`` as a 64 character lowercase hexadecimal string."""
    return hashlib.sha256(data).hexdigest()


def doc_hash(doc: Mapping[str, Any] | None) -> str | None:
    """Hash of the canonical form of a document.

    Args:
        doc: the full document, or None when there is no document (the state
            before an insert or after a delete).

    Returns:
        ``sha256("doc:" + canonical_json(doc))`` in hexadecimal, or None.
    """
    if doc is None:
        return None
    return sha256_hex(b"doc:" + canonical_json(doc).encode("utf-8"))


def key_string(document_key: Mapping[str, Any]) -> str:
    """Stable string form of a change event ``documentKey``.

    The ledger indexes entries by this string, for example
    ``{"_id":{"$numberInt":"7"}}``, so the same document always maps to the
    same key whether the entry came from Design S, Design A or a scan.
    """
    return canonical_json(document_key)


def plain_json(obj: Any) -> bytes:
    """Deterministic UTF8 JSON for ledger metadata made of plain types only.

    Entries and block headers contain only strings, numbers, lists, dicts and
    None, so the standard library encoder with sorted keys is sufficient and
    much faster than a BSON round trip.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
