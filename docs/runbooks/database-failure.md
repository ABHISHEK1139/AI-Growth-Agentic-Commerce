# Database Failure Runbook

**Severity:** P1 (full outage) / P2 (replica lag, slow queries)
**On-call:** platform + on-call lead
**Alert source:** `worker_heartbeat_lost`, `worker_backlog_high`, the API
readiness probe

The AgentPay commerce database is the single source of truth for every
order, payment, inventory hold, and audit record. A database outage
**stops the gateway from accepting new orders** but does not affect
already-completed orders (their audit trail is read-only after
`captured` + `confirmed`).

This runbook covers the diagnosis and recovery path for a PostgreSQL
outage.

---

## Step 1: Confirm the failure

The API's `/health/ready` probe returns the current migration version
and database connectivity. A failure here is the first signal:

```bash
curl -fsS https://api.example.com/health/ready | jq .
```

If the probe returns a 5xx, the database is unreachable from the API.
The worker's `WORKER_JOB_FAILED` log line is the same signal from the
worker's perspective.

## Step 2: Identify the failure mode

| Symptom | Likely cause | Action |
| ------- | ------------ | ------ |
| Probe fails, all connections time out | PostgreSQL process is down or network is partitioned | Restart the database (see Step 3a) |
| Probe fails, "too many connections" | Connection pool is exhausted | Restart the API to drop stale connections (Step 3b) |
| Probe succeeds, queries are slow | Long-running transaction, replica lag, or a vacuum storm | Identify and kill the offender (Step 3c) |
| Probe succeeds, intermittent 5xx | Connection pool or DNS issue | Restart the API (Step 3b) |

## Step 3a: Restart the database

In a managed environment, use the provider's "restart" command.
Locally:

```bash
docker compose restart postgres
```

The API and worker will reconnect automatically once PostgreSQL is
back. The worker's reconciliation job catches up on any payments
that arrived during the outage — see the
[payment-incident runbook](./payment-incident.md) if any of them
disagree with the provider.

## Step 3b: Restart the API

A connection-pool exhaustion is fixed by a rolling restart of the
API. The gateway's idempotency layer makes this safe: a buyer who
retries during the restart will be answered with the same response the
first attempt produced.

```bash
docker compose restart api
```

If the issue recurs, the connection pool is sized too small. Adjust
`DATABASE_POOL_SIZE` in the API's environment and roll out a new
deployment.

## Step 3c: Kill a long-running query

```sql
-- Identify long-running queries
SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
FROM pg_stat_activity
WHERE (now() - pg_stat_activity.query_start) > interval '30 seconds'
  AND state != 'idle'
ORDER BY duration DESC;

-- Cancel and terminate if safe to do so
SELECT pg_cancel_backend(<pid>);  -- polite
SELECT pg_terminate_backend(<pid>);  -- forceful
```

**Do not** cancel a query that is in the middle of a payment state
transition. The state machine is not designed to be cancelled mid-flight
— a partial cancellation can leave a payment in `created` with a
provider `captured` status, which is the same shape as a webhook
missed. If in doubt, let the query finish.

## Step 4: Verify

After recovery, confirm:

1. `/health/ready` returns 200 with the latest migration version.
2. The worker's heartbeat resumes (`WORKER_HEARTBEAT` log lines every
   30 s).
3. The DLQ reconcile job processed the entries that piled up during
   the outage.
4. A test order can be created end-to-end.

## Step 5: Restore from backup

If the database is corrupt or the data is wrong (e.g. a manual
`DELETE` in production), restore from a snapshot. The procedure is
documented in [backup-restore](./backup-restore.md).

**Restoring overwrites the current database.** The script refuses to
do so without an explicit `--yes` flag for a reason: a restore cannot
be undone.

## Step 6: Postmortem

A database outage of any severity requires a postmortem. Required
sections:

* Timeline (UTC, with the alert timestamps and the customer impact
  timestamps).
* Root cause — was it disk, network, a bad query, a configuration
  change, a managed-service incident, or something else?
* Customer impact — how long were orders blocked? How many buyers
  were affected? Was the gateway accepting orders it should not have?
* Action items, each with an owner and a date.

Save the postmortem under `docs/postmortems/<YYYY-MM-DD>-<slug>.md`.

## Don't do this

* **Do not** drop and recreate the database to "fix" an outage. The
  audit trail is the database.
* **Do not** modify the `payment` or `order` tables directly to catch
  up after an outage. The state machine, the audit log, and the
  downstream notifications all flow through the service. A direct
  UPDATE will leave the audit log empty for the change.
* **Do not** scale the database up as a reflex. If the issue is a
  missing index, scaling up does not help. Always diagnose first.
