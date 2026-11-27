# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/test_core.py
# Owner:   Rohith Nelluri (database lead)
# Purpose: unit tests of canonical hashing, Merkle trees and blocks
# Covers:  AC01, AC09 (see docs/D.4.1)
# ============================================================================
"""Canonical hashing, RFC 9162 Merkle trees and block construction."""

import hashlib
from datetime import datetime

import pytest
from bson.int64 import Int64

from ledger.blocks import block_root, build_block, prove_entry, txn_groups, verify_entry_proof
from ledger.canonical import canonical_json, doc_hash, key_string
from ledger.merkle import EMPTY_ROOT, inclusion_proof, leaf_hash, merkle_root, node_hash, verify_inclusion
from ledger.model import GENESIS, Entry


def _entry(i, txn="t1", op="update"):
    """Build a small Entry for block and proof tests."""
    return Entry(ns="bank.accounts", key=key_string({"_id": i}), op=op, pre_hash="a" * 64,
                 post_hash=f"{i:064x}", txn=txn, source="A", ts=[100, i], wall=1000.0 + i)


def test_field_order_does_not_change_hash():
    """AC01: the same document with fields in another order hashes the same."""
    a = {"_id": 1, "checking": 5, "nested": {"x": 1, "y": [1, 2]}}
    b = {"nested": {"y": [1, 2], "x": 1}, "checking": 5, "_id": 1}
    assert doc_hash(a) == doc_hash(b)
    assert canonical_json(a) == canonical_json(b)


def test_datetime_precision_matches_server_storage():
    # BSON stores milliseconds: microseconds beyond that must not change the hash
    """AC01: datetimes hash at millisecond precision, as MongoDB stores them."""
    precise = {"_id": 1, "at": datetime(2026, 10, 7, 12, 0, 0, 123456)}
    stored = {"_id": 1, "at": datetime(2026, 10, 7, 12, 0, 0, 123000)}
    assert doc_hash(precise) == doc_hash(stored)


def test_numeric_types_are_distinguished():
    """AC01: int, long, double and decimal values hash differently."""
    assert doc_hash({"v": 1}) != doc_hash({"v": 1.0})
    assert doc_hash({"v": 1}) != doc_hash({"v": Int64(1)})


def test_any_value_change_changes_hash():
    """AC01: changing any value anywhere in the document changes its hash."""
    base = {"_id": 7, "savings": 100, "checking": 200}
    assert doc_hash(base) != doc_hash({**base, "checking": 201})
    assert doc_hash(base) != doc_hash({**base, "extra": None})
    assert doc_hash(None) is None


def test_key_string_is_canonical_extended_json():
    """Document keys are rendered as canonical Extended JSON strings."""
    assert key_string({"_id": 7}) == '{"_id":{"$numberInt":"7"}}'
    assert key_string({"_id": "abc"}) == '{"_id":"abc"}'


def test_merkle_small_trees_match_rfc_definition():
    """AC09: roots of small trees equal the RFC 9162 definition computed by hand."""
    a, b, c = b"a", b"b", b"c"
    assert merkle_root([]) == EMPTY_ROOT == hashlib.sha256(b"").digest()
    assert merkle_root([a]) == hashlib.sha256(b"\x00a").digest()
    assert merkle_root([a, b]) == node_hash(leaf_hash(a), leaf_hash(b))
    # three leaves: the left subtree holds two (the largest power of two below 3)
    assert merkle_root([a, b, c]) == node_hash(node_hash(leaf_hash(a), leaf_hash(b)), leaf_hash(c))


def test_leaf_and_node_domains_differ():
    """AC09: leaf and interior node hashes use different prefixes."""
    left, right = leaf_hash(b"x"), leaf_hash(b"y")
    # an interior node can never be presented as a leaf of the same bytes
    assert node_hash(left, right) != leaf_hash(left + right)


@pytest.mark.parametrize("n", list(range(1, 34)))
def test_every_inclusion_proof_verifies(n):
    """AC09: every leaf of trees with 1 to 33 leaves has a proof that verifies."""
    leaves = [f"leaf {i}".encode() for i in range(n)]
    root = merkle_root(leaves)
    for i in range(n):
        proof = inclusion_proof(i, leaves)
        assert verify_inclusion(i, n, leaves[i], proof, root)
        assert not verify_inclusion(i, n, leaves[i] + b"!", proof, root)
        if n > 1:
            assert not verify_inclusion((i + 1) % n, n, leaves[i], proof, root) or leaves[i] == leaves[(i + 1) % n]


def test_inclusion_rejects_out_of_range():
    """AC09: asking for a proof outside the tree raises an error."""
    leaves = [b"a", b"b", b"c"]
    with pytest.raises(IndexError):
        inclusion_proof(3, leaves)
    assert not verify_inclusion(3, 3, b"a", [], merkle_root(leaves))
    assert not verify_inclusion(-1, 3, b"a", [], merkle_root(leaves))


def test_txn_groups_keep_first_appearance_order():
    """Transactions inside a block keep the order in which they first appear."""
    ents = [_entry(1, "t2"), _entry(2, "t1"), _entry(3, "t2"), _entry(4, "t3")]
    groups = txn_groups(ents)
    assert [t for t, _ in groups] == ["t2", "t1", "t3"]
    assert [e.key for e in groups[0][1]] == [ents[0].key, ents[2].key]


def test_block_seal_and_chain():
    """AC05: sealed blocks link to their predecessor and their hashes recompute."""
    b0 = build_block(0, GENESIS, [_entry(1), _entry(2, "t2")], created_at=1010.0)
    assert b0.block_hash == b0.compute_hash() and b0.txn_count == 2 and b0.entry_count == 2
    assert b0.first_ts == [100, 1] and b0.last_ts == [100, 2]
    assert b0.lag_max == pytest.approx(9.0) and b0.lag_mean == pytest.approx(8.5)
    b1 = build_block(1, b0.block_hash, [_entry(3)], created_at=1020.0)
    assert b1.prev_hash == b0.block_hash
    b0.entry_count += 1  # any header change changes the hash
    assert b0.compute_hash() != b0.block_hash


def test_entry_proofs_against_block_root():
    """AC09: each entry proves against the root of its block."""
    ents = [_entry(i, f"t{i % 3}") for i in range(7)]
    blk = build_block(0, GENESIS, ents, created_at=2000.0)
    for i in range(len(ents)):
        proof = prove_entry(ents, i)
        assert verify_entry_proof(ents[i], proof, blk)
        altered = Entry(**{**ents[i].to_doc(), "post_hash": "f" * 64})
        assert not verify_entry_proof(altered, proof, blk)


def test_any_entry_change_changes_block_root():
    """AC05: changing any entry changes the block root."""
    ents = [_entry(i) for i in range(4)]
    root = block_root(ents)
    for field, value in (("post_hash", "0" * 64), ("op", "delete"), ("txn", "other"), ("wall", 5.0)):
        changed = [Entry(**{**e.to_doc()}) for e in ents]
        setattr(changed[2], field, value)
        assert block_root(changed) != root
    # chain bookkeeping is outside the leaf (checked by the Design S chain instead)
    changed = [Entry(**{**e.to_doc()}) for e in ents]
    changed[0].chain = "1" * 64
    assert block_root(changed) == root
