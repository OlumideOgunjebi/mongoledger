# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/scan.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: full scan of the protected collections and the Design A baseline
# Covers:  FR06, FR14; acceptance test AC04 (see docs/D.4.1)
# ============================================================================
"""Reading the current state of protected collections for the verifier.

The verifier compares what the ledger says each document should be with what
the database actually holds. This module produces the second half: a full
scan that hashes every protected document with the same canonical hash the
ledger uses (canonical.doc_hash).
"""

from __future__ import annotations

from .canonical import doc_hash, key_string


def scan_state(client, data_db: str, collections) -> dict[tuple, str]:
    """Hash every document of the protected collections.

    Args:
        client: connected MongoClient.
        data_db: name of the protected database.
        collections: names of the protected collections.

    Returns:
        ``{(namespace, key_string): doc_hash}`` for every document found.
    """
    state: dict[tuple, str] = {}
    db = client[data_db]
    for name in collections:
        ns = f"{data_db}.{name}"
        for doc in db[name].find({}):
            state[(ns, key_string({"_id": doc["_id"]}))] = doc_hash(doc)
    return state


def baseline_entries_for_a(client, data_db: str, collections):
    """Baseline entries for Design A, recorded once at setup before the stream starts.

    The change stream only reports changes made after it starts, so the
    current content of every protected document is recorded first as a
    ``baseline`` entry (source "B"). Replaying the ledger then starts from a
    known state instead of from an empty collection.

    Returns:
        A list of Entry objects, one per document.
    """
    from .model import Entry

    out = []
    for name in collections:
        ns = f"{data_db}.{name}"
        for doc in client[data_db][name].find({}):
            out.append(Entry(ns=ns, key=key_string({"_id": doc["_id"]}), op="baseline", pre_hash=None,
                             post_hash=doc_hash(doc), txn="baseline", source="B"))
    return out
