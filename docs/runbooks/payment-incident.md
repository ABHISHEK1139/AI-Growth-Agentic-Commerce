# Payment Incident Runbook

**Severity:** P1
**On-call:** payments + on-call lead
**Alert source:** `reconciliation_mismatch`, `payment_incident`, `provider_error`

A payment incident is any case where the gateway, the provider, and
the local state disagree about the state of money. The most common
shapes are:

* A merchant reports a successful payment that the gateway does not
  show as `captured`.
* A buyer is charged by the provider but the gateway never created an
  order.
* A refund is reported by the provider but the gateway still shows
  the payment as `captured`.

This runbook covers the diagnosis and recovery path.

---

## First: don't make it worse

Before touching the database, **stop the worker** so the reconciliation
job cannot race your manual changes:

```bash
docker compose stop worker
```

If you cannot stop the worker (e.g. a shared staging environment),
make your changes atomic and accept that the worker may apply them
again; the actions below are idempotent.

## Step 1: Triangulate the state

You need three pieces of information: the local payment, the local
order, and the provider's view of the same payment. Each is reachable
through a different system.

### Local payment

```sql
SELECT payment_id, status, amount_minor, currency, provider,
       provider_order_id, provider_payment_id, created_at, verified_at
FROM payment
WHERE payment_id = '<id>' OR provider_payment_id = '<provider_id>';
```

### Local order

```sql
SELECT order_id, status, total_minor, currency, payment_id, confirmed_at
FROM "order"
WHERE payment_id = '<id>';
```

### Provider view

```bash
# Razorpay in test mode
curl -u "$RAZORPAY_KEY_ID:$RAZORPAY_KEY_SECRET" \
  https://api.razorpay.com/v1/payments/<provider_payment_id>
```

The provider returns its own `status`, `amount`, `captured`, and
`error_description` fields. These are the truth.

## Step 2: Classify the incident

| Local status | Provider status | Order status | Classification |
| ------------ | --------------- | ------------ | ------------- |
| `created`    | `captured`      | absent       | Webhook missed (likely DLQ) — see [webhook-replay](./webhook-replay.md) |
| `created`    | `captured`      | `pending`    | Same as above; the order was created but the payment never confirmed |
| `captured`   | `failed`        | any          | Provider-side reversal. Verify with the provider, then refund the buyer. |
| `failed`     | `captured`      | any          | Local record is wrong. **Do not** mark the order confirmed until the provider confirms the capture. |
| `captured`   | `captured`      | `confirmed`  | No incident. Close the alert. |
| `refunded`   | `refunded`      | any          | No incident. Close the alert. |
| any          | `captured` but local has no row | absent | The buyer paid but the order was never linked. Recover via the order-creation path. |

## Step 3: Recover

### Webhook missed (the most common case)

Follow the [webhook-replay runbook](./webhook-replay.md). The recovery
is to re-apply the original webhook payload, which drives the
`payment.captured` → `verify_payment` → order confirmation chain.

### Provider-side reversal

1. Verify with the provider that the reversal is legitimate.
2. If yes, mark the payment `refunded` via the standard refund flow:
   ```bash
   curl -X POST https://api.example.com/api/v1/payments/<id>/refund \
     -H "Authorization: Bearer <admin-token>" \
     -H "Content-Type: application/json" \
     -d '{"amount_minor": null, "reason": "Provider reversed the capture"}'
   ```
3. If the order was already `confirmed`, mark it `cancelled` and
   notify the merchant.

### Local state is wrong (DB was out of sync)

1. Take a backup **before** making changes (see [backup-restore](./backup-restore.md)).
2. Update the payment row to match the provider:
   ```sql
   UPDATE payment
   SET status = 'captured',
       verified_at = now(),
       provider_payment_id = '<provider_id>',
       updated_at = now()
   WHERE payment_id = '<id>';
   ```
3. If the corresponding order is absent, create it via the order
   service. **Do not** create the order directly in SQL: the inventory
   hold, the audit record, and the buyer's email all flow through the
   service.
4. If the order is `pending`, transition it to `confirmed` via the
   service.

### Buyer paid but no order exists

1. Find the buyer's `checkout_id` from the gateway's checkout list.
2. Re-run the checkout completion: the order will be re-created from
   the same checkout, and the existing payment will be attached.
3. If the checkout has been swept (TTL), recover it from the
   `failed_webhook` queue and replay; the order is then created from
   the checkout as part of the replay.

## Step 4: Notify

* The buyer must be told the outcome. The gateway sends the
  confirmation email; if the local order was created after the email
  was sent, the buyer's tracking page may show stale status. Update
  the order's `notified_at` so the next cron sweep does not resend.
* The merchant must be told the timeline. Their dashboard reads the
  same data the gateway shows, so a discrepancy is visible to them.

## Step 5: Postmortem

A payment incident always requires a postmortem. Required sections:

* Timeline (UTC, with the alert timestamps).
* Root cause — was it a webhook delivery failure, a worker crash, a
  provider outage, or a manual error?
* Customer impact — how many buyers, how much money, how long.
* Action items, each with an owner and a date.

Save the postmortem under `docs/postmortems/<YYYY-MM-DD>-<slug>.md`.
