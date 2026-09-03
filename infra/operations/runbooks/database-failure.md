# Database failure runbook

## Symptoms

* API process is restarting on its own.
* Worker log shows repeated "DB unavailable in test mode" warnings
  that are not actually the test mode.
* `GET /api/v1/health` reports `database=down`.

## Triage

1. **Confirm the database is reachable from the API host.**

   ```bash
   psql "${DATABASE_URL}" -c "SELECT 1"
   ```

2. **Check the database's own health.**

   - On Postgres: `SELECT pg_is_in_recovery();` — if `t`, the
     database is a read replica and your writes are failing.
   - `SELECT * FROM pg_stat_activity WHERE state = 'active';` —
     are there any long-running queries holding locks?

3. **Check the connection pool.** The API uses SQLAlchemy's default
   pool; if a long query holds a connection, the next request blocks.
   `pg_stat_activity` will show the offender.

## Mitigation

### Postgres is unreachable

1. Restart the API and worker (`docker compose restart api worker`).
2. If the issue persists, fail over to the replica. The connection
   string is in `DATABASE_URL`; the replica lives at `DATABASE_REPLICA_URL`.
3. Open a ticket with the database provider.

### Long-running query is blocking

1. Identify the query in `pg_stat_activity`.
2. Decide: wait it out, or `pg_terminate_backend(pid)`. Killing a
   query is reversible (the next request will retry); killing a
   transaction in the middle of a payment is not.

### Read-replica drift

If a write returned successfully but the next read can't see it, you
hit replica lag. The fix is on the writer's side: configure the API
to read from the primary until replica lag drops below 1 second.

## Root cause

* **Connection pool exhaustion.** A spike in checkout traffic held all
  pool slots. The fix is in
  `apps/api/config.py::database_pool_size` and the SQLAlchemy engine
  configuration.
* **Migration drift.** A migration ran on the primary but not the
  replica. Re-run the migration on the replica.
* **DNS flapping.** The `DATABASE_URL` host has changed IPs. Pin the
  IP or use a load-balanced CNAME.

## Postmortem checklist

- [ ] How long was the database unreachable? (from alert timestamp to
      recovery)
- [ ] How many in-flight requests were affected?
- [ ] Was the connection-pool size sufficient? (the answer is
      usually "no")
- [ ] Edit this runbook with anything that surprised you.
