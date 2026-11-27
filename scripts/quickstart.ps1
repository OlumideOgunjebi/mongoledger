# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    scripts/quickstart.ps1
# Owner:   Yadu Pratap (deployment lead)
# Purpose: one command start on Windows: containers, setup, demo
# Covers:  NFR09, NFR10 (see docs/D.4.1)
# ============================================================================

# One command start on Windows PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\quickstart.ps1
#
# Same steps as quickstart.sh: .env, MongoDB replica set, virtual environment,
# tests and the two minute demonstration.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker Desktop is not installed or not running" }
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "created .env from .env.example" }

Write-Host "starting MongoDB (first run downloads the image)"
docker compose up -d --wait mongo
if ($LASTEXITCODE -ne 0) { throw "docker compose failed" }

if (-not (Test-Path .venv)) { python -m venv .venv }
. .\.venv\Scripts\Activate.ps1
python -m pip install -q --upgrade pip
python -m pip install -q -e ".[bench,test]"

if (-not $env:MONGO_URI) { $env:MONGO_URI = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true" }
python -m pytest -q tests
python demo.py
Write-Host "done. Activate the environment with: .\.venv\Scripts\Activate.ps1"
