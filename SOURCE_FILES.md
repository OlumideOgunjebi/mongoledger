# Source and file list

Every file in the repository, grouped by folder, with the member who owns it and what it is for.
Line counts are given for text files and sizes for binary files. Owners follow Table 2 of the
D.3.1 report; each source file also names its owner and the requirements it covers in its header.
Requirement numbers (FR, NFR, AC) refer to `docs/D.4.1 User Requirements and Analysis.pdf`.

## (root)

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `.env.example` | Pratap | template for the local .env file (placeholder password only) | 511 bytes |
| `.gitignore` | Pratap | keeps .env, caches, raw results and digest logs out of Git | 84 bytes |
| `README.md` | All | project portal: links, quick start, commands, layout and ownership | 251 |
| `SOURCE_FILES.md` | All | this list of every file in the repository | 186 |
| `demo.py` | Ogunjebi | two minute live demonstration: clean run, verification and three attacks | 144 |
| `docker-compose.yml` | Pratap | MongoDB 8.0 replica set rs0, SQL Server 2022 and the application container | 60 |
| `mongoledger.code-workspace` | All | VS Code workspace file (open the project as a VS Code project) | 270 bytes |
| `pyproject.toml` | Pratap | packaging: version 1.0.0, dependencies, the mongoledger command | 40 |
| `requirements.txt` | Pratap | Python dependencies for a plain pip install | 5 |

## ledger

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `ledger/__init__.py` | All | package marker and version number of the ledger library | 19 |
| `ledger/blocks.py` | Nelluri | grouping entries by transaction, sealing and chaining blocks | 142 |
| `ledger/canonical.py` | Nelluri | deterministic SHA256 hashing of MongoDB documents (canonical Extended JSON) | 127 |
| `ledger/cli.py` | Ogunjebi | the mongoledger command line: setup, service, fold, verify, history, blocks, digests, prove | 314 |
| `ledger/design_a.py` | Pratap | Design A: change stream service with resume tokens, transaction grouping and gap blocks | 317 |
| `ledger/design_s.py` | Nelluri | Design S: ledger entry written inside the application transaction, k chain heads, fold into blocks | 297 |
| `ledger/digest_log.py` | Ogunjebi | publishing block digests outside the database, with an optional Git witness | 116 |
| `ledger/merkle.py` | Nelluri | RFC 9162 Merkle tree hashing and inclusion proofs | 155 |
| `ledger/model.py` | Nelluri | the two ledger record types shared by both designs: Entry and Block | 192 |
| `ledger/mongo_store.py` | Nelluri | storing blocks and entries in the ledger database (ledger_a, ledger_s) | 129 |
| `ledger/scan.py` | Ogunjebi | full scan of the protected collections and the Design A baseline | 62 |
| `ledger/search.py` | Ogunjebi | auditor searches: history, blocks, digests and inclusion proofs | 201 |
| `ledger/verifier.py` | Ogunjebi | verification of chain, digests, per head chains and replayed state against a full scan | 330 |

## workloads

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `workloads/__init__.py` | Pratap | package marker for the workloads | 10 |
| `workloads/analytics.py` | Pratap | trading workload over the MongoDB sample_analytics dataset | 261 |
| `workloads/smallbank.py` | Pratap | SmallBank for MongoDB (plain and Design S) and SQL Server, and the closed loop driver | 343 |

## bench

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `bench/__init__.py` | Pratap | package marker for the benchmark harness | 10 |
| `bench/plot.py` | Pratap | charts of throughput, p95 latency and commit to block lag from the summary file | 124 |
| `bench/run.py` | Pratap | experiment harness: one summary row per run in results/summary.csv | 205 |

## attacks

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `attacks/__init__.py` | Ogunjebi | package marker for the insider attack suite | 10 |
| `attacks/restore_backup.sh` | Ogunjebi | attack 7: restore an older file backup of the data and both ledgers | 49 |
| `attacks/run_attacks.py` | Ogunjebi | the seven insider actions of D.4.1 Table 2 and their verification outcomes | 163 |
| `attacks/standalone_write.sh` | Ogunjebi | attack 2: restart mongod outside replica set mode and change a document without an oplog entry | 43 |

## sqlserver

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `sqlserver/01_schema.sql` | Nelluri | SQL Server schema: plain table and updatable ledger table for SmallBank | 57 |
| `sqlserver/__init__.py` | Nelluri | package marker for the SQL Server 2022 baseline | 10 |
| `sqlserver/ledger_baseline.py` | Nelluri | SQL Server 2022 Ledger baseline: digests and verification | 165 |

## ycsb

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `ycsb/README.md` | Pratap | how to run YCSB 0.17.0 workloads A and B | 20 |
| `ycsb/run_ycsb.sh` | Pratap | YCSB workloads A and B on MongoDB and SQL Server | 55 |

## tests

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `tests/conftest.py` | All | pytest configuration shared by every test module | 15 |
| `tests/fake_mongo.py` | All | in memory stand in for the parts of pymongo the ledger uses | 377 |
| `tests/integration/test_live_mongo.py` | All | integration tests against a live MongoDB replica set | 142 |
| `tests/test_analytics.py` | Pratap | tests of the sample_analytics dataset and trading workload | 104 |
| `tests/test_core.py` | Nelluri | unit tests of canonical hashing, Merkle trees and blocks | 147 |
| `tests/test_design_a.py` | Pratap | unit tests of the Design A event processor | 124 |
| `tests/test_end_to_end.py` | Ogunjebi | end to end tests: both designs, the verifier and every tampering case | 198 |
| `tests/test_search.py` | Ogunjebi | tests of the history, blocks, digests and prove commands | 141 |
| `tests/test_service_and_cli.py` | Ogunjebi | tests of setup, the Design A service, restart, gap blocks and the cross check | 95 |
| `tests/test_smallbank.py` | Pratap | tests of the SmallBank workload in plain and Design S modes | 81 |

