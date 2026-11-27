# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    ledger/__init__.py
# Owner:   All members
# Purpose: package marker and version number of the ledger library
# Covers:  NFR11 (see docs/D.4.1)
# ============================================================================
"""Change stream ledger for MongoDB.

Tamper evidence for an unmodified MongoDB replica set, built from two public
interfaces: multi document transactions (Design S) and change streams
(Design A). The pure modules (canonical, merkle, model, blocks, verifier) have
no database dependency; the MongoDB adapters live in design_s, design_a,
mongo_store and scan.
"""

__version__ = "1.0.0"
