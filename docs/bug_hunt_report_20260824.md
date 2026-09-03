# AgentPay Bug Hunt Report — 2026-08-24

Scope: full static trace of the money-critical paths (checkout → authorization → payment → webhook → order), inventory concurrency, agent surface, connectors, campaigns, recommendations, auth/token layer, middleware, and catalog ingestion.

Severity legend: **CRITICAL** = can lose/duplicate money or break a hard invariant; **HIGH** = exploitable correctness/security defect; **MEDIUM** = state-machine/audit/data-integrity defect; **LOW** = robustness issue.

---

## CRITICAL

### C1. `verify_payment` accepts an amount-mismatched HMAC verification path (double-charge / wrong-amount risk)
File: `services/payments/service.py` (`verify_payment`), `apps/api/routers/razorpay_checkout.py` (`verify_razorpay_payment`).

The router verifies `HMAC(order_id|payment_id)` against the key secret, then calls `verify_payment`. Inside `verify_payment`, the provider-status fallback path checks `prov_payment.amount_minor == payment.amount_minor`, but the **signature path does not check amount at all**. The signature only covers `order_id|payment_id` — it proves nothing about the amount. Consequences:

- If a payment row was created for checkout A at ₹64,999 but the provider order was somehow reused/recreated at a different amount, a valid Razorpay signature still marks it verified.
- More importantly, the *fake* provider's `fetch_order` returns `status="paid"` for **any unknown order id** when behavior is `"success"` — so in any deployment running the fake provider, `verify_payment` succeeds via the fallback path with `amount_minor=5000000` hardcoded... except the fake's fallback payment also returns `amount_minor=5000000`, which will not match arbitrary checkout totals. The real hole is that `FakePaymentProvider.fetch_order` reports **any unknown order as paid**, meaning a caller who knows/guesses a `provider_order_id` can drive `verify_payment` to success without any real capture. This turns "verification" into "trust the client-supplied id".

Reproduction sketch: create payment → call `/api/v1/payments/razorpay/verify-signature` with a fabricated `razorpay_order_id` equal to the stored one and any `razorpay_payment_id`, computing the HMAC yourself is required on the razorpay path — but on the internal `PaymentService.verify_payment` path (webhook processor or tests) the fake provider's unconditional `paid` status satisfies the gate.

