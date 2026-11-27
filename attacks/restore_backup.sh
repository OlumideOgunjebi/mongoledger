#!/usr/bin/env bash
# ============================================================================
# COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
# Team # 3: A Change Stream Ledger for MongoDB
# Repository: github.com/OlumideOgunjebi/mongoledger
# File:    attacks/restore_backup.sh
# Owner:   Olumide Ogunjebi (verification and security lead)
# Purpose: attack 7: restore an older file backup of the data and both ledgers
# Covers:  FR25; acceptance test AC13 (see docs/D.4.1)
# ============================================================================

# Attack: restore an older backup.
#
#   bash attacks/restore_backup.sh snapshot      copy the whole data volume (data and ledgers)
#   bash attacks/restore_backup.sh rollback      put that old volume back (rolls back the ledgers too)
#   bash attacks/restore_backup.sh dump          mongodump of the protected database only
#   bash attacks/restore_backup.sh restore-data  mongorestore --drop of the protected database only
#
# rollback should raise digest_without_block, because the digest log outside the
# database remembers blocks the restored ledger no longer has. restore-data
# should raise modified_document (and history_break on the next ledgered change).
set -euo pipefail
mkdir -p backups results
DB="${DATA_DB:-bank}"
note() { python -c "import json, time; open('results/attacks.jsonl','a').write(json.dumps({'at': time.time(), 'kind': 'attack', 'name': '$1'})+'\n')"; }

case "${1:-}" in
  snapshot)
    docker compose stop mongo
    docker compose run --rm --no-deps --entrypoint tar mongo czf /backups/mongo_volume.tgz -C /data/db .
    docker compose start mongo
    echo "volume snapshot written to backups/mongo_volume.tgz" ;;
  rollback)
    docker compose stop mongo
    docker compose run --rm --no-deps --entrypoint sh mongo -c \
      "rm -rf /data/db/* && tar xzf /backups/mongo_volume.tgz -C /data/db"
    docker compose start mongo
    note restore_volume_rollback
    echo "old volume restored; now run: python -m attacks.run_attacks report" ;;
  dump)
    docker compose exec -T mongo mongodump --db "$DB" --archive=/backups/"$DB".archive
    echo "protected database dumped to backups/$DB.archive" ;;
  restore-data)
    docker compose exec -T mongo mongorestore --drop --archive=/backups/"$DB".archive
    note restore_data_only
    echo "protected database restored; now run: python -m attacks.run_attacks report" ;;
  *)
    echo "usage: bash attacks/restore_backup.sh snapshot|rollback|dump|restore-data"; exit 1 ;;
esac
