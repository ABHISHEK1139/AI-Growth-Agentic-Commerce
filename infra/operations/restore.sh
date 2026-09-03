#!/usr/bin/env bash
# AgentPay database restore script.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/db \
#   RESTORE_FILE=/var/backups/agentpay/agentpay-20260101T000000Z.sql.gz \
#   ./infra/operations/restore.sh
#
# Confirms the file exists, decompresses it, and pipes it to psql. The
# script refuses to run unless RESTORE_CONFIRM=yes is also set, to make
# a wrong invocation from history expensive rather than catastrophic.
#
# Restore is destructive: it will overwrite the current schema and data.
# Run it against a fresh database, or a database that has been confirmed
# disposable. Production restores belong behind a maintenance window.

set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set, e.g. postgresql://user:pass@host:5432/db}"
: "${RESTORE_FILE:?RESTORE_FILE must be set, e.g. /var/backups/agentpay/agentpay-...sql.gz}"
: "${RESTORE_CONFIRM:=no}"

if [[ "${RESTORE_CONFIRM}" != "yes" ]]; then
    echo "Refusing to restore without RESTORE_CONFIRM=yes" >&2
    exit 1
fi

if [[ ! -f "${RESTORE_FILE}" ]]; then
    echo "Restore file not found: ${RESTORE_FILE}" >&2
    exit 1
fi

echo "Restoring ${RESTORE_FILE} into ${DATABASE_URL}"
gunzip -c "${RESTORE_FILE}" | psql \
    --single-transaction \
    --set ON_ERROR_STOP=on \
    --no-psqlrc \
    "${DATABASE_URL}"

echo "Restore complete"
