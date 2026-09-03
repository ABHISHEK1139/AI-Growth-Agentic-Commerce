# Remaining gaps and implementation plan

## Target outcome

Deliver one trustworthy end-to-end Track 01 journey:

```text
Merchant catalog -> AI buyer search -> verified price and inventory
-> Razorpay test payment -> signed webhook -> confirmed order
-> audit trail -> measured upsell/campaign outcome
```

## What already exists

- Amazon product dataset ingestion and normalized catalog artifacts.
- Dataset USD prices converted to INR at the fixed demo rate of $1 = INR 100.
- Catalog search, checkout creation, inventory reservation, frozen price hash,
  policy evaluation, authorization, payment creation, and audit events.
- Razorpay test-mode order creation and direct order verification.
- Groq-backed shopper intent extraction with deterministic tool boundaries.
- Agent-readable catalog/tool surface, security scopes, idempotency, and a
  Next.js shopper/merchant interface.
- Cross-sell/recommendation and campaign service foundations.

## Gaps to close

| Area | Current state | Required work |
| --- | --- | --- |
| Prices and inventory | Historical dataset prices and a fixed demo exchange rate | Use merchant-owned INR prices, live inventory, versioned imports, and reconciliation |
| Payment lifecycle | A Razorpay test-mode order is created and fetched | Complete browser payment, signed webhook confirmation, reconciliation, refunds, and failure recovery |
| Infrastructure | Local/in-memory tests; Docker/PostgreSQL tests may be skipped | Deploy API, web, worker, PostgreSQL, Redis, migrations, health checks, and CI integration tests |
| AI buyer | Live intent extraction and bounded tools | Multi-turn reliability, fallback UX, quality evaluation, cost and latency controls |
| Catalog API | Local agent surface and fixture-based coverage | Stable public API/MCP documentation and an independently running external buyer client |
| Upsell/campaigns | Rules/services are present | Event capture, A/B experiments, attribution, campaign approval lifecycle, and revenue reporting |
| Operations | Broad tests and audit records | Alerting, dashboards, backups, replay/runbooks, secret management, and load testing |

## Phase 0 - Secure and stabilize

1. Rotate all Groq and Razorpay credentials that may have appeared in local or
   shared output.
2. Keep real secrets out of the repository and use `.env.example` only for
   placeholders.
3. Add a deployed secret-manager integration.
4. Redact request headers and secrets from logs, error responses, and test
   tracebacks.
5. Replace the hard-coded Windows path in the live test with a path calculated
   from `__file__`.
6. Make CI run formatting, lint, unit tests, and focused integration tests.

Acceptance criteria:

- A clean clone runs with fake/mock providers.
- Live services require explicit opt-in.
- A provider error cannot disclose credentials.

## Phase 1 - Define the production commerce contract

Make merchant data the source of truth. Retain the dataset only as demo data.

Required entities:

- Merchant
- CatalogSource
- CatalogImport
- CatalogVersion
- Product
- Offer
- InventorySnapshot
- PriceSnapshot

Every offer must retain merchant SKU, source/version, currency, integer
minor-unit price, inventory timestamp, validity window, and import provenance.

Rules:

- Checkout uses a server-created, frozen PriceSnapshot.
- The client never supplies a final charge amount.
- Price/inventory changes invalidate incomplete checkouts where required.
- Dataset offers are labelled as demo data; merchant offers are labelled by
  their actual import source.

Acceptance criteria:

- Every payment can be traced to an exact catalog import and offer version.
- A stale offer cannot be charged without a new server-side validation.

## Phase 2 - Merchant catalog and inventory sync

### 2.1 CSV import

Implement a merchant CSV import first. Required columns:

```text
sku,title,description,price_minor,currency,inventory,status,image_url,category
```

Create:

- Upload, validation, preview, publish, rollback endpoints.
- A staging table and row-level error report.
- Atomic publish into a new CatalogVersion.
- Import/audit report visible in the merchant UI.

Suggested routes:

```text
POST /api/v1/merchant/catalog/imports
GET  /api/v1/merchant/catalog/imports/{import_id}
POST /api/v1/merchant/catalog/imports/{import_id}/validate
POST /api/v1/merchant/catalog/imports/{import_id}/publish
POST /api/v1/merchant/catalog/imports/{import_id}/rollback
```

### 2.2 Connectors

Use a connector protocol for pull and webhook sync, then implement it in this
order:

1. CSV/manual import
2. Generic REST commerce API
3. Shopify
4. WooCommerce
5. Incremental price/inventory webhooks

Acceptance criteria:

- Invalid rows never partially publish a catalog.
- Replaying an import is idempotent.
- Inventory and price changes are auditable and versioned.

## Phase 3 - Complete the real checkout lifecycle

Required journey:

