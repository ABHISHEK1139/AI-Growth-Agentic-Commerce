#!/usr/bin/env bash
# AgentPay database backup script.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/db \
#   BACKUP_DIR=/var/backups/agentpay \
#   ./infra/operations/backup.sh
#
# Produces a timestamped, gzipped logical dump under ${BACKUP_DIR}.
# Designed to be safe to run from cron: it locks nothing, requires only
# the standard `pg_dump` client, and writes to a fresh file every time.
#
# Why a bash script rather than a Python one:
#   - The cron runner is the bash, not a venv. Avoiding the dependency
#     on `python -m` keeps this script callable from a bare container.
#   - `pg_dump | gzip` is the canonical Postgres backup idiom; any
#     operator reading the script sees exactly what runs.

set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set, e.g. postgresql://user:pass@host:5432/db}"
: "${BACKUP_DIR:?BACKUP_DIR must be set, e.g. /var/backups/agentpay}"

# Timestamp is the UTC ISO 8601 with colons replaced — colons are awkward in
# filenames and `:%z` doesn't strip them on every toolchain.
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="${BACKUP_DIR}/agentpay-${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

echo "Backing up ${DATABASE_URL} to ${BACKUP_FILE}"
pg_dump \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    --format=plain \
    "${DATABASE_URL}" | gzip -9 > "${BACKUP_FILE}"

echo "Backup complete: $(stat -c %s "${BACKUP_FILE}") bytes"
