# Database schemas

## MongoDB

MongoDB does not enforce a schema, so `mongodb_schema.json` documents what the code writes, one
`$jsonSchema` per collection. The databases are:

| Database | Collections | Written by |
|---|---|---|
| `bank` | `accounts` | SmallBank loader and workload (`workloads/smallbank.py`) |
| `analytics` | `accounts`, `customers`, `transactions` | sample_analytics loader and trading workload (`workloads/analytics.py`) |
| `ledger_a` | `blocks`, `entries`, `meta` | Design A service (`ledger/design_a.py`, `ledger/mongo_store.py`) |
| `ledger_s` | `blocks`, `entries`, `heads`, `pending` | Design S library and fold step (`ledger/design_s.py`) |

Relationships (logical, not enforced):

```
blocks 1 ──< entries            entries.block_id = blocks.block_id, ordered by entries.idx
blocks 1 ── 1 blocks            blocks.prev_hash = previous blocks.block_hash (hash chain)
entries >── 1 protected doc     entries.ns + entries.key identify one document
heads 1 ──< pending / entries   Design S: head, seq and prev form one chain per head
customers >──< accounts         customers.accounts contains accounts.account_id
accounts 1 ──< transactions     transactions.account_id = accounts.account_id (bucket pattern)
```

Indexes created by the code: unique `blocks.block_id`; unique `entries (block_id, idx)`;
unique `pending (head, seq)`; `analytics.accounts.account_id`;
`analytics.transactions (account_id, transaction_count)`; `analytics.customers.accounts`.

To apply a validator for one collection (optional, moderate level so existing documents are not rejected):

```javascript
// mongosh
const s = JSON.parse(fs.readFileSync("schema/mongodb_schema.json"))["bank.accounts"];
delete s["$comment"];
db.getSiblingDB("bank").runCommand({collMod: "accounts", validator: {$jsonSchema: s}, validationLevel: "moderate"});
```

## SQL Server

`sqlserver/01_schema.sql` creates the `LedgerBench` database with plain tables (`bank_plain`,
`usertable_plain`) and updatable ledger tables (`bank_ledger`, `usertable_ledger`, each with a
history table). SQL Server adds the hidden ledger columns (`ledger_start_transaction_id`,
`ledger_end_transaction_id`, `ledger_start_sequence_number`, `ledger_end_sequence_number`) and keeps
the ledger in `sys.database_ledger_transactions` and `sys.database_ledger_blocks`.

## How to connect

| System | Connection | Notes |
|---|---|---|
| MongoDB (Docker or Codespaces) | `mongodb://localhost:27017/?replicaSet=rs0&directConnection=true` | No authentication in the testbed. Set `MONGO_URI` to use another server. |
| MongoDB shell | `docker compose exec mongo mongosh` | Then `use bank` or `use ledger_a`. |
| MongoDB Compass | the same URI as above | Read only browsing of data and ledgers. |
| SQL Server | `localhost,1433`, login `sa`, password from `.env` (`MSSQL_SA_PASSWORD`) | Database `LedgerBench`; use `TrustServerCertificate=yes`. |
| SQL Server shell | `docker compose exec mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C` | Prompts for the password. |