## datasets

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `datasets/README.md` | Pratap | dataset sources, download location, sizes and SHA256 checksums | 38 |
| `datasets/sample_analytics/accounts.json.gz` | Pratap | dataset: MongoDB sample_analytics accounts (1,746 documents) | 17,875 bytes |
| `datasets/sample_analytics/customers.json.gz` | Pratap | dataset: MongoDB sample_analytics customers (500 documents) | 55,006 bytes |
| `datasets/sample_analytics/transactions.json.gz` | Pratap | dataset: MongoDB sample_analytics transactions (1,746 buckets) | 4,701,960 bytes |

## schema

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `schema/README.md` | Nelluri | database schemas, relationships, indexes and how to connect | 55 |
| `schema/mongodb_schema.json` | Nelluri | $jsonSchema of every MongoDB collection (data and both ledgers) | 132 |

## scripts

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `scripts/health_check.ps1` | Pratap | post deployment health check on Windows | 30 |
| `scripts/health_check.sh` | Pratap | post deployment health check on Linux and macOS | 45 |
| `scripts/quickstart.ps1` | Pratap | one command start on Windows: containers, setup, demo | 34 |
| `scripts/quickstart.sh` | Pratap | one command start on Linux and macOS: containers, setup, demo | 41 |

## .devcontainer

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `.devcontainer/devcontainer.json` | Pratap | GitHub Codespaces definition: run the project in the browser without installation | 39 |
| `.devcontainer/docker-compose.yml` | Pratap | services for GitHub Codespaces and VS Code dev containers | 43 |

## .github

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `.github/workflows/tests.yml` | Pratap | continuous integration: unit tests and live replica set tests on every push | 96 |

## .vscode

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `.vscode/extensions.json` | All | recommended VS Code extensions | 4 |
| `.vscode/launch.json` | All | run and debug configurations: demo, setup, service, verify, tests | 60 |
| `.vscode/settings.json` | All | VS Code project settings: pytest discovery, interpreter, MONGO_URI | 21 |
| `.vscode/tasks.json` | All | tasks: quick start, health check, demo, tests, attacks, build | 19 |

## dist

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `dist/mongoledger-1.0.0-py3-none-any.whl` | Pratap | executable: installable wheel (pip install), adds the mongoledger command | 70,771 bytes |
| `dist/mongoledger-1.0.0.tar.gz` | Pratap | source distribution archive of version 1.0.0 | 73,041 bytes |

## docs

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `docs/D.3.1 Midterm Project Status Report.docx` | All | project document D.3.1 Midterm Project Status Report | 106,197 bytes |
| `docs/D.3.1 Midterm Project Status Report.pdf` | All | project document D.3.1 Midterm Project Status Report | 248,253 bytes |
| `docs/D.3.1.1 Team Contribution Form, Team 3.docx` | All | project document D.3.1.1 Team Contribution Form, Team 3 | 28,942 bytes |
| `docs/D.3.1.1 Team Contribution Form, Team 3.pdf` | All | project document D.3.1.1 Team Contribution Form, Team 3 | 69,925 bytes |
| `docs/D.4.1 User Requirements and Analysis.docx` | All | project document D.4.1 User Requirements and Analysis | 28,531 bytes |
| `docs/D.4.1 User Requirements and Analysis.pdf` | All | project document D.4.1 User Requirements and Analysis | 231,332 bytes |
| `docs/D.4.2 Design Document.docx` | All | project document D.4.2 Design Document | 1,150,905 bytes |
| `docs/D.4.2 Design Document.pdf` | All | project document D.4.2 Design Document | 927,418 bytes |
| `docs/D.4.3 Project Full Application, Team 3.docx` | All | project document D.4.3 Project Full Application, Team 3 | 21,729 bytes |
| `docs/D.4.3 Project Full Application, Team 3.pdf` | All | project document D.4.3 Project Full Application, Team 3 | 181,784 bytes |
| `docs/D.4.4 Deployment Document.docx` | All | project document D.4.4 Deployment Document | 20,473 bytes |
| `docs/D.4.4 Deployment Document.pdf` | All | project document D.4.4 Deployment Document | 151,294 bytes |
| `docs/D.4.5 User Guide.docx` | All | project document D.4.5 User Guide | 19,818 bytes |
| `docs/D.4.5 User Guide.pdf` | All | project document D.4.5 User Guide | 137,814 bytes |
| `docs/PROJECT_MANAGEMENT.md` | Ogunjebi | project management: Hive link, task breakdown, schedule, risks, workflow | 97 |
| `docs/architecture_wide.png` | All | figure used in the D.4 documents | 160,603 bytes |
| `docs/block_structure.png` | All | figure used in the D.4 documents | 302,399 bytes |
| `docs/data_flow.png` | All | figure used in the D.4 documents | 170,992 bytes |
| `docs/data_model.png` | All | figure used in the D.4 documents | 304,850 bytes |
| `docs/physical_architecture.png` | All | figure used in the D.4 documents | 207,564 bytes |

## digests

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `digests/.gitkeep` | Ogunjebi | keeps the digest log folder (digest files are written at run time) | 0 bytes |

## results

| File | Owner | Purpose | Lines or size |
|---|---|---|---|
| `results/.gitkeep` | Pratap | keeps the results folder (summary.csv, attacks.jsonl) | 0 bytes |

Python source in total: 5,270 lines, including comments and docstrings.
