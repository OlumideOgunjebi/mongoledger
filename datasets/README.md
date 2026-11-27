# Datasets

## sample_analytics (stored here)

| File | Documents | Uncompressed size | SHA256 of the uncompressed file |
|---|---|---|---|
| `sample_analytics/accounts.json.gz` | 1,746 | 302,693 bytes | `cb3a611e49ab312b902a07f3da9354eacc079026d44bc21c370f772a0fa6d9a7` |
| `sample_analytics/customers.json.gz` | 500 | 246,237 bytes | `7fc9ed04b8852b256e95e136ade3681475ae0176c6847dff11207f8b773faafb` |
| `sample_analytics/transactions.json.gz` | 1,746 buckets, 88,119 trades | 20,141,601 bytes | `89dfa0d665faf6a6582a1c6a7543c1034404d4dbe3bbbb2d1f4ae73c8f5f9ee0` |

**Source.** MongoDB's sample dataset for a fictitious financial services company, published for
MongoDB Atlas ("Load Sample Data"):
https://www.mongodb.com/docs/atlas/sample-data/sample-analytics/. The files were downloaded from the
JSON export at https://github.com/neelabalan/mongodb-sample-dataset (folder `sample_analytics`),
which exports the Atlas sample databases with one canonical Extended JSON document per line, and
compressed with gzip. The checksums above let anyone confirm the files are unchanged.

**Content.** All people, addresses and emails are generated, not real. `accounts` holds
`account_id`, `limit` and `products`; `customers` holds personal details and the list of account ids a
customer owns; `transactions` uses the bucket pattern, one document per account with up to 100 trades
(`date`, `amount`, `transaction_code`, `symbol`, `price`, `total`). One `account_id` (of 1,746)
appears twice in `accounts`, as in the original dataset.

**Load.** `python -m workloads.analytics load --db analytics` (about ten seconds), or without the
project: `gunzip -k *.gz` then `mongoimport --db analytics --collection accounts --file accounts.json`
for each file.

## SmallBank (generated)

Created by `MongoBank.load(n)` and `SqlBank.load(n)` in `workloads/smallbank.py`: customers numbered
from 0 up to n minus 1, named `cust0000000` onward, each with savings and checking of 1,000,000 cents. The
experiments use 100,000 customers; the demonstration uses 1,000. The workload is SmallBank
(Alomari, Cahill, Fekete and Röhm, ICDE 2008) with the BenchBase transaction mix.

## YCSB (generated)

Created by the YCSB 0.17.0 `load` phase (https://github.com/brianfrankcooper/YCSB/releases):
1,000,000 records in `usertable`, each a key and ten fields of 100 random bytes. See `ycsb/README.md`.
