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
# Underscore naming matches infra/operations/backup.py, so the Python
# retention pruner recognises (and eventually cleans) these files too.
BACKUP_FILE="${BACKUP_DIR}/agentpay_manual_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

# Never print DATABASE_URL: it embeds the password and this script runs from
# cron, where stdout is mailed or shipped to a log aggregator. Show the
# destination and a redacted user@host only.
REDACTED_URL="$(printf '%s' "${DATABASE_URL}" | sed -E 's|://([^:/?#]+):[^@/?#]+@|://\1:***@|')"
echo "Backing up ${REDACTED_URL} to ${BACKUP_FILE}"

# A failed pg_dump must not leave a partial archive that a later restore
# mistakes for a good backup.
trap 'rm -f "${BACKUP_FILE}"' ERR
pg_dump \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    --format=plain \
    "${DATABASE_URL}" | gzip -9 > "${BACKUP_FILE}"
trap - ERR

echo "Backup complete: $(stat -c %s "${BACKUP_FILE}") bytes"
