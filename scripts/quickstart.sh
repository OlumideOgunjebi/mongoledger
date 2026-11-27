#!/usr/bin/env bash
# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    scripts/quickstart.sh
# Owner:   Yadu Pratap (deployment lead)
# Purpose: one command start on Linux and macOS: containers, setup, demo
# Covers:  NFR09, NFR10 (see docs/D.4.1)
# ============================================================================

# One command start on macOS, Linux, WSL or Git Bash:
#   bash scripts/quickstart.sh
#
# 1. checks that Docker and Python 3.10+ are available
# 2. creates .env from .env.example if it does not exist (edit the password before using SQL Server)
# 3. starts MongoDB 8.0 as a single node replica set and waits until it is PRIMARY
# 4. creates a virtual environment in .venv and installs the project with its extras
# 5. runs the unit tests, the live integration tests and the two minute demonstration
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || { echo "Docker is not installed or not on PATH"; exit 1; }
PY="$(command -v python3 || command -v python)"
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'

[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example"; }

echo "starting MongoDB (first run downloads the image)"
docker compose up -d --wait mongo

if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
# shellcheck disable=SC1091
if [ -f .venv/bin/activate ]; then . .venv/bin/activate; else . .venv/Scripts/activate; fi
python -m pip install -q --upgrade pip
python -m pip install -q -e ".[bench,test]"

export MONGO_URI="${MONGO_URI:-mongodb://localhost:27017/?replicaSet=rs0&directConnection=true}"
python -m pytest -q tests
python demo.py
echo "done. Activate the environment with: source .venv/bin/activate"
