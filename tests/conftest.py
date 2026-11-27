# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    tests/conftest.py
# Owner:   All members (shared test support)
# Purpose: pytest configuration shared by every test module
# Covers:  NFR11 (see docs/D.4.1)
# ============================================================================
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