```text
Product page -> cart -> checkout confirmation -> server price freeze
-> Razorpay order -> Razorpay browser checkout -> payment
-> signed webhook -> confirmed order -> shopper and merchant status
```

Implement:

- Browser checkout launch and return/pending state.
- Raw-body Razorpay webhook HMAC validation.
- Provider-event idempotency and out-of-order event handling.
- Worker retry and dead-letter/replay path for transient webhook failures.
- Reconciliation job for pending local payments versus Razorpay orders.
- Refund flow and audit events.

Acceptance criteria:

- A browser test-mode payment creates exactly one confirmed order.
- Duplicate webhooks never duplicate a payment/order.
- Invalid signatures are rejected.
- Pending payments are reconciled or surfaced for intervention.

## Phase 4 - Deploy the real stack

Run the intended architecture in staging:

```text
Next.js web + FastAPI API + worker + PostgreSQL + Redis + object storage
```

Deliver:

- Docker Compose staging environment.
- Alembic migration execution against PostgreSQL.
- Redis-backed jobs, rate limits, idempotency support, and retries.
- Readiness checks for database, Redis, migration version, worker heartbeat,
  and active catalog version.
- Structured logs containing correlation IDs but never credentials.
- Backup and restore procedure.

Acceptance criteria:

- Compose, migration, and PostgreSQL integration tests run in CI rather than
  skip.
- A staging URL completes the browser payment lifecycle.

## Phase 5 - Improve the AI buyer

The model may interpret intent and select bounded tools; it must never own
money, inventory, policy, or final authorization.

Implement:

- Multi-turn product clarification.
- Search, comparison, delivery/return explanation, and accessory suggestion.
- Explicit confirmation before checkout/payment actions.
- Strict tool schemas and response validation.
- Maximum tool-call/time budget and graceful provider-failure UX.
- Per-run audit records, model latency, and cost telemetry.
- Evaluation suite of real shopping prompts, including adversarial prompts.

Acceptance criteria:

- An AI buyer completes a realistic deployed purchase without bypassing the
  deterministic commerce controls.
- Quality, latency, and cost are measured from repeatable evaluations.

## Phase 6 - External agent-readability

Publish a stable integration package:

- OpenAPI specification.
- Capability document and MCP/tool schemas.
- API-key/OAuth onboarding.
- Sandbox merchant/buyer accounts.
- Versioning and compatibility policy.
- Separate external buyer client repository or process.

Acceptance criteria:

- An independent buyer client discovers capabilities, searches, checks out,
  confirms, and observes order status using only public interfaces.

## Phase 7 - Upsell/cross-sell measurement

Capture these events:

```text
product_viewed, search_performed, recommendation_shown,
recommendation_clicked, cart_updated, checkout_started,
payment_completed, order_confirmed, refund_issued
```

Build:

- Recommendation impression IDs and order attribution.
- Merchant dashboard metrics: attach rate, AOV, conversion, attributed revenue,
  and refund rate.
- Control versus recommendation experiment assignments.

Acceptance criteria:

- The dashboard proves whether a recommendation improved AOV or conversion.

## Phase 8 - Campaign orchestration

Add campaign lifecycle and safety controls:

```text
draft -> review -> approved -> active -> paused -> completed
```

Implement budgets, margin/inventory limits, start/end dates, targeting,
merchant approval, audit records, performance metrics, and automatic safety
pauses. The AI can propose campaigns; deterministic policy and merchant approval
must control publication.

Acceptance criteria:

- A merchant can launch/pause an audited campaign safely.
- Campaign performance is visible and attributable.

## Phase 9 - Production operations

Add:

- CI for lint, unit, PostgreSQL/Redis integration, browser E2E, and security.
- Dependency/container image scanning.
- Backup/restore drills.
- Error tracking and alerts for webhook failures, reconciliation mismatches,
  worker backlog, failed imports, provider errors, and authorization anomalies.
- Checkout/webhook concurrency load tests.
- Payment incident and webhook-replay runbooks.

Acceptance criteria:

- Failures are diagnosable and recoverable without database edits.
- Evidence exists for reliability under concurrent checkout traffic.

## Recommended implementation order

1. Security cleanup and CI baseline.
2. Docker/PostgreSQL/Redis staging stack.
3. CSV merchant catalog import with real INR prices.
4. Browser Razorpay test payment, webhook, and confirmed order.
5. External buyer API/MCP demonstration.
6. Recommendation attribution dashboard.
7. Campaign lifecycle and safety controls.
8. Shopify/WooCommerce connectors and operational hardening.

## Next milestone

The highest-value next milestone is a deployed staging demo where a
merchant-imported INR product is found by an AI buyer, paid through Razorpay test
mode, confirmed through a signed webhook, and visible in the merchant dashboard
with its full audit trail.