Fix: bind the signature payload to include the amount (Razorpay's own contract is `order_id|payment_id`; add a server-side re-fetch of the order and compare `amount_minor` and `status=="paid"` from the provider before trusting either path). Also make `FakePaymentProvider.fetch_order/fetch_payment` return unpaid for unknown ids unless explicitly staged.

### C2. Webhook deduplication race allows duplicate processing of the same event
File: `services/payments/webhooks.py`.

Dedup queries `ProviderEvent` by `(provider_event_id) OR (raw_body_hash AND signature)`, then inserts inside `begin_nested()`. The nested savepoint flush catches an IntegrityError — good — but the **query+insert pair is not serialized**: two concurrent deliveries both pass the SELECT (neither row committed yet), both insert; only one savepoint survives. That part is handled. The actual bug: after the savepoint exception, the code returns `already_processed` **without checking whether the winning insert has actually been processed** — fine — but the deeper problem is that `process_webhook` performs its state transitions *after* inserting the event row in the same transaction, while the event row is marked `status="processed"` **before** processing completes. If processing raises (e.g., unmatched payment → DomainError), the event stays `unmatched` only because the whole transaction rolls back — acceptable — but if the caller commits partially (the API layer commits on success only), a crash between flush and verify leaves the event recorded as processed with no side effects applied, and every retry is answered `already_processed`. Result: a captured-payment webhook can be permanently swallowed.

Fix: mark the event `processed` only after successful handling (update status at the end), or record `received` first and transition to `processed` post-handling.

### C3. Idempotency replay bypasses all payment gates
File: `services/payments/idempotency.py` + `services/payments/service.py` step 5.

On replay (`is_replay=True`), `create_payment` returns the cached body immediately. But the request hash covers only `{checkout_id, authorization_id, amount_minor}` — it does **not** cover the buyer/merchant identity beyond the idempotency record's actor columns. Since records are keyed by `(actor_type, actor_id, endpoint, key)`, cross-buyer reuse is blocked. However: the cached response is returned even if the original payment has since **failed, expired, or been superseded** — a client retrying hours later gets `200 OK` with a stale `payment_id` in state `pending`, and may present it as proof of initiation. Worse, if the checkout was cancelled between attempts, the replay still answers success. Replay must re-check checkout/payment liveness before serving cache.

### C4. Authorization consumption is not atomic with payment creation (replay window)
File: `services/payments/service.py` steps 4–8.

`revalidate_for_payment` reads the authorization and later sets `auth.status = "consumed"`. The checkout row is locked `with_for_update()`, but the **authorization row is not locked**. Two concurrent `create_payment` calls with different idempotency keys (or no key) for the same checkout+authorization:

1. Both lock the same checkout row → serialized by the DB. ✅ (this saves the common case)
2. BUT two payments for the *same authorization across two checkouts* are impossible (authorization.checkout_id is unique), so the checkout lock mostly covers it.
3. The residual race: `create_payment` without an idempotency key creates a second `Payment` row for the same checkout after the first attempt failed at the provider (step 7 failure path transitions the *payment* to failed but leaves the authorization consumed). A retry then hits `AUTHORIZATION_ALREADY_CONSUMED` even though no charge was made — the buyer is bricked out of completing a legitimate purchase after a transient provider error. Money is not lost, but the purchase is unrecoverable without manual intervention.

Fix: consume the authorization only after provider order creation succeeds, or support explicit re-issue on provider failure.

---

## HIGH

### H1. `razorpay_checkout.create_razorpay_order` trusts client-supplied `buyer_id`/`merchant_id`
File: `apps/api/routers/razorpay_checkout.py`.

```python
merchant_id = principal.merchant_id or request.merchant_id or settings.default_merchant_id
buyer_id = principal.buyer_id or request.buyer_id or "buyer_demo"
```

A token principal whose `principal.buyer_id` is set ignores request fields (good), but a session principal with role MERCHANT_* has `buyer_id=None`, so `request.buyer_id` wins — any authenticated merchant-side caller can create a checkout and payment **as any buyer string**, charging through the pipeline under an arbitrary buyer identity. Also auto-creates Merchant/Buyer rows on demand, letting an attacker mint tenant rows.

Fix: require `Role.BUYER` (or a buyer-bound token) on this route and drop `request.buyer_id` entirely.

### H2. Same route fabricates offers/inventory with 1000 units when no offer matches
Same file. If no active offer matches `request.amount`, the endpoint silently creates a new Offer + Inventory(1000) and proceeds to charge. A client can therefore transact at any amount ≥ ₹1 against a synthetic product, bypassing the published catalog and policy category allowlists (category hardcoded `electronics`). This defeats the "server-side verified offer pricing" property the route claims.

### H3. `idempotency_key=request.receipt` on the razorpay create-order route
The receipt is client-controlled and optional. Two different logical purchases sharing a receipt string (or a client omitting it → `None`) get identical/no idempotency semantics. Combined with H2, retries with the same receipt replay the cached response even if the underlying checkout differs. Use the generated `checkout_id` as the idempotency key instead.

### H4. Fake provider treats unknown orders as paid (test double leaking into prod posture)
File: `services/payments/provider.py`. `get_payment_provider` falls back to `_FAKE_PROVIDER_INSTANCE` whenever config is missing/unconfigured — including in deployments where `PAYMENT_PROVIDER=razorpay` but credentials are blank. The service then silently charges nothing yet reports success-shaped states (`fetch_order` → `paid`). Fail-closed (raise on unconfigured razorpay) is safer than silently degrading to the fake.

### H5. Campaign approve/activate skips policy re-check and PROPOSED→ACTIVE shortcut
Files: `services/campaigns/service.py`, `apps/api/routers/campaigns.py`.
- `activate_campaign` accepts `PROPOSED` directly, skipping approval entirely — the "Merchant authorization gate" is bypassable by calling activate.
- Policy is evaluated once at propose time; approve/activate never re-evaluates, so a policy change between propose and activate is ignored.
- Repository is process-memory + Redis with **no transactionality**: concurrent approve/activate lose updates (last write wins), and `get()` falls back to scanning memory ignoring Redis-only rows written by another worker.

### H6. Connector registration is SSRF-by-design with no URL validation
File: `services/connectors/generic_rest.py`, `ecommerce_platform.py`, router `connectors.py`. `store_url`/`base_url` are fetched server-side with no `is_safe_public_url` equivalent — a merchant-operator credential can point the connector at `http://169.254.169.254/...` or internal services and read results through sync errors/logs. Also `push_order` posts order payloads to arbitrary URLs.

### H7. Generic REST price heuristic corrupts prices
File: `services/connectors/generic_rest.py`:
```python
if isinstance(raw_price, float) or (isinstance(raw_price, int) and raw_price < 10000):
    price_minor = int(raw_price * 100)
```
An integer minor-unit price below 10000 paise (₹99.99) is multiplied by 100 again → 100× overcharge. Same heuristic duplicated in `feed.py`. Any catalog containing legitimately cheap items gets wrong prices, which then flow into checkouts.

### H8. Negotiation result is advisory but priced nowhere — agreed price never binds
File: `services/negotiation/engine.py`, router `agent.py`. `evaluate_bid` can return `accepted` with `agreed_price_minor < list_price`, but nothing persists a discounted offer or version bump; the subsequent checkout still charges list price. Not a money-loss bug, but a contract violation: the agent/buyer was told "accepted at X" and is charged Y. Either persist a bound negotiated offer (with new offer_version + audit) or refuse negotiation until such binding exists.

---

## MEDIUM

### M1. Checkout expiry transition uses wrong source state mapping
`transitions.py`: `EXPIRE_CHECKOUT` rules exist only from `CHECKOUT_CREATED` and `AUTHORIZED`. A checkout sitting in `AUTHORIZATION_PENDING` past expiry cannot be expired by the engine (`normalize_source_status("REQUIRES_APPROVAL")→AUTHORIZATION_PENDING` has no EXPIRE_CHECKOUT rule); `create_payment`'s expiry branch will raise `ILLEGAL_TRANSITION` instead of cleanly expiring. Stale pending checkouts leak inventory holds indefinitely (no reaper visible).

### M2. `fail_payment` releases inventory even when the checkout is already completed
`services/payments/service.py::fail_payment` unconditionally releases stock and transitions the checkout FAIL_PAYMENT. If invoked (e.g., a late `payment.failed` webhook) after a *different* payment already completed the checkout, `release_stock` finds the reservation `committed` and no-ops (ok), but the checkout transition attempt raises ILLEGAL_TRANSITION after partial mutation ordering — and the audit trail records PAYMENT_FAILED for a payment whose sibling succeeded, which is correct, but the release path should be gated on reservation status rather than attempted blindly.

### M3. `PaymentService.get_payment_by_id` writes an audit event on every read
Every GET emits `PAYMENT_STATUS_CHECKED` into the append-only ledger. Polling clients (frontend refresh loops) flood the tamper-evident ledger, inflating storage and burying real events. Reads should not be ledger events, or should be sampled/aggregated.

### M4. Webhook `order.paid`/`authorized` accepted with looser provider checks than capture
In `webhooks.py`, the order-fallback accepts `status in ("paid","attempted","authorized")` — `attempted` means a payment exists but is **not** captured, yet the handler proceeds to `verify_payment` and confirms the order. An `order.attempted`-style callback (or a provider quirk) finalizes an order for an uncaptured payment.

### M5. `AuthorizationService.request_authorization` returns existing consumed/expired auths' schema paths inconsistently
Existing-auth short-circuit returns the stored record for `approved|pending` **without re-validating expiry** — an approved-but-expired authorization is handed back as if valid; the expiry only surfaces later at `revalidate_for_payment`. Confusing but not exploitable (payment gate still checks).

### M6. Tenant scoping gaps in ad-hoc queries
Several services bypass `TenantScopedRepository` with raw `session.query(...)` filtered only by what the caller remembered:
- `policy/service.py` loads Offer/Product/Inventory by id with no merchant predicate (offer ids are globally unique PKs so impact is limited, but a future composite-key change breaks isolation silently).
- `recommendations/service.py` deliberately falls back to "check without merchant_id filter for demo cross-merchant browsing" — an explicit cross-tenant product read reachable from a buyer-scoped endpoint.
- `campaigns` repository keyed by merchant but `get()` scans global memory when Redis is down.

### M7. `MerchantRulesRepository.upsert_rules` ignores its positional `merchant_id` for scoping
`upsert_rules(merchant_id=...)` writes to `target_merchant_id` but `get_by_merchant_id(target_merchant_id)` runs through the *repository scope*, which may be a different merchant — a platform-admin `acting_on` flow can upsert into merchant X while reading/checking existence in merchant Y, creating duplicate-rule confusion. Also `PolicyDecisionRepository.merchant_column = "checkout_id"` is a lie to the base class (no merchant column), defeating the structural guarantee for that table.

### M8. Rate-limit table misses live routes
`ratelimit.py` protects `POST /api/v1/payments` etc., but the razorpay routes (`POST /api/create-order`, `/api/v1/payments/razorpay/create-order`, `/api/verify-payment`) fall to the default 120/min — the money-moving web-checkout path is rate-limited more loosely than the internal one. `/api/v1/agent/tools/execute` and `/api/v1/agent/converse` (LLM cost amplification) have no rule at all.

### M9. Explore endpoint research step fires outbound search per request
`explore.py` gates web search on `settings.search_provider != "null"`, but `ResearchWorker.execute_product_research`'s DuckDuckGo scraper is unconditional when enabled, with no per-user budget beyond the route limit (20/min). Combined with intent-degradation, every explore call can trigger network fetches — cost amplification surface.

### M10. Catalog import: duplicate product_ids silently overwrite
`catalog/service.py` inserts `Product(product_id=p_data.get("product_id") or ...)` with no upsert guard; a JSONL containing the same product_id twice (or an id colliding with another version's rows — PK is global, not per-version) raises IntegrityError mid-import, leaving the import_run `running` and the transaction half-built (caller-dependent rollback). Re-runs after checksum change collide with prior versions' products since `product_id` is the primary key across versions.

---

## LOW

### L1. `compute_price_hash` dict path drops unknown keys silently — snapshot drift between creation (includes `product_id` in stored dict) and revalidation (hashes only PRICE_FACTORS) is safe today only because both sides filter; adding a factor requires touching three places.

### L2. `IdempotencyRecord.expires_at` default is 24h but acquire_lock resets TTL to 300s on retry — inconsistent windows documented nowhere.

### L3. `WebhookProcessor.process_webhook` default `provider_name="fake"` while the route never passes the resolved provider name — audit rows mislabel razorpay events as `fake`.

### L4. `explore.py` catches `Exception` around intent extraction, swallowing `DomainError`s the constraints module raises intentionally (e.g., unsupported currency) and downgrading them to "degraded" instead of surfacing the refusal.

### L5. `guard.py` Layer-2 fail-closed returns `is_safe=False` for transport errors — correct — but `assert_safe` in `loop.run_bounded_agent` is called *without* config, so agent `/converse` always runs heuristic-only regardless of configured remote guard (inconsistent with `/api/explore` which passes settings).

### L6. `orders.py` ownership re-check loops over page results — O(n) redundant but harmless; however `list_orders_for_buyer` count query is unbounded on large tenants (count(*) per request).

---

## Top fixes, prioritized

1. **C1/H4**: make `verify_payment` re-fetch provider order and compare amount+status on *both* paths; make fake provider fail-closed for unknown ids; raise instead of silently falling back to the fake when razorpay is selected but unconfigured.
2. **C2**: move `ProviderEvent.status="processed"` to after successful handling.
3. **C3**: re-validate checkout/payment liveness before serving idempotent replays.
4. **H1–H3**: strip client-supplied `buyer_id`/`merchant_id`/receipt-derived idempotency from the razorpay web-checkout route; remove synthetic offer creation.
5. **H6/H7**: apply the existing `is_safe_public_url` policy to connector base URLs; delete the `<10000 ⇒ major-units` price heuristic (require explicit `price_minor`).
6. **M1/M4**: add `AUTHORIZATION_PENDING → CHECKOUT_EXPIRED` rule; restrict webhook order-fallback statuses to `paid` only.
