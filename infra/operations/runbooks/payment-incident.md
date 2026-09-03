# Payment incident runbook

## Symptoms

* `PAYMENT_INCIDENT` alert, or
* Three or more `PROVIDER_ERROR` alerts within 10 minutes, or
* Merchant reports "my buyer's payment keeps failing".

## Triage

1. **Read the alert payload** for `provider` and `error_code`. Common
   patterns:
   - `502 / SERVICE_UNAVAILABLE` → provider is down; route to the
     provider status page.
   - `401 / UNAUTHORIZED` → a key has rotated. Verify `RAZORPAY_KEY_ID`
     and `RAZORPAY_KEY_SECRET` are current.
   - `429 / RATE_LIMITED` → we are over the provider's per-second cap.
2. **Check the provider status page.** If the provider is reporting an
   incident, the right action is to wait; do not retry.
3. **Check our own dashboards.** Are the failures concentrated in one
   merchant? One buyer? One currency? The pattern points at the cause.

## Mitigation

### Provider is down

There is no fix from our side. Mark the dead-letter entries as
"deferred until provider recovery" and let the worker resume on the
next run:

```sql
UPDATE failed_webhook
SET next_retry_at = NOW() + INTERVAL '15 minutes',
    last_error = 'Deferred — provider incident; will retry'
WHERE status = 'pending';
```

### Key has rotated

1. Verify the new key in your secret manager.
2. Roll the API process so it picks up the new key
   (`docker compose restart api`).
3. Replay the failed webhooks from the dead-letter table.

### Rate limit

Add a per-merchant rate limit on the checkout route. The
`apps/api/middleware.py` rate-limiter is the right place; the ceiling
should be below the provider's per-second cap with safety margin.

## Root cause

* **Test-mode vs live-mode drift.** The API was built with test keys
  but the worker was started with live keys (or vice versa). Audit the
  env vars.
* **Shared credentials across environments.** Two processes were using
  the same API key; one environment's load crossed the other's quota.
  Use per-environment keys.
* **Provider deprecation.** A payment method was deprecated and we
  didn't update the route.

## Postmortem checklist

- [ ] How long were payments failing? (compare alert timestamps)
- [ ] How many buyers were affected? (count from `payment` table where
      `status = 'failed'`)
- [ ] Was the provider's status page already reporting the incident?
- [ ] Edit this runbook with anything that surprised you.
