# Project management

COMP.8157 Advanced Database Topics, Fall 2026, Team # 3: A Change Stream Ledger for MongoDB.

| | Link |
|---|---|
| Project management tool (Hive) | https://projects.hive.com/?shareToken=avt_9Q3eSs2szmzB2ezkZoZhHxzNcdWdDfMwERWWXs7WgJQZMKusetkc5EpM87Ac2yA4 |
| Code repository | https://github.com/OlumideOgunjebi/mongoledger |
| Run in the browser | https://codespaces.new/OlumideOgunjebi/mongoledger |

Hive holds the live status of every task below, with its owner, dates and notes. This page records
how the project is managed and how each management principle appears in the repository.

## 1. Scope and requirements

The scope was fixed by the approved proposal (D.2.1) and written as 27 functional and 13 non
functional requirements with 16 acceptance criteria in D.4.1. Every source file names the
requirements it implements in its header, and each test that checks an acceptance criterion names it,
so each requirement can be traced from the document to the code and to its test.

## 2. Work breakdown and responsibilities

The work is broken into nineteen tasks in the order in which they depend on each other. The status
column is the one reported in the midterm status report (D.3.1, 4 November 2026); Hive shows the
current status.

| ID | Task | Owner | Depends on | Status at D.3.1 |
|---|---|---|---|---|
| T01 | Project idea, threat model, proposal and presentation (D.1, D.2.1, D.2.2) | All | none | Completed |
| T02 | User requirements and analysis (D.4.1) | All | T01 | Completed |
| T03 | Analysis and design document (D.4.2) | All | T02 | Completed |
| T04 | Canonical hashing and RFC 9162 Merkle trees with inclusion proofs | Nelluri | T03 | Completed |
| T05 | Design S library: transaction wrapper, k chain heads, fold into blocks | Nelluri | T04 | Completed |
| T06 | Design A service: change stream, resume tokens, gap blocks, transaction grouping | Pratap | T04 | Completed |
| T07 | Digest log outside the database with optional Git witness | Ogunjebi | T04 | Completed |
| T08 | Verifier: chain, digests, state replay against a full scan, cross check | Ogunjebi | T05, T06, T07 | Completed |
| T09 | Command line and search commands (history, blocks, digests, prove) | Ogunjebi | T08 | Completed |
| T10 | SmallBank and sample_analytics workloads; benchmark harness | Pratap | T05, T06 | Completed |
| T11 | Insider attack suite (seven actions of D.4.1 Table 2) | Ogunjebi | T08, T10 | Completed |
| T12 | Docker Compose, Codespaces, CI workflow, quickstart and health check scripts | Pratap | T05 to T09 | Completed |
| T13 | Automated tests | All | T04 to T10 | Completed |
| T14 | SQL Server 2022 Ledger baseline | Nelluri | T10 | In progress |
| T15 | YCSB workloads A and B on MongoDB and SQL Server | Pratap | T10, T14 | Remaining |
| T16 | Measurement campaign: 1 to 32 clients, head and interval sweeps, five repeats | Pratap, Nelluri | T10, T14, T15 | Remaining |
| T17 | Source committed by component, pull requests, release tag v1.0.0 | All | T13 | In progress |
| T18 | Final D.4.3, D.4.4, D.4.5 and the final report D.5 | All | T16, T17 | Remaining |
| T19 | In class presentation (D.6) | All | T18 | Remaining |

Roles: Olumide Ogunjebi is the verification and security lead and coordinates the team (repository
owner, Hive board); Rohith Nelluri is the database lead (ledger core, Design S, SQL Server); Yadu
Pratap is the deployment lead (Design A, workloads, containers, Codespaces, CI). Shared documents are
written jointly and reviewed by all three members.

## 3. Schedule and critical path

| Milestone | Date |
|---|---|
| D.2.1 Full proposal | 4 October 2026 |
| D.2.2 Proposal presentation | 6 October 2026 |
| D.4.1 User requirements and analysis | 23 October 2026 |
| D.4.2 Design document | 30 October 2026 |
| Live prototype run, all seven insider actions detected | 31 October 2026 |
| D.3.1 Midterm status report, D.3.2 individual reports | 4 November 2026 |
| D.4.3 Application, D.4.4 Deployment, D.4.5 User guide, D.5 Final report | 1 December 2026 |

The sequence follows the data path of the system: hashing and Merkle trees (T04) come before both
designs (T05, T06), which come before the verifier (T08); the measurement campaign (T16) needs the
workloads and both baselines (T14, T15). The critical path to the final report is therefore T14,
T15, T16 and T18.

## 4. Risk register

| Risk | Effect | Response |
|---|---|---|
| Contention on Design S chain heads | Design S throughput far below unprotected MongoDB | Head count is an experiment parameter; sweep 1 to 64 heads |
| SQL Server has no arm64 image | Baseline cannot run on Apple Silicon | `platform: linux/amd64` in Compose; baseline runs on an Intel or Windows laptop |
| Measurements vary between laptops | Results not comparable | Fixed seeds, container limits of 2 CPUs and 4 GB, five repeats |
| Oplog window shorter than a service outage | Changes made during the outage are not in the stream | Gap block (code 286) plus a full scan at the next verification |
| A secret committed to Git | SQL Server password exposed | Password only in a local `.env`, which `.gitignore` excludes; `.env.example` holds a placeholder |

## 5. Version control and change control

Each component is committed by the member who owns it, on a branch, through a pull request that
another member reviews before it is merged into `main`; the continuous integration workflow
(`.github/workflows/tests.yml`) runs the unit tests, the live replica set tests and the demonstration
on every push. Commit messages describe the work they contain. The delivered version is tagged
`v1.0.0`. Changes to scope are recorded as requirements: the sample_analytics workload and the
history, blocks, digests and prove commands were added after the proposal as FR19, FR20 and FR23 in
D.4.1, with no change to the schedule.

## 6. Quality and monitoring

Quality is measured against the acceptance criteria of D.4.1. The unit tests run on an in memory
stand in for MongoDB (`tests/fake_mongo.py`), and the integration tests run against a live replica
set. Progress was reported in the midterm status report (D.3.1) and in each member's individual
report (D.3.2), and problems found during testing are kept in the incident log of the deployment
document (D.4.4).
