# Reconciliation mismatch runbook

## Symptoms

* `RECONCILIATION_MISMATCH` alert with `severity=warning`.
* Local payment is in `status = unknown` but the provider returns
  neither `paid` nor `failed` (e.g. `created`, `attempted`, `expired`).

## Triage

1. **Read the alert payload.** It includes `payment_id`,
   `provider_order_id`, and `provider_status`.
2. **Fetch the provider order directly:**

   ```bash
   curl -u "${RAZORPAY_KEY_ID}:${RAZORPAY_KEY_SECRET}" \
        "https://api.razorpay.com/v1/orders/${PROVIDER_ORDER_ID}"
   ```

   The `status` field is the source of truth. Common values:

   - `created` — buyer hasn't paid yet. The right action is to wait.
   - `attempted` — buyer tried to pay, payment is still being
     processed. The right action is to wait.
   - `expired` — buyer took too long. Refund or re-checkout.
   - `paid` — webhook is missing; this is a delivery problem, not a
     reconciliation problem.

3. **Check the local payment:**

   ```sql
   SELECT payment_id, status, provider_order_id, created_at, updated_at
   FROM payment
   WHERE payment_id = '...';
   ```

## Mitigation

### Order is `expired` on the provider

```sql
UPDATE payment
SET status = 'failed', updated_at = NOW()
WHERE payment_id = '...' AND status = 'unknown';
```

The order has to be re-created from the original checkout if the buyer
still wants to pay.

### Order is `paid` on the provider but webhook never delivered

Re-fetch the payment and verify it:

```bash
DATABASE_URL=... .venv/bin/python -c "
from apps.api.db import get_session_factory
from services.payments.provider import get_payment_provider
from apps.api.config import get_settings
from services.payments.service import PaymentService
session = get_session_factory()()
provider = get_payment_provider(get_settings().payment_provider_config())
svc = PaymentService(provider=provider)
print(svc.verify_payment(session, payment_id='...', provider_payment_id='...'))
"
```

### Order is `created` or `attempted`

This is normal mid-checkout. The worker will pick it up on the next
`poll_unknown_payments` tick. If the alert has been firing for more
than an hour, the buyer likely abandoned — consider expiring the
local payment to release any held inventory:

```sql
UPDATE payment
SET status = 'failed', updated_at = NOW()
WHERE payment_id = '...' AND status = 'unknown';
```

## Root cause

* **Checkout TTL is too long.** Buyers abandon and the local payment
  sits in `unknown` for the full TTL. Reduce the TTL in
  `apps/api/config.py`.
* **Webhook delivery is broken.** The order is `paid` on the provider
  but the webhook never arrived. Run
  [`webhook-replay.md`](webhook-replay.md).
* **Provider has its own intermediate state.** Some providers return
  `attempted` between `created` and `paid`. The worker should treat
  that as "wait one more tick" rather than "mismatch".

## Postmortem checklist

- [ ] How many payments were stuck in `unknown` at peak?
- [ ] What is the average `updated_at` lag for `unknown` payments?
- [ ] Are the order's `provider_status` values consistent with the
      provider's documented state machine?
- [ ] Edit this runbook with anything that surprised you.
