# Operations scripts

This directory contains the production ops surface that lives outside the
Python application itself.

## `backup.sh`

A logical `pg_dump` of the AgentPay database. Designed to be run from cron
every hour; produces a timestamped `.sql.gz` under the configured
`BACKUP_DIR`. Required environment:

| Variable        | Example                                                  |
| --------------- | -------------------------------------------------------- |
| `DATABASE_URL`  | `postgresql://agentpay:agentpay@db:5432/agentpay`         |
| `BACKUP_DIR`    | `/var/backups/agentpay`                                  |

A typical crontab entry:

```cron
0 * * * * DATABASE_URL=... BACKUP_DIR=/var/backups/agentpay /opt/agentpay/infra/operations/backup.sh
```

## `restore.sh`

Decompresses a backup file and runs it through `psql`. **Destructive.**
Refuses to run unless `RESTORE_CONFIRM=yes` is set, to make a mis-pasted
command a 1-second `exit 1` rather than a multi-minute data loss.

Required environment:

| Variable           | Example                                                  |
| ------------------ | -------------------------------------------------------- |
| `DATABASE_URL`     | `postgresql://agentpay:agentpay@db:5432/agentpay`         |
| `RESTORE_FILE`     | `/var/backups/agentpay/agentpay-20260101T000000Z.sql.gz` |
| `RESTORE_CONFIRM`  | `yes` (refuses otherwise)                                |

## `runbooks/`

Step-by-step incident response. Each runbook lists the alert that
triggers it, the diagnostic commands, and the recovery actions in the
order a tired on-call engineer should follow them. See
`runbooks/README.md` for the index.
