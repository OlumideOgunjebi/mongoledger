# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    sqlserver/ledger_baseline.py
# Owner:   Rohith Nelluri (database lead)
# Purpose: SQL Server 2022 Ledger baseline: digests and verification
# Covers:  FR21; acceptance test AC11 (see docs/D.4.1)
# ============================================================================
"""SQL Server Ledger baseline: schema, digests and verification through pyodbc.

  python -m sqlserver.ledger_baseline schema
  python -m sqlserver.ledger_baseline digest  [--every 5 --seconds 60]
  python -m sqlserver.ledger_baseline verify

On premises SQL Server uploads digests automatically only to Azure storage, so
the baseline generates them itself with sp_generate_database_ledger_digest at
the same interval as the MongoDB ledger, appends them to digests/sqlserver.jsonl,
and checks them with sp_verify_database_ledger.

Connection: SQLSERVER_CONN, or built from MSSQL_SA_PASSWORD with the defaults below.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIGESTS = Path("digests/sqlserver.jsonl")


def conn_str(database: str = "LedgerBench") -> str:
    """ODBC connection string: SQLSERVER_CONN if set, else sa on localhost with MSSQL_SA_PASSWORD.

    The password is read from the environment (normally the .env file used by
    docker compose) and is never written to disk by this module.
    """
    if os.environ.get("SQLSERVER_CONN"):
        return os.environ["SQLSERVER_CONN"]
    pwd = os.environ.get("MSSQL_SA_PASSWORD", "")
    driver = os.environ.get("ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    return (f"DRIVER={{{driver}}};SERVER=localhost,1433;DATABASE={database};UID=sa;PWD={pwd};"
            "TrustServerCertificate=yes;Encrypt=no")


def connect(database: str = "LedgerBench", autocommit: bool = True):
    """Open a pyodbc connection (imported lazily so MongoDB users need no ODBC driver)."""
    import pyodbc
    return pyodbc.connect(conn_str(database), autocommit=autocommit)


def run_script(path: Path) -> None:
    """Run a T SQL script, splitting it into batches on lines that contain only GO."""
    conn = connect("master")
    for batch in re.split(r"^\s*GO\s*$", path.read_text(), flags=re.MULTILINE | re.IGNORECASE):
        if batch.strip():
            conn.cursor().execute(batch)
    conn.close()


def generate_digest(conn) -> dict:
    """Ask SQL Server for the current database digest and append it to digests/sqlserver.jsonl.

    On premises SQL Server uploads digests automatically only to Azure
    storage, so the baseline stores them itself, at the same interval as the
    MongoDB ledgers, to keep the comparison fair.

    Returns:
        The digest as a dict (empty if the database has no ledger blocks yet).
    """
    row = conn.cursor().execute("EXECUTE sp_generate_database_ledger_digest").fetchone()
    if row is None or row[0] is None:
        return {}
    digest = json.loads(row[0])
    DIGESTS.parent.mkdir(parents=True, exist_ok=True)
    with open(DIGESTS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({**digest, "published_at": time.time()}) + "\n")
    return digest


def read_digests() -> list[dict]:
    """All stored digests, without the local published_at field SQL Server does not expect."""
    if not DIGESTS.exists():
        return []
    out = []
    for line in DIGESTS.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            d.pop("published_at", None)
            out.append(d)
    return out


def verify(conn) -> tuple[bool, str, float]:
    """Run sys.sp_verify_database_ledger against every stored digest.

    The procedure returns 0 when the ledger matches all digests. The return
    code is captured explicitly, because a failed verification may be
    reported as a return value rather than as an error.

    Returns:
        (ok, message, seconds taken)
    """
    digests = read_digests()
    if not digests:
        return False, "no digests recorded", 0.0
    t0 = time.perf_counter()
    sql = ("SET NOCOUNT ON; DECLARE @rc INT; "
           "EXEC @rc = sys.sp_verify_database_ledger @digests = ?; SELECT @rc AS rc;")
    try:
        cur = conn.cursor()
        cur.execute(sql, json.dumps(digests))
        rc, last_block = None, None
        while True:
            if cur.description:
                rows = cur.fetchall()
                name = cur.description[0][0]
                if rows and name == "rc":
                    rc = rows[0][0]
                elif rows and name == "last_verified_block_id":
                    last_block = rows[0][0]
            if not cur.nextset():
                break
        ok = rc == 0
        msg = f"return code {rc}, last verified block {last_block}"
        return ok, msg, time.perf_counter() - t0
    except Exception as exc:  # verification failures are also reported as errors
        return False, str(exc), time.perf_counter() - t0


def main(argv=None) -> int:
    """Command line entry: schema, digest (optionally repeated) or verify."""
    p = argparse.ArgumentParser(prog="sqlserver.ledger_baseline")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema")
    d = sub.add_parser("digest")
    d.add_argument("--every", type=float, default=0)
    d.add_argument("--seconds", type=float, default=0)
    sub.add_parser("verify")
    args = p.parse_args(argv)
    if args.cmd == "schema":
        run_script(HERE / "01_schema.sql")
        print("schema ready in LedgerBench")
        return 0
    conn = connect()
    if args.cmd == "digest":
        end = time.time() + args.seconds
        while True:
            print(json.dumps(generate_digest(conn)))
            if not args.every or time.time() >= end:
                return 0
            time.sleep(args.every)
    ok, msg, secs = verify(conn)
    print(json.dumps({"ok": ok, "message": msg, "verify_seconds": round(secs, 4)}))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
