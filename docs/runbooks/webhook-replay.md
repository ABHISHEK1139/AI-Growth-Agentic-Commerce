# Webhook Replay Runbook

**Severity:** P2
**On-call:** payments team
**Alert source:** `webhook_dead_letter`, `webhook_retry_exhausted`

A payment provider (Razorpay, or a custom gateway) sends a webhook to
`POST /api/v1/webhooks/razorpay` when a payment state changes. The
`WebhookProcessor` verifies the HMAC signature, deduplicates by event ID,
and applies the state transition. When a webhook **cannot** be applied
— because the database was unreachable, the referenced payment no longer
exists, the provider reported a status that disagrees with our local
state, or anything else fails between signature verification and the
state-machine commit — the processor **moves the event to the
`failed_webhook` dead-letter queue** instead of returning an error.

Returning an error would make the provider retry the same payload
forever, which is what we want to avoid. Dead-lettering is the right
behaviour, but it means a human must intervene.

This runbook describes the manual replay procedure.

---

## When to use this runbook

You were paged because one of:

* `ALERT_FIRED` with `alert_kind=webhook_retry_exhausted` and
  `severity=critical` — the worker has retried an entry five times
  without success.
* A `WEBHOOK_PROCESSING_FAILED` log line whose error message is not
  obviously transient.
* A merchant reports a paid order that the gateway confirms but the
  database does not. (See the [payment-incident runbook](./payment-incident.md).)

## Step 1: Identify the entry

Every dead-letter entry has a `failed_webhook_id` and a
`provider_event_id`. They appear in the alert's `context` block and in
the worker logs.

```sql
SELECT
  failed_webhook_id,
  provider,
  event_type,
  attempt_count,
  last_error,
  next_retry_at,
  created_at
FROM failed_webhook
WHERE status = 'pending'
ORDER BY created_at DESC
LIMIT 20;
```

If the entry has `attempt_count >= max_attempts`, the worker has
stopped retrying. Otherwise the worker will retry on its own; **wait
one more cycle** before replaying manually.

## Step 2: Diagnose the failure

Read `last_error`. The most common shapes are:

| Error message (abbreviated)                                          | Root cause                              | Action |
| -------------------------------------------------------------------- | --------------------------------------- | ------ |
| `Provider payment status 'failed' is uncaptured`                     | Provider reports the payment as failed. | Do **not** replay. Mark `status = 'rejected'` and refund if needed. |
| `Independent provider verification failed: provider fetch error`     | Provider was unreachable at the time.   | Replay once the provider is back. |
| `Webhook payload is missing required provider payment identifier.`   | Provider sent a malformed payload.      | Contact provider support; do not replay. |
| `Payment not found for webhook`                                      | Payment row was deleted or never created. | Confirm with the merchant; if legitimate, create the payment then replay. |

## Step 3: Replay

The replay path is `POST /api/v1/payments/razorpay/webhook` with the
**original raw body and signature** — *not* a re-serialised payload.
Even a byte-identical re-serialisation will fail the HMAC check.

If you have the original raw body (e.g. from the provider's delivery
log), re-POST it. Otherwise:

```python
# In a one-off script: read the failed_webhook row, re-construct the
# raw body, and POST it back to the gateway. Use the same signature.
import httpx, json
from apps.api.config import get_settings
from apps.api.db import get_session_factory
from services.payments.models import FailedWebhook

with get_session_factory()() as session:
    entry = session.query(FailedWebhook).get("the-failed-webhook-id")
    # The original raw body is *not* preserved in failed_webhook;
    # this is by design. The replay path requires the original HTTP
    # request, which the provider's delivery log holds.
```

If the provider's log is unavailable, the safe action is to **mark the
entry `status='resolved'`** and reconcile the local payment directly
via the payment-incident runbook. Replaying with a constructed body
**fails the HMAC check**, which is the correct behaviour.

## Step 4: Mark the entry

Once the underlying issue is fixed (or the entry is no longer
actionable), mark it resolved:

```sql
UPDATE failed_webhook
SET status = 'resolved',
    resolved_at = now(),
    resolution_note = 'replayed after provider recovered at <timestamp>'
WHERE failed_webhook_id = '<id>';
```

The `resolved_by` column should carry the operator's identifier, not
"system".

## Step 5: Verify the side-effects

After replay, confirm:

1. The corresponding `provider_event` row has `status='processed'`.
2. The corresponding `payment` row has the expected status (`captured`,
   `failed`, etc.).
3. The corresponding `order` row, if any, has its status updated.

If the replay did not produce all three, the payment-incident runbook
applies.

## Don't do this

* **Do not delete the `failed_webhook` row.** The row is the audit
  trail for the original failure. Set `status='resolved'` and keep the
  row.
* **Do not bypass the HMAC check.** The whole security model rests on
  the gateway rejecting unsigned or tampered webhooks. The replay
  path is *not* a back door.
* **Do not retry indefinitely.** The worker already retries with
  exponential backoff. If five attempts have failed, a sixth manual
  attempt will fail too. Diagnose first.

## Escalation

If the diagnosis is unclear, escalate to the payments on-call lead.
Include:

* The `failed_webhook_id`
* The `last_error` text
* A redacted excerpt of the original raw body (the signature is fine
  to share internally; the key prefix is not).
