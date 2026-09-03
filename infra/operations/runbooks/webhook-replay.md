# Webhook replay runbook

## Symptoms

* `WEBHOOK_RETRY_EXHAUSTED` alert fires with `severity=critical`.
* `GET /api/v1/operations/alerts` shows repeated dead-letter entries
  with the same `event_id`.
* Merchant reports "I paid but the order is stuck in pending".

## Triage

1. **Read the alert payload.** It includes `provider`, `event_type`,
   `event_id`, and `last_error`. The `last_error` is the most useful
   field — the reason the worker gave up retrying.
2. **Check the dead-letter table.**

   ```sql
   SELECT failed_webhook_id, provider, event_type, attempt_count,
          last_error, created_at
   FROM failed_webhook
   WHERE status = 'pending'
   ORDER BY created_at DESC
   LIMIT 20;
   ```

3. **Check the provider dashboard.** Compare the provider-side event id
   (in the alert's `event_id`) with the merchant's local payment
   record. The most common mismatch is that the local payment was
   created from a different provider order than the webhook targets.

## Mitigation

If the underlying cause is fixable on our side (e.g. the local payment
record was wrong), fix the local record and replay the webhook:

```bash
DATABASE_URL=... .venv/bin/python -c "
from apps.api.db import get_session_factory
from services.payments.webhooks import WebhookProcessor
factory = get_session_factory()
with factory() as session:
    proc = WebhookProcessor()
    print(proc.process_webhook(
        session,
        raw_body=open('/tmp/evt_42_body.bin','rb').read(),
        signature=open('/tmp/evt_42_sig.txt').read().strip(),
    ))
"
```

If the underlying cause is a provider bug (Razorpay sent us a duplicate
with the wrong order id), there is nothing to replay — close the
dead-letter entry and document the provider incident.

```sql
UPDATE failed_webhook
SET status = 'resolved',
    resolved_at = NOW(),
    resolution_note = 'Provider bug — see incident-2026-09-02'
WHERE failed_webhook_id = '...';
```

## Root cause

* **Reconciliation mismatch.** The provider says one thing, we say
  another. Run [`reconciliation-mismatch.md`](reconciliation-mismatch.md)
  next.
* **Missing inventory row.** The local payment was created without an
  inventory reservation; the provider says the buyer paid, but the
  inventory service refused to confirm. Backfill the reservation and
  replay.
* **Schema migration drift.** A column was renamed but a webhook
  processor still references the old name. Re-deploy.

## Postmortem checklist

- [ ] How long was the webhook stuck? (from `created_at` to
      `WEBHOOK_RETRY_EXHAUSTED` time)
- [ ] Was the merchant notified within their SLA? (compare timestamps)
- [ ] Did the DLQ grow beyond its expected size?
- [ ] Edit this runbook with anything that surprised you.
