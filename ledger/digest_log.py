# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/digest_log.py
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: publishing block digests outside the database, with an optional Git
#          witness
# Covers:  FR08, FR09; acceptance test AC06 (see docs/D.4.1)
# ============================================================================
"""Publishing block digests outside the database.

The digest log is an append only JSON Lines file, one line per sealed block::

    {"block_hash": "9f2c...", "block_id": 12, "kind": "data",
     "last_ts": [1760000123, 4], "published_at": 1760000125.31}

Why it matters: an administrator can rewrite the ledger collections, but not
a digest that has already left the database. If a rewritten or deleted block
no longer matches its published digest, the verifier raises
``digest_mismatch`` or ``digest_without_block``.

In a real deployment the file would sit on storage the database administrator
cannot rewrite (write once storage, another organization, a transparency
service). For the project we can also push it to a separate Git repository on
GitHub, whose server side history acts as an inexpensive external witness.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from .model import Block


class GitWitness:
    """Best effort: commit and push the digest file after each publication.

    Failures (no network, no credentials) are ignored on purpose: the local
    digest file is the primary record and must never block sealing.
    """

    def __init__(self, repo_dir: str, push: bool = True, every: int = 1):
        """
        Args:
            repo_dir: path of a Git working copy that contains the digest file.
            push: whether to push after committing.
            every: commit only every n digests, to limit commit volume.
        """
        self.repo_dir = repo_dir
        self.push = push
        self.every = max(1, every)
        self._count = 0

    def publish(self, path: Path, block_id: int) -> None:
        """Commit (and push) the digest file after a new digest was appended."""
        self._count += 1
        if self._count % self.every:
            return
        rel = os.path.relpath(path, self.repo_dir)
        cmds = [["git", "add", rel], ["git", "commit", "-q", "-m", f"digest for block {block_id}"]]
        if self.push:
            cmds.append(["git", "push", "-q"])
        for cmd in cmds:
            subprocess.run(cmd, cwd=self.repo_dir, check=False, capture_output=True)


class DigestLog:
    """Append only file of block digests."""

    def __init__(self, path: str, witness: Optional[GitWitness] = None):
        """
        Args:
            path: file to append to; parent folders are created if needed.
            witness: optional GitWitness that receives every publication.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.witness = witness

    def publish(self, block: Block, published_at: Optional[float] = None) -> dict:
        """Append the digest of a sealed block and force it to disk.

        ``fsync`` guarantees the digest is durable before the caller seals the
        next block, which bounds how much history an attacker could rewrite
        without contradicting a published digest.

        Returns:
            The record that was written.
        """
        rec = {
            "block_id": block.block_id,
            "block_hash": block.block_hash,
            "kind": block.kind,
            "last_ts": block.last_ts,
            "published_at": published_at if published_at is not None else time.time(),
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if self.witness:
            self.witness.publish(self.path, block.block_id)
        return rec

    def read(self) -> list[dict]:
        """All published digests in file order (empty list if no file yet)."""
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
