# Worker stuck runbook

## Symptoms

* `WORKER_HEARTBEAT_LOST` alert (if you've added one; see
  `services/operations/alerts.py` for the closed set).
* `GET /api/v1/operations/alerts` shows the heartbeat is stale.
* The DLQ is growing but no reconcile runs.

## Triage

1. **Confirm the worker process is up.**

   ```bash
   docker compose ps worker
   # or, in a non-compose deployment:
   systemctl status agentpay-worker
   ```

2. **Read the worker's tail of the log.**

   ```bash
   docker compose logs --tail=200 worker
   ```

   Look for repeated tracebacks or for a job that's been running
   longer than its expected duration.

3. **Check the database lock view** for a worker-held lock that
   hasn't been released.

   ```sql
   SELECT pid, state, wait_event_type, wait_event, query
   FROM pg_stat_activity
   WHERE application_name LIKE '%agentpay%worker%';
   ```

## Mitigation

### Process is dead

```bash
docker compose restart worker
```

If the worker keeps dying, read the traceback in the log. The most
common cause is a malformed cron expression in `build_jobs()` or a
deadlock between the reconcile and poll jobs.

### Job is wedged

Restart the worker. The job registry is a clean Python list; a
restart drops the in-flight job without committing half a
transaction, because each job is a `with factory() as session:`
block.

### Database is unreachable

Run [`database-failure.md`](database-failure.md) first. The worker
cannot recover from a missing database on its own.

## Root cause

* **Long-running sweep.** A single sweep holds a row lock and the
  next sweep blocks on `with_for_update(skip_locked=True)`. Add
  per-sweep timeouts.
* **Two workers on the same database.** Compose usually prevents
  this, but a manual `docker run` can put a second worker against
  the same store. The DLQ row lock should keep them safe, but the
  throughput drops.
* **Database connection leak.** A sweep ran a query, caught an
  exception, and did not close the cursor. `pg_stat_activity` will
  show `idle in transaction`; the fix is in the sweep code.

## Postmortem checklist

- [ ] How long was the worker stuck? (from heartbeat lost to
      restart)
- [ ] Did the DLQ grow beyond its expected size during the outage?
- [ ] Was there a downstream cascade (webhook alerts firing because
      the DLQ reconcile didn't run)?
- [ ] Edit this runbook with anything that surprised you.
