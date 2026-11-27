# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    scripts/health_check.ps1
# Owner:   Yadu Pratap (deployment lead)
# Purpose: post deployment health check on Windows
# Covers:  NFR07, NFR10 (see docs/D.4.1)
# ============================================================================

# Post deployment health check for Windows PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1 [-Design A|S]
# Same checks as health_check.sh. Environment: MONGO_URI, DATA_DB, COLLECTIONS.
param([string]$Design = "A")
Set-Location (Join-Path $PSScriptRoot "..")
$env:DB = if ($env:DATA_DB) { $env:DATA_DB } else { "bank" }
$colls = if ($env:COLLECTIONS) { $env:COLLECTIONS } else { "accounts" }
$env:FIRST = $colls.Split(",")[0]
if (-not $env:MONGO_URI) { $env:MONGO_URI = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true" }
$fail = 0
function Check($name, [scriptblock]$test) {
    & $test *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "PASS  $name" } else { Write-Host "FAIL  $name"; $script:fail = 1 }
}
Check "server is a writable replica set PRIMARY" { python -c "import os,pymongo; h=pymongo.MongoClient(os.environ['MONGO_URI'], serverSelectionTimeoutMS=3000).admin.command('hello'); assert h.get('isWritablePrimary') and h.get('setName')" }
Check "before and after images enabled on $($env:DB).$($env:FIRST)" { python -c "import os,pymongo; c=pymongo.MongoClient(os.environ['MONGO_URI'], serverSelectionTimeoutMS=3000); i=next(c[os.environ['DB']].list_collections(filter={'name': os.environ['FIRST']})); assert i['options']['changeStreamPreAndPostImages']['enabled']" }
Check "ledger $Design has blocks" { python -m ledger.cli --data-db $env:DB --collections $colls blocks --design $Design }
Check "every published digest matches" { python -m ledger.cli --data-db $env:DB --collections $colls digests --design $Design }
Check "verify reports no alert" { python -m ledger.cli --data-db $env:DB --collections $colls verify --design $Design }
exit $fail
