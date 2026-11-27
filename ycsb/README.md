# YCSB runs

YCSB 0.17.0 has bindings for MongoDB and for JDBC databases (used here for SQL Server).
Download `ycsb-0.17.0.tar.gz` from the YCSB GitHub releases page and unpack it next to
this repository, then download the Microsoft JDBC driver jar (`mssql-jdbc-*.jre11.jar`)
into `ycsb-0.17.0/jdbc-binding/lib/`.

YCSB writes directly to the database, so it measures the unprotected baseline and
Design A (the change stream service observes every write). Design S needs writes to go
through the Python library and is measured with SmallBank instead.

| Target | What runs | Command |
|---|---|---|
| MongoDB plain | YCSB only | `bash ycsb/run_ycsb.sh mongo a` |
| MongoDB Design A | YCSB plus `python -m ledger.cli --data-db ycsb --collections usertable service` | `bash ycsb/run_ycsb.sh mongo-a a` |
| SQL Server plain | table `usertable_plain` | `bash ycsb/run_ycsb.sh sql a plain` |
| SQL Server Ledger | table `usertable_ledger` | `bash ycsb/run_ycsb.sh sql a ledger` |

Workload A is update heavy (50 percent reads, 50 percent updates); workload B is read
mostly (95 percent reads). Results are written to `results/ycsb/`.
