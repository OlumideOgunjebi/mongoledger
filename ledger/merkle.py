# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/merkle.py
# Owner:   Rohith Nelluri (database lead)
# Purpose: RFC 9162 Merkle tree hashing and inclusion proofs
# Covers:  FR07, FR18; acceptance test AC09 (see docs/D.4.1)
# ============================================================================
"""Merkle tree hashing and inclusion proofs following RFC 9162, section 2.1.

A Merkle tree reduces any number of items to one 32 byte root. Changing any
item changes the root, and a short list of sibling hashes (an inclusion proof)
shows that one item belongs to a tree without revealing the other items.

Leaves and interior nodes use different one byte prefixes (0x00 and 0x01), so
an interior node can never be passed off as a leaf (second preimage
resistance). The tree shape is the one defined by the RFC: for n > 1 items the
left subtree holds the largest power of two strictly smaller than n. Following
the RFC exactly means any independent RFC 9162 implementation can check our
proofs.

Example for five leaves d0..d4 (k = 4 for the first split)::

                 root
               /      \\
           h(0..3)    h(4)
           /     \\
       h(0,1)   h(2,3)
"""

from __future__ import annotations

import hashlib
from typing import Sequence

# Root of an empty tree as defined by the RFC: the hash of the empty string.
EMPTY_ROOT = hashlib.sha256(b"").digest()


def leaf_hash(data: bytes) -> bytes:
    """MTH({d}) = SHA256(0x00 || d): the hash of one leaf."""
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """SHA256(0x01 || left || right): the hash of an interior node."""
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    """Largest power of two strictly smaller than n (n >= 2).

    This is the size of the left subtree in the RFC 9162 tree shape.
    """
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def _root_of_hashes(hashes: Sequence[bytes]) -> bytes:
    """Root over leaf hashes that are already computed (recursive definition)."""
    n = len(hashes)
    if n == 1:
        return hashes[0]
    k = _split(n)
    return node_hash(_root_of_hashes(hashes[:k]), _root_of_hashes(hashes[k:]))


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    """MTH(D[n]) for a list of leaf data items.

    Args:
        leaves: raw leaf data, not yet hashed (each item is hashed with the
            0x00 prefix here).

    Returns:
        The 32 byte root, or EMPTY_ROOT for an empty list.
    """
    if not leaves:
        return EMPTY_ROOT
    return _root_of_hashes([leaf_hash(d) for d in leaves])


def inclusion_proof(index: int, leaves: Sequence[bytes]) -> list[bytes]:
    """PATH(m, D[n]): the sibling hashes from one leaf up to the root.

    Args:
        index: position m of the leaf being proven (0 based).
        leaves: the complete list of leaf data items of the tree.

    Returns:
        The audit path, ordered from the leaf's sibling to the root's child.

    Raises:
        IndexError: if ``index`` is outside the tree.
    """
    n = len(leaves)
    if not 0 <= index < n:
        raise IndexError("leaf index out of range")
    hashes = [leaf_hash(d) for d in leaves]

    def path(m: int, hs: Sequence[bytes]) -> list[bytes]:
        # RFC 9162 section 2.1.3.1: descend into the subtree that holds the
        # leaf and append the root of the other subtree on the way back up.
        """Audit path of leaf ``m`` in the subtree whose leaf hashes are ``hs`` (RFC 9162, 2.1.3.1)."""
        if len(hs) == 1:
            return []
        k = _split(len(hs))
        if m < k:
            return path(m, hs[:k]) + [_root_of_hashes(hs[k:])]
        return path(m - k, hs[k:]) + [_root_of_hashes(hs[:k])]

    return path(index, hashes)


def verify_inclusion(index: int, tree_size: int, leaf_data: bytes, proof: Sequence[bytes], root: bytes) -> bool:
    """Check an inclusion proof with the algorithm of RFC 9162, section 2.1.3.2.

    The verifier needs only the leaf, its position, the tree size, the proof
    and a trusted root; it never sees the other leaves.

    Args:
        index: position of the leaf in the tree.
        tree_size: number of leaves in the tree.
        leaf_data: the raw leaf being proven.
        proof: the audit path returned by ``inclusion_proof``.
        root: the root the leaf is claimed to belong to.

    Returns:
        True only if the proof recomputes exactly ``root``.
    """
    if index >= tree_size or index < 0:
        return False
    # fn walks the leaf's position upward, sn the last position of the tree.
    fn, sn = index, tree_size - 1
    r = leaf_hash(leaf_data)
    for p in proof:
        if sn == 0:
            return False            # proof longer than the tree is tall
        if fn & 1 or fn == sn:
            # The leaf side is a right child (or the last, unpaired node):
            # the sibling goes on the left.
            r = node_hash(p, r)
            if not fn & 1:
                # Skip levels where the node had no right sibling.
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)     # sibling goes on the right
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == root
