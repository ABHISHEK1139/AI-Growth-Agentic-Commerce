# Backup and Restore Runbook

**Severity:** P2 (routine) / P1 (recovery)
**On-call:** platform
**Frequency:** nightly, with 14-day retention

The AgentPay database is backed up nightly via
`python -m infra.operations.backup`. Backups are compressed
`pg_dump` snapshots stored under `/var/backups/agentpay/` (or the
destination configured in the deployment). The script prunes
snapshots older than 14 days by default.

This runbook covers:

* Verifying that a backup ran successfully.
* Restoring from a snapshot (full database, or a single table for
  point-in-time analysis).

---

## Verifying a backup

The backup script prints a single line per run, e.g.:

```
[backup] starting at 2026-01-15T00:00:00Z: connecting to postgresql://user:***@host:5432/db -> agentpay_nightly_20260115T000000Z.sql.gz
[backup] wrote /var/backups/agentpay/agentpay_nightly_20260115T000000Z.sql.gz (240.3 MB)
[backup] pruning old snapshot agentpay_nightly_20260101T000000Z.sql.gz
```

The exit code is `0` on success. The CI integration job asserts this
on every run.

A scheduled backup that did not produce a file in the expected window
is a `P2` alert. The first thing to check is the cron schedule:

```bash
crontab -l | grep infra.operations.backup
```

The expected cron line is:

```
0 0 * * * . /var/lib/agentpay/.env && /usr/bin/python -m infra.operations.backup --dest /var/backups/agentpay --label nightly --retention-days 14 >> /var/log/agentpay/backup.log 2>&1
```

If the cron is present but the script is failing, run it by hand to
see the error:

```bash
. /var/lib/agentpay/.env
python -m infra.operations.backup --dest /var/backups/agentpay --label debug
```

The most common failure modes:

* `pg_dump: command not found` — install `postgresql-client`.
* `connection to server ... timeout` — the database was unreachable
  when the backup ran. See [database-failure](./database-failure.md).
* `permission denied for schema public` — the backup user does not
  have read access. The expected role is `agentpay_backup` with
  `SELECT` on every table.

## Restoring from a snapshot

### Step 1: Stop the gateway

The API and worker must not be running during a restore. A
transaction that lands between the restore and the next read will
have its data silently overwritten by the older snapshot.

```bash
docker compose stop api worker
```

### Step 2: Pick a snapshot

```bash
ls -lh /var/backups/agentpay/
```

Choose the snapshot whose timestamp best matches the point you want to
recover. The filename encodes the timestamp:

```
agentpay_<label>_<UTC-timestamp>.sql.gz
# e.g. agentpay_nightly_20260115T000000Z.sql.gz
```

### Step 3: Restore

```bash
. /var/lib/agentpay/.env  # exports DATABASE_URL
python -m infra.operations.restore \
  --snapshot /var/backups/agentpay/agentpay_nightly_20260115T000000Z.sql.gz \
  --yes
```

The `--yes` flag is required when the database already has tables.
The script refuses without it so a typo in the snapshot path cannot
silently overwrite production.

### Step 4: Replay the gap

The restored database is now at the snapshot's point in time. Any
orders or payments that arrived between the snapshot and the
restore are gone. To recover them:

1. Replay webhooks for the gap (see
   [webhook-replay](./webhook-replay.md)). The provider's delivery
   log holds the originals.
2. Manually reconcile any orders whose buyers saw "payment failed"
   during the gap. The
   [payment-incident runbook](./payment-incident.md) walks through
   this.

### Step 5: Restart the gateway

```bash
docker compose start api worker
```

Confirm with the readiness probe:

```bash
curl -fsS https://api.example.com/health/ready | jq .
```

## Restoring a single table (point-in-time analysis)

Restoring the whole database is heavy. For analysis only, restore into
a sidecar database:

```bash
# Create a sidecar database
createdb -h localhost -U agentpay agentpay_restore_investigation

# Restore into it
DATABASE_URL=postgresql://agentpay:***@localhost/agentpay_restore_investigation \
  python -m infra.operations.restore \
    --snapshot /var/backups/agentpay/agentpay_nightly_20260115T000000Z.sql.gz

# Query the sidecar
psql -h localhost -U agentpay agentpay_restore_investigation -c \
  "SELECT count(*) FROM payment WHERE status = 'captured' AND created_at > '2026-01-15';"
```

The sidecar database does not affect the live gateway. Drop it when
done:

```bash
dropdb -h localhost -U agentpay agentpay_restore_investigation
```

## Recovery drills

A backup is only as good as the last restore. The platform team runs a
monthly recovery drill:

1. Pick a recent snapshot.
2. Restore it into a sidecar database.
3. Run a fixed set of read-only queries against the sidecar.
4. Confirm the row counts match the production database at the
   snapshot's timestamp.

The drill's results are recorded in the platform on-call channel. A
missed drill is a `P3` issue; a failed drill is a `P2` until the gap
is closed.
