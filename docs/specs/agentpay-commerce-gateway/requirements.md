# Requirements Document

## Introduction

AgentPay is a merchant-side AI commerce gateway. It makes an ordinary merchant machine-readable and safely transactable by AI buyers. A language model interprets intent and selects tools; a deterministic core owns prices, inventory, policy, authorization, and payment.

The thesis this system exists to demonstrate is not that it uses a large model. It is that **the model is useful but bounded**: it can interpret a request, compare validated offers, and coordinate tools, while the commerce core independently controls money, policy, state, inventory, authorization, and verification.

Success is a complete, audited, idempotent purchase loop against Razorpay test mode, driven by an independent external client that holds no privileged access.

### Scope of this document

This specification covers the remaining build. Task 1 (repository scaffolding, configuration, structured logging with redaction, correlation identifiers, health probes, architecture import contracts) is **already complete** with 82 passing tests, and its acceptance criteria are recorded here so they remain regression-protected rather than assumed.

Source material, treated as authoritative background:

- `agentpay_detailed_architecture_plan.txt` - base architecture, 30 sections
- `agentpay_dataset_pipeline_plan_v2.txt` - dataset and catalog pipeline
- `agentpay_v2_addendum_external_agent_commerce.txt` - external agent commerce addendum
- `datasets/` - six compressed source files

### Locked decisions

These are settled and are not reopened by this document.

| # | Decision |
|---|---|
| D-1 | Three source category pairs only (Electronics, Cell_Phones_and_Accessories, Appliances). No new dataset downloads. |
| D-2 | `kitchen_appliance` bucket removed; `phone_accessory` promoted to its own bucket; quotas rebalanced to 20,000. |
| D-3 | Modular monolith: FastAPI, PostgreSQL with pgvector, Redis, one worker, Next.js. No sixth service before Requirement 21 is met (external buyer contract suite). |
| D-4 | The fake payment provider is the default everywhere; Razorpay test mode is opt-in by configuration. |
| D-5 | Synthetic INR prices seeded from SHA-256 of `parent_asin`, carrying `pricing_source: synthetic_band_random`. |
| D-6 | Authorization is explicit human approval. No simulated cryptographic mandates. |
| D-7 | `price_hash` is SHA-256 over canonical JSON of the pricing tuple. |
| D-8 | The golden path runs at 100 products before any scaling work. |
| D-9 | No claim of implementing ACP, AP2, or MCP. `docs/protocol-scope.md` states the real position. |

## Glossary

| Term | Meaning |
|---|---|
| **Minor units** | Integer smallest currency denomination. Paise for INR. The only representation of money in this system. |
| **Offer** | The object an AI buyer selects. Binds a product to a merchant, price, availability, delivery, and a policy snapshot. |
| **Checkout** | An immutable snapshot of a selected offer plus server-computed totals. Cannot be altered by model output. |
| **price_hash** | SHA-256 over the canonical pricing tuple. The single value that gates authorization and payment creation. |
| **Authorization** | Explicit buyer approval bound to one buyer, merchant, checkout, amount ceiling, currency, category, expiry, and price_hash. |
| **Policy decision** | A persisted ALLOW / REQUIRE_APPROVAL / BLOCK outcome with a machine-readable reason code. |
| **Golden path** | Intent, discovery, offer, checkout, policy, authorization, payment, verification, order, audit. |
| **External buyer** | An unprivileged HTTP client using only the documented public agent API. No database access, no admin role, no special headers. |
| **Evidence** | Untrusted retrieved content carried with provenance, presented to the model as quoted data and never as instructions. |
| **Deterministic core** | Services that compute financial outcomes from explicit inputs and versioned rules, with no model in the decision path. |

---

## Requirements

### Requirement 1: Catalog ingestion from compressed source data

**User Story:** As a platform operator, I want the raw dataset transformed into reliable commerce data through a resumable pipeline, so that catalog quality is measured rather than asserted and every record traces back to its source.

#### Acceptance Criteria

1. WHEN the pipeline reads a source file THEN the system SHALL stream it as gzip in text mode one line at a time, and SHALL NOT decompress it to disk
2. THE SYSTEM SHALL NOT modify any file under the raw source directory at any point in its lifecycle
3. WHEN a source line contains malformed JSON THEN the system SHALL skip that line and continue, rather than aborting the run
4. WHEN a record lacks a parent identifier THEN the system SHALL reject the record before it enters candidate storage
5. WHEN a record title is absent, shorter than 8 characters, or longer than 300 characters THEN the system SHALL reject the record
6. WHEN a record has no usable image URL in any resolution THEN the system SHALL reject the record
7. WHEN a parent identifier has already been seen during the current run THEN the system SHALL reject the duplicate, including across source files
8. WHEN image data arrives as a list of per-image objects OR as parallel columnar lists THEN the system SHALL normalize both shapes to one internal representation
9. WHEN a details field arrives as a JSON-encoded string rather than an object THEN the system SHALL parse it, and IF parsing fails THEN the system SHALL substitute an empty object rather than raising
10. WHEN features or description arrives as a list, a bare string, or is absent THEN the system SHALL normalize all three cases to a list
11. THE SYSTEM SHALL assign each surviving record exactly one subcategory label from a fixed set, using an uncategorized fallback rather than discarding the record
12. THE SYSTEM SHALL compute a completeness score from 0 to 100 from title quality, feature presence, description presence, usable image presence, detail richness, rating volume, and rating quality
13. THE SYSTEM SHALL write candidate records in batches of 2,000 and SHALL NOT accumulate more than one batch in memory
14. THE SYSTEM SHALL retain each record complete original source JSON alongside its normalized fields
15. WHERE a debug line cap is configured THE SYSTEM SHALL stop reading each source file at that many lines, and WHERE the cap is unset THE SYSTEM SHALL read each file to completion
16. WHEN the pipeline is interrupted THEN the system SHALL resume from the last completed stage boundary without reprocessing earlier stages

---

### Requirement 2: Quota-based product selection and provenance

**User Story:** As a merchant administrator, I want a bounded, reproducible product selection with an honest account of what the classifier could and could not place, so that catalog numbers presented to anyone are measured facts.

#### Acceptance Criteria

1. THE SYSTEM SHALL select products per subcategory ordered by completeness score descending, then rating volume descending, limited to that subcategory quota
2. THE SYSTEM SHALL treat quotas as caps and SHALL NOT pad an under-filled subcategory with records from another subcategory
3. WHEN a subcategory yields fewer records than its quota THEN the system SHALL report the shortfall in the quality report
4. THE SYSTEM SHALL derive the product identifier deterministically from the source parent identifier, such that repeated runs produce identical identifiers
5. THE SYSTEM SHALL retain the original parent identifier as the external product identifier
6. THE SYSTEM SHALL write the complete verbatim source record for every selected product to a per-product provenance file
7. THE SYSTEM SHALL normalize currency to minor units, memory to GB, storage to GB, weight to grams, dimensions to millimetres, delivery to integer days, and booleans to true or false
8. THE SYSTEM SHALL carry the source dataset own price as reference metadata explicitly marked non-authoritative
9. WHEN resolving a product image THEN the system SHALL prefer highest available resolution, falling back through available alternatives
10. THE SYSTEM SHALL deduplicate image URLs within a product and SHALL produce no duplicate storage keys
11. THE SYSTEM SHALL NOT download any image during manifest generation

---

### Requirement 3: Deterministic synthetic offer generation

**User Story:** As a platform operator, I want prices and availability generated reproducibly from a seed, so that a demo can be rehearsed and re-run identically and the synthetic origin is never mistaken for scraped market data.

#### Acceptance Criteria

1. THE SYSTEM SHALL derive every generated price, delivery window, return period, and inventory count from a hash of the product parent identifier
2. WHEN the offer generation stage is run more than once over the same input THEN the system SHALL produce byte-identical output
3. THE SYSTEM SHALL draw each price from the configured band for that product subcategory
4. THE SYSTEM SHALL express every price in integer minor units
5. THE SYSTEM SHALL record the pricing source as synthetic on every generated offer
6. THE SYSTEM SHALL initialize reserved quantity to zero and offer version to one
7. THE SYSTEM SHALL surface the pricing source in the merchant dashboard so the synthetic origin is visible without reading the data files

---

### Requirement 4: Review linking scoped to the selected catalog

**User Story:** As a platform operator, I want reviews limited to products actually in the catalog, so that a source dataset of hundreds of millions of reviews never becomes a storage or runtime problem.

#### Acceptance Criteria

1. THE SYSTEM SHALL load the set of selected product identifiers before streaming any review file
2. WHEN a review parent identifier is absent from the selected set THEN the system SHALL discard it while streaming and SHALL NOT retain it
3. THE SYSTEM SHALL derive each review identifier deterministically so that re-running the stage creates no duplicate rows
4. THE SYSTEM SHALL index stored reviews by parent identifier for per-product lookup
5. THE SYSTEM SHALL NOT materialize the full source review set in memory or on disk at any point

---

### Requirement 5: Computed catalog health reporting

**User Story:** As a merchant administrator, I want catalog health figures computed from the actual import, so that every number shown anywhere is defensible.

#### Acceptance Criteria

1. THE SYSTEM SHALL compute total product count, per-subcategory counts, per-status counts, missing-description count, generated offer count, and linked review count from the produced artifacts
2. THE SYSTEM SHALL NOT accept, store, or display any hand-entered catalog statistic
3. WHEN the quality report is compared against the produced artifacts THEN every count SHALL equal the actual record count in the corresponding artifact
4. THE SYSTEM SHALL report the configured target alongside the achieved total so any shortfall is visible
5. THE SYSTEM SHALL serve the merchant-facing catalog health view from computed import data, not from a static file edited by hand

---

### Requirement 6: Catalog versioning with atomic publish

**User Story:** As a merchant administrator, I want to review a draft catalog before it goes live and publish it atomically, so that buyers never see a half-imported catalog and in-flight checkouts are unaffected.

#### Acceptance Criteria

1. WHEN an import runs THEN the system SHALL create a draft catalog version rather than modifying the published one
2. WHEN validation finds a record that violates a data quality rule THEN the system SHALL flag it for review and SHALL NOT silently discard it
3. WHEN a catalog version is published THEN the system SHALL flip its status and supersede the previous version within a single database transaction
4. IF a failure occurs partway through publication THEN the system SHALL leave the previously published version active and unchanged
5. WHEN a catalog is published while a checkout exists THEN that checkout SHALL continue to resolve against its own captured offer snapshot
6. THE SYSTEM SHALL record source name, checksum, schema version, licence note, and timing for every import run
7. WHEN the same source is imported twice THEN the system SHALL produce the same result without duplicating records
8. THE SYSTEM SHALL make every seed script safe to run repeatedly

---

### Requirement 7: Deterministic offer discovery and ranking

**User Story:** As an AI buyer, I want a bounded set of offers that genuinely satisfy my stated constraints, so that I reason over validated commerce facts instead of inventing them.

#### Acceptance Criteria

1. THE SYSTEM SHALL filter candidate offers in the database by category, price ceiling, minimum memory, minimum storage, maximum delivery days, and positive available inventory before any ranking occurs
2. THE SYSTEM SHALL rank the filtered set on price fit, specification fit, delivery fit, return policy, and merchant-defined ranking
3. THE SYSTEM SHALL return no more than the configured maximum number of results
4. THE SYSTEM SHALL NOT return an offer that is out of stock, expired, or not in an active status
5. THE SYSTEM SHALL return only the fields required for reasoning, together with offer expiry and evidence references
6. WHEN a search response is produced THEN it SHALL validate against its published schema
7. THE SYSTEM SHALL NOT permit any model output to determine price, inventory, delivery estimate, or eligibility in a search result
8. WHEN a buyer asks for a laptop at or below INR 70,000 with at least 16 GB memory and delivery within 3 days THEN every returned offer SHALL satisfy all three constraints

---

### Requirement 8: Explicit state transitions

**User Story:** As a platform operator, I want every commerce state change to pass through one validated function, so that no controller, tool, or agent endpoint can move money-adjacent state directly.

#### Acceptance Criteria

1. THE SYSTEM SHALL route every aggregate state change through a single transition function
2. THE SYSTEM SHALL NOT permit any controller, tool, or agent endpoint to assign a status field directly
3. WHEN a transition is requested THEN the system SHALL verify current state, event permissibility, required fields, hash consistency, authorization validity, expiry, and actor permission before applying it
4. IF a requested transition is not permitted from the current state THEN the system SHALL reject it with a deterministic error code and SHALL leave state unchanged
5. WHEN a transition is applied THEN the system SHALL persist the new state and its audit event within a single database transaction
6. IF that transaction fails THEN the system SHALL leave neither the state change nor the audit event persisted
7. WHEN an aggregate is in a terminal state THEN the system SHALL reject any further finalization attempt
8. THE SYSTEM SHALL confirm an order exactly once, however many verification signals arrive

---

### Requirement 9: Complete audit ledger

**User Story:** As a merchant operator, I want every consequential action recorded with correlation identifiers, so that any transaction can be reconstructed and explained after the fact.

#### Acceptance Criteria

1. THE SYSTEM SHALL emit an audit event for each defined event type in its vocabulary, covering request receipt, prompt safety, intent extraction, catalog search, offer return, offer selection, offer revalidation, checkout creation, policy evaluation, authorization request, grant and rejection, payment creation, status check, verification and failure, order confirmation, price and inventory change detection, idempotent replay, research, and blocked tool calls
2. THE SYSTEM SHALL record actor type, actor identifier, aggregate type, aggregate identifier, input hash, decision, reason code, policy version, model version, and amount where relevant
3. THE SYSTEM SHALL propagate trace, request, agent run, checkout, payment, and provider event identifiers across HTTP requests and worker tasks
4. WHEN a state change occurs THEN the system SHALL record exactly one corresponding audit event
5. THE SYSTEM SHALL NOT include any secret, credential, or token value in an audit event
6. WHEN an aggregate audit trail is requested THEN the system SHALL return its events in chronological order
7. THE SYSTEM SHALL make the rendered transaction timeline match the audit ledger exactly

---

### Requirement 10: Atomic inventory reservation

**User Story:** As a merchant, I want stock held atomically at checkout and released reliably, so that two competing AI buyers cannot both purchase the last unit and expired holds do not leak stock.

#### Acceptance Criteria

1. WHEN a checkout is created THEN the system SHALL reserve inventory using a single conditional update guarded by both available quantity and a version token
2. IF the conditional update affects zero rows THEN the system SHALL return an inventory-unavailable or version-conflict error and SHALL NOT create the checkout
3. WHEN two requests contend for the last available unit THEN exactly one SHALL succeed and the other SHALL receive a deterministic failure
4. THE SYSTEM SHALL NOT allow available or reserved quantity to become negative under any sequence of operations
5. WHEN a checkout expires, is cancelled, or has its authorization rejected THEN the system SHALL release its reservation and emit the corresponding audit events
6. WHEN a release is attempted for an already-released reservation THEN the system SHALL treat it as a no-op
7. WHEN payment is verified THEN the system SHALL decrement both reserved and available quantity to commit the reservation
8. THE SYSTEM SHALL track reservation state per checkout so release and commit decisions are made from persisted state rather than inference

---

### Requirement 11: Checkout with price integrity

**User Story:** As a buyer, I want the amount I approve to be computed and frozen by the server, so that nothing between my approval and the charge can change what I pay.

#### Acceptance Criteria

1. WHEN a checkout is created THEN the system SHALL load the offer, reserve inventory, validate offer status and expiry, and compute subtotal, shipping, tax, and configured discounts server-side
2. THE SYSTEM SHALL represent every monetary value as an integer in minor units
3. THE SYSTEM SHALL NOT use binary floating-point for any monetary value
4. THE SYSTEM SHALL ignore any client-supplied amount and SHALL use only its own computed totals
5. THE SYSTEM SHALL apply only discounts that are configured, and SHALL NOT apply a discount proposed by a model
6. THE SYSTEM SHALL store an immutable price snapshot on the checkout
7. THE SYSTEM SHALL compute the price hash as SHA-256 over a canonical representation of offer identifier, offer version, unit price, quantity, shipping, tax, discount, currency, and checkout expiry
8. WHEN the same pricing inputs are hashed twice THEN the system SHALL produce the same hash
9. WHEN any single pricing input changes THEN the resulting hash SHALL differ
10. WHEN a checkout passes its expiry THEN the system SHALL prevent payment creation, mark it expired, and state that no charge was attempted
11. THE SYSTEM SHALL support checkout refresh and cancellation as explicit operations

---

### Requirement 12: Deterministic policy evaluation

**User Story:** As a buyer, I want spending boundaries enforced by explicit rules rather than model judgement, so that a persuasive prompt cannot authorize a purchase.

#### Acceptance Criteria

1. THE SYSTEM SHALL compute every policy decision from explicit inputs and a versioned rule set, with no model in the decision path
2. THE SYSTEM SHALL return exactly one of ALLOW, REQUIRE_APPROVAL, or BLOCK, together with a machine-readable reason code
3. WHEN the same inputs are evaluated repeatedly THEN the system SHALL return the same decision
4. THE SYSTEM SHALL evaluate buyer identity, merchant identity, category, total amount, currency, payment method, offer status, inventory status, authorization status, time validity, and merchant rules
5. WHEN an amount is within the maximum limit but above the automatic approval limit THEN the system SHALL return REQUIRE_APPROVAL with a reason of amount above automatic limit
6. WHEN a category or merchant is outside the approved set THEN the system SHALL return BLOCK with the corresponding reason code
7. THE SYSTEM SHALL persist every policy decision with its inputs hash and policy version
8. THE SYSTEM SHALL enforce merchant rules covering maximum discount, minimum order value, allowed payment methods, out-of-stock behaviour, and prohibited overrides
9. THE SYSTEM SHALL NOT permit a model to alter, bypass, or re-run a policy decision to obtain a different outcome

---

### Requirement 13: Authorization binding

**User Story:** As a buyer, I want my approval bound to one exact purchase, so that it cannot be reused, redirected, or applied after the terms change.

#### Acceptance Criteria

1. THE SYSTEM SHALL bind each authorization to buyer, merchant, checkout, amount ceiling, currency, category, expiry, and the checkout price hash
2. WHEN payment is attempted with an authorization bound to a different checkout THEN the system SHALL reject it with an authorization-checkout-mismatch error
3. WHEN an authorization has passed its expiry THEN the system SHALL reject its use
4. WHEN an authorization has already been consumed THEN the system SHALL reject any further use
5. IF the checkout current price hash differs from the hash recorded on the authorization THEN the system SHALL reject payment creation
6. THE SYSTEM SHALL re-validate the authorization immediately before any payment provider interaction
7. THE SYSTEM SHALL present the exact merchant, product, quantity, total amount, currency, delivery estimate, return policy, and approval expiry at approval time
8. THE SYSTEM SHALL NOT present a vague confirmation prompt in place of the exact amount and merchant
9. THE SYSTEM SHALL support approve, reject, and revoke as explicit operations
10. WHEN an authorization is granted or rejected THEN the system SHALL require that action to come from the buyer who owns it

---

### Requirement 14: Payment provider abstraction

**User Story:** As a developer, I want payments behind a provider-neutral interface with a controllable fake, so that the entire purchase loop and its failure modes are testable without credentials or network access.

#### Acceptance Criteria

1. THE SYSTEM SHALL define a provider interface covering order creation, payment fetch, order fetch, signature verification, and refund
2. THE SYSTEM SHALL provide a fake implementation satisfying that interface
3. THE SYSTEM SHALL default to the fake provider so that a clean clone runs the full golden path and test suite with no credentials
4. THE SYSTEM SHALL allow the fake to be driven into success, failure, timeout, delayed webhook, and invalid signature behaviours
5. THE SYSTEM SHALL allow a test to assert how many provider orders were created for a given checkout
6. THE SYSTEM SHALL subject the real adapter and the fake to the same shared contract test suite
7. THE SYSTEM SHALL select the provider by configuration only
8. THE SYSTEM SHALL NOT permit the agent or any model output to invoke the payment provider directly

---

### Requirement 15: Payment creation

**User Story:** As a buyer, I want payment created only after every precondition is re-verified, so that a charge is never attempted against stale or mismatched terms.

#### Acceptance Criteria

1. WHEN payment creation is requested THEN the system SHALL validate checkout state, checkout expiry, authorization validity, amount, currency, and price hash before contacting the provider
2. IF any precondition fails THEN the system SHALL NOT contact the payment provider at all
3. THE SYSTEM SHALL recompute the amount server-side and SHALL NOT trust an amount supplied by a client
4. THE SYSTEM SHALL create an internal payment attempt record before contacting the provider
5. THE SYSTEM SHALL store the provider order identifier against the internal payment
6. THE SYSTEM SHALL return only browser-safe payment data to the client
7. THE SYSTEM SHALL NOT include the provider secret key in any response, log, audit event, or model prompt
8. THE SYSTEM SHALL keep all provider credentials server-side
9. WHEN payment is created THEN the system SHALL emit a payment-created audit event

---

### Requirement 16: Verified and deduplicated webhook processing

**User Story:** As a platform operator, I want provider callbacks cryptographically verified and replay-proof, so that a forged or repeated notification cannot confirm an order.

#### Acceptance Criteria

1. WHEN a webhook arrives THEN the system SHALL verify its signature against the raw request body using the configured webhook secret
2. IF the signature is invalid THEN the system SHALL reject the request with a deterministic error and SHALL NOT alter any payment or order state
3. THE SYSTEM SHALL store the provider event identifier as a unique key
4. WHEN a provider event identifier has already been processed THEN the system SHALL treat the delivery as a no-op
5. THE SYSTEM SHALL map each provider event type to an internal payment event before transitioning state
6. THE SYSTEM SHALL finalize an order only after verification it independently trusts
7. WHEN provider events arrive out of order THEN the system SHALL reach the correct final state
8. WHEN the browser reports success but no webhook has arrived THEN the system SHALL verify the provider response and fetch provider status, holding the order pending until verification succeeds
9. THE SYSTEM SHALL NOT require an authenticated session on the webhook endpoint, and SHALL rely on signature verification as its sole trust mechanism

---

### Requirement 17: Idempotent money mutations

**User Story:** As a buyer, I want a retried request to return the original outcome, so that a network hiccup or an impatient click cannot charge me twice.

#### Acceptance Criteria

1. THE SYSTEM SHALL require an idempotency key on every money-mutating endpoint
2. THE SYSTEM SHALL enforce uniqueness of actor, endpoint, and idempotency key together
3. WHEN a request is replayed with the same key and the same request body THEN the system SHALL return the originally stored response
4. WHEN a key is reused with a different request body THEN the system SHALL return a key-reused-with-different-request error
5. WHEN a payment request is submitted twice with the same key THEN the system SHALL create exactly one provider order
6. WHEN two identical requests arrive concurrently THEN exactly one SHALL execute and the other SHALL replay its result
7. WHEN a replay occurs THEN the system SHALL emit an idempotency-replayed audit event
8. THE SYSTEM SHALL retry only read-only queries, provider status lookups, webhook processing, and non-money background tasks automatically
9. WHEN a money-mutating call must be retried THEN the system SHALL either reuse the same idempotent operation or first query the previous attempt

---

### Requirement 18: Failure detection and recovery

**User Story:** As a buyer, I want to be told plainly what happened when something goes wrong, so that I am never left uncertain whether I was charged.

#### Acceptance Criteria

1. WHEN an offer price or version changes between approval and payment THEN the system SHALL detect the mismatch, mark the checkout price-changed, and SHALL NOT create a provider payment
2. WHEN a price change blocks payment THEN the system SHALL state that the approved offer changed and no charge was made, and SHALL offer a fresh comparison and new authorization
3. THE SYSTEM SHALL NOT silently substitute a different offer after buyer approval
4. WHEN a provider call times out THEN the system SHALL mark the payment unknown and SHALL NOT immediately create another provider order
5. WHILE a payment is unknown THE SYSTEM SHALL query provider status using the existing attempt, verifying and finalizing if captured, exposing failure if failed, and retaining unknown status otherwise
6. WHEN bounded retries are exhausted for an unknown payment THEN the system SHALL place it in a manual-review state
7. THE SYSTEM SHALL sweep expired checkouts, transition them, and release their reservations
8. THE SYSTEM SHALL make the expiry sweep idempotent so releasing an already-released reservation is a no-op
9. THE SYSTEM SHALL NOT issue a second charge request in any failure scenario
10. THE SYSTEM SHALL surface each failure state in plain language with a recovery path available from the interface
11. THE SYSTEM SHALL NOT hide a blocked action behind a generic error

---

### Requirement 19: Agent capability discovery

**User Story:** As an independent AI buyer that has never contacted this merchant, I want one call that tells me what I can do and how, so that I can transact without prior integration work.

#### Acceptance Criteria

1. THE SYSTEM SHALL serve a capability document at a well-known discovery path and at a versioned agent path
2. THE SYSTEM SHALL return an identical payload from both paths
3. THE SYSTEM SHALL generate the document from live policy and capability configuration
4. THE SYSTEM SHALL NOT serve a hand-maintained static document that can drift from actual behaviour
5. WHEN a policy limit changes in configuration THEN the served document SHALL reflect the change
6. THE SYSTEM SHALL declare schema version, authentication method, token endpoint, available scopes, capabilities, limits, endpoints, and a policy summary
7. THE SYSTEM SHALL state the payment provider and that it operates in test mode
8. THE SYSTEM SHALL NOT expose secrets, internal identifiers, or infrastructure details in the document
9. THE SYSTEM SHALL state plainly that it is not a certified implementation of any external protocol
10. THE SYSTEM SHALL permit the document to be cached for a short period

---

### Requirement 20: Independent external buyer completing a real purchase

**User Story:** As a judge, I want to watch a client that has no relationship to this codebase complete a purchase over the documented API, so that the interoperability claim is demonstrated rather than asserted.

#### Acceptance Criteria

1. THE SYSTEM SHALL expose a public agent surface sufficient to discover capabilities, authenticate, search, create a checkout, request and hold authorization, create a payment, poll for verification, and fetch the resulting order
2. THE SYSTEM SHALL authenticate external agents by API key exchanged for a scoped bearer token
3. THE SYSTEM SHALL enforce scope on every agent endpoint
4. WHEN a request lacks the required scope THEN the system SHALL reject it with a deterministic authorization error
5. THE SYSTEM SHALL NOT provide any privileged shortcut, special header, or backdoor available to the external client
6. THE SYSTEM SHALL produce identical domain results whether an operation is invoked through the agent surface or the internal surface
7. THE EXTERNAL BUYER SHALL reside in a separate repository from the gateway, with no shared privileged code path
8. THE EXTERNAL BUYER SHALL obtain its own human approval for authorization within its own interface
9. WHEN the external buyer runs its full scenario against the fake provider THEN it SHALL reach a confirmed order
10. WHEN the external buyer runs its full scenario against Razorpay test mode THEN it SHALL reach a confirmed order
11. THE EXTERNAL BUYER SHALL NOT be able to bypass policy evaluation, authorization, or payment verification

---

### Requirement 21: Contract test suite driven by the external client

**User Story:** As a developer, I want the public contract exercised by the independent client in CI, so that a breaking change to the agent surface fails the build.

#### Acceptance Criteria

1. THE SYSTEM SHALL include contract tests covering capability discovery, full purchase flow, negotiation bounds, authorization binding, idempotent payment, price-change rejection, payment timeout recovery, and blocked unauthorized access
2. THE SYSTEM SHALL drive contract tests through the same client the external buyer uses
3. THE SYSTEM SHALL run the contract suite in continuous integration on any change to the public agent surface
4. THE SYSTEM SHALL publish a compliance summary recording pass count, state transitions covered, and failure codes covered
5. THE SYSTEM SHALL include a test asserting that the documented state transitions are covered by the suite
6. WHEN an authorization from one checkout is used to pay another THEN the contract suite SHALL observe a mismatch error
7. WHEN a payment is requested twice with one key THEN the contract suite SHALL observe one provider order and one payment identifier

---

### Requirement 22: Bounded model use and prompt safety

**User Story:** As a platform operator, I want the model influence confined to interpretation and explanation, so that untrusted text cannot turn into a financial action.

#### Acceptance Criteria

1. THE SYSTEM SHALL validate every extracted intent against a published schema before use
2. THE SYSTEM SHALL reject unknown financial fields in an extracted intent rather than ignoring them
3. THE SYSTEM SHALL classify prompt safety before the main agent runs, and SHALL NOT rely on that classification as its only defence
4. THE SYSTEM SHALL enforce a maximum input length
5. THE SYSTEM SHALL present product descriptions, reviews, merchant notes, and retrieved pages to the model as delimited evidence with provenance
6. THE SYSTEM SHALL NOT execute, obey, or act upon instructions found within untrusted content
7. WHEN untrusted content instructs the system to ignore a budget, exfiltrate a credential, report a payment as successful, or override a return policy THEN the system SHALL leave tool permissions and deterministic checks unchanged and SHALL record the attempt
8. THE SYSTEM SHALL expose only allowlisted tools to the agent
9. WHEN a non-allowlisted tool is requested THEN the system SHALL block it and emit a tool-blocked audit event
10. THE SYSTEM SHALL validate every tool argument against a schema defining types, maximum lengths, enumerations, and quantity caps
11. THE SYSTEM SHALL enforce step and time limits on an agent run
12. THE SYSTEM SHALL require explicit confirmation before any consequential action
13. THE SYSTEM SHALL NOT expose raw database access through any tool
14. THE SYSTEM SHALL NOT display hidden chain-of-thought reasoning
15. THE SYSTEM SHALL record the model version on audit events that involve a model
16. WHEN the model provider times out THEN the system SHALL return a structured error rather than hanging

---

### Requirement 23: The model has no write path to money

**User Story:** As a security reviewer, I want the boundary between the model and the financial core enforced mechanically, so that it cannot erode through ordinary development.

#### Acceptance Criteria

1. THE SYSTEM SHALL prevent the agent layer from importing any database library or repository module
2. THE SYSTEM SHALL prevent the payment layer from importing the agent layer or the model gateway
3. THE SYSTEM SHALL prevent domain services from importing the API or worker layers
4. THE SYSTEM SHALL verify these boundaries in continuous integration such that a violation fails the build
5. THE SYSTEM SHALL NOT permit any model output to alter price, inventory, policy, authorization amount, or payment amount
6. THE SYSTEM SHALL route every agent tool call through an application service that enforces permission and validation
7. THE SYSTEM SHALL read every displayed price, stock level, and delivery estimate from the offer record rather than from model output

---

### Requirement 24: Authentication, authorization, and tenant isolation

**User Story:** As a merchant, I want my catalog, pricing, and transactions inaccessible to other tenants, so that using a shared gateway is safe.

#### Acceptance Criteria

1. THE SYSTEM SHALL support session authentication for the web application and token authentication for external agents
2. THE SYSTEM SHALL define roles for buyer, merchant administrator, merchant operator, and platform administrator
3. THE SYSTEM SHALL enforce tenant isolation on every query using merchant and buyer identifiers
4. WHEN a query is constructed without a tenant filter THEN the system SHALL fail rather than return unfiltered rows
5. WHEN one tenant requests another tenant data THEN the system SHALL deny the request
6. WHEN a buyer acts on a checkout, authorization, payment, or order they do not own THEN the system SHALL deny the request
7. WHEN an access token has expired THEN the system SHALL deny the request
8. THE SYSTEM SHALL validate maximum message length, identifier format, quantity limits, currency allowlist, URL scheme and host, tool arguments, and payload size on input
9. THE SYSTEM SHALL restrict cross-origin requests to configured origins
10. THE SYSTEM SHALL NOT permit a wildcard cross-origin configuration outside local development
11. THE SYSTEM SHALL refuse to start outside local development if a placeholder secret remains configured

---

### Requirement 25: Observability without leakage

**User Story:** As an operator, I want to debug a failed transaction from logs alone, without those logs becoming a credential store.

#### Acceptance Criteria

1. THE SYSTEM SHALL emit structured JSON logs carrying timestamp, level, service, event, correlation identifiers, latency, outcome, and error code
2. THE SYSTEM SHALL attach whichever correlation identifiers are in scope to every log line automatically
3. THE SYSTEM SHALL mask values whose key names indicate a secret
4. THE SYSTEM SHALL mask values whose shape matches a credential, regardless of the key they appear under
5. THE SYSTEM SHALL NOT log provider secrets, authorization tokens, payment credentials, unredacted personal addresses, or model prompts containing sensitive data
6. THE SYSTEM SHALL bound redaction recursion so a pathological payload cannot stall logging
7. THE SYSTEM SHALL scrub exception and stack text before emission
8. THE SYSTEM SHALL NOT include a database connection string or driver message in a health probe response
9. THE SYSTEM SHALL fail its build if a credential-shaped string appears in a tracked file
10. THE SYSTEM SHALL measure commerce, agent, safety, and payment metrics from real logged data
11. THE SYSTEM SHALL NOT present an unmeasured or estimated metric as a measured one

---

### Requirement 26: Buyer intent capture and AI activity transparency

**User Story:** As a buyer, I want to describe what I need in my own words and watch what the agent actually did, so that I trust the result without needing to understand the system behind it.

#### Acceptance Criteria

1. THE SYSTEM SHALL accept a natural-language request as the primary entry point to the buyer experience
2. THE SYSTEM SHALL display the constraints it extracted from that request in structured, human-readable form
3. WHEN extracted constraints differ from what the buyer intended THEN the buyer SHALL be able to correct them and re-run without retyping the request
4. THE SYSTEM SHALL display a step-by-step activity summary naming each action taken and its outcome
5. THE SYSTEM SHALL display the count of candidate offers considered and the count that satisfied all hard constraints
6. THE SYSTEM SHALL NOT display hidden chain-of-thought, deliberation narration, or elapsed-thinking commentary
7. THE SYSTEM SHALL indicate progress while an agent run is in flight rather than presenting a blank or frozen view
8. IF an agent run fails or times out THEN the system SHALL state what completed, what did not, and offer a retry
9. THE SYSTEM SHALL make every extracted monetary constraint visible in major units while transmitting minor units
10. THE SYSTEM SHALL present deterministic filters that a buyer can adjust directly, independent of the conversation

---

### Requirement 27: Product representation and product question answering

**User Story:** As a buyer, I want a product page that foregrounds what matters for my stated purpose and lets me ask follow-up questions, so that I can decide without cross-referencing specification sheets myself.

#### Acceptance Criteria

1. THE SYSTEM SHALL display product imagery, title, rating, price, key specifications, stock status, and delivery estimate on the product view
2. THE SYSTEM SHALL emphasise the specification fields relevant to the buyer stated intent, and SHALL source every displayed value from the backend
3. THE SYSTEM SHALL read price, stock, delivery estimate, and return policy from the offer record and SHALL NOT display a model-generated value for any of them
4. THE SYSTEM SHALL provide a per-product question interface
5. WHEN a product question is answered THEN the system SHALL cite its sources and label each claim as a catalog fact, an official documentation fact, an inference, or unresolved
6. WHEN a product question cannot be answered from available evidence THEN the system SHALL say so and SHALL NOT fabricate a citation
7. THE SYSTEM SHALL present a fit assessment that distinguishes satisfied requirements from caveats
8. THE SYSTEM SHALL state that displayed prices are generated rather than scraped market prices
9. WHEN an image is unavailable THEN the system SHALL render a labelled placeholder rather than a broken image
10. THE SYSTEM SHALL keep a product question interaction free of any ability to alter price, inventory, or checkout state

---

### Requirement 28: Offer comparison and recommendation rationale

**User Story:** As a buyer, I want to see why one offer was recommended over the others against my own constraints, so that the recommendation is a reasoned argument rather than an opaque score.

#### Acceptance Criteria

1. THE SYSTEM SHALL compare offers rather than products alone, showing price, delivery, return period, inventory, and merchant per offer
2. THE SYSTEM SHALL identify the recommended offer explicitly
3. THE SYSTEM SHALL justify the recommendation by reference to the buyer stated constraints
4. WHEN a candidate offer fails a hard constraint THEN the system SHALL state which constraint it failed
5. THE SYSTEM SHALL NOT present a bare numeric score as the sole justification
6. THE SYSTEM SHALL distinguish a recommendation from an authorization, and SHALL NOT let selecting an offer authorize a payment
7. THE SYSTEM SHALL display offer expiry so a buyer can see that terms are time-bound
8. THE SYSTEM SHALL revalidate offers before display and SHALL NOT show a stale offer as current

---

### Requirement 29: Authorization screen

**User Story:** As a buyer, I want to see exactly what I am approving and why approval is required, so that consent is informed and specific to one purchase.

#### Acceptance Criteria

1. THE SYSTEM SHALL display the exact merchant, product, quantity, itemised amounts, total, and currency at approval time
2. THE SYSTEM SHALL itemise product price, shipping, tax, and discount separately from the total
3. THE SYSTEM SHALL display delivery estimate and return policy on the approval view
4. THE SYSTEM SHALL display the policy decision and the machine-readable reason that approval is required
5. THE SYSTEM SHALL display which policy checks passed and which triggered the approval requirement
6. THE SYSTEM SHALL display the authorization expiry as a countdown derived from server time
7. THE SYSTEM SHALL NOT allow client clock drift to extend an authorization beyond its server-side expiry
8. WHEN the authorization expires while displayed THEN the system SHALL disable approval and offer revalidation
9. THE SYSTEM SHALL present approve and reject as equally available, clearly labelled actions
10. THE SYSTEM SHALL NOT use a vague confirmation prompt in place of the exact amount and merchant
11. THE SYSTEM SHALL NOT pre-select, default to, or auto-submit approval
12. THE SYSTEM SHALL make the approval control operable by keyboard and describable by a screen reader, with the amount and merchant included in its accessible label
13. THE SYSTEM SHALL generate an idempotency key for the approval and payment requests it issues

---

### Requirement 30: Payment progress, receipt, and test-mode disclosure

**User Story:** As a buyer, I want to see payment progress and a verifiable receipt, and to know unambiguously that this is a test transaction.

#### Acceptance Criteria

1. THE SYSTEM SHALL display a persistent, visible indicator that the system is operating in payment test mode whenever the configured provider is in test mode
2. THE SYSTEM SHALL NOT describe a test-mode transaction as a production payment anywhere in the interface
3. THE SYSTEM SHALL display payment progress through order creation, buyer payment, and server-side verification as distinct stages
4. THE SYSTEM SHALL describe each payment state in plain language rather than a raw status code
5. WHILE verification is pending THE SYSTEM SHALL state that verification is in progress and SHALL NOT present the order as confirmed
6. WHEN payment is verified THEN the system SHALL display amount, payment identifier, order identifier, and a link to the transaction record
7. THE SYSTEM SHALL NOT display an order as confirmed before the server has independently verified the payment
8. THE SYSTEM SHALL NOT receive or hold any provider secret key in the browser
9. THE SYSTEM SHALL obtain payment status from the backend and SHALL NOT treat a client-side provider callback as proof of payment
10. THE SYSTEM SHALL make the receipt available for later retrieval from the order history

---

### Requirement 31: Transaction timeline and graceful failure recovery

**User Story:** As a buyer, I want a timestamped record of everything that happened, and when something goes wrong I want to know whether I was charged, so that a failure is never ambiguous.

#### Acceptance Criteria

1. THE SYSTEM SHALL render a timestamped timeline covering request receipt, intent extraction, catalog search, offer selection, policy evaluation, authorization, payment creation, verification, and order confirmation
2. THE SYSTEM SHALL include failure and recovery events in the timeline
3. THE SYSTEM SHALL make the rendered timeline match the audit ledger exactly
4. THE SYSTEM SHALL display correlation identifiers on the timeline so a record can be located in the audit ledger
5. WHEN a price change blocks payment THEN the system SHALL display the approved amount, the current amount, and an explicit statement that no charge was made
6. WHEN a price change blocks payment THEN the system SHALL offer a fresh comparison and a new authorization as available actions
7. WHEN payment status is unknown THEN the system SHALL state that confirmation has not arrived, that no second payment was created, and that status is being checked
8. WHEN payment status is unknown THEN the system SHALL offer a status refresh action
9. WHEN an action is blocked by policy THEN the system SHALL display the reason and SHALL NOT show a generic error
10. THE SYSTEM SHALL make every failure state recoverable from the interface without requiring a page reload or a support request
11. THE SYSTEM SHALL NOT display a monetary outcome as uncertain when the backend has determined it, nor as certain when the backend has not

---

### Requirement 32: Merchant console and agent activity inspection

**User Story:** As a merchant operator, I want to see how AI buyers are interacting with my catalog and inspect any individual agent run, so that opening an AI sales channel is something I can supervise.

#### Acceptance Criteria

1. THE SYSTEM SHALL display counts of AI buyers, offer requests, AI-originated orders, and conversion rate, each computed from logged data
2. THE SYSTEM SHALL NOT display a hardcoded, estimated, or placeholder figure on the merchant console
3. THE SYSTEM SHALL display recent agent activity with per-entry outcome, including successes, price changes, and policy blocks
4. THE SYSTEM SHALL provide a per-agent-run detail view listing the request, the tools invoked, the offer selected, the decision rationale, the policy decision, and the current status
5. THE SYSTEM SHALL present decision evidence on the agent run view and SHALL NOT present chain-of-thought
6. THE SYSTEM SHALL display blocked tool calls and blocked actions rather than omitting them
7. THE SYSTEM SHALL display failed and recovered payments distinctly from successful ones
8. THE SYSTEM SHALL enforce merchant scoping on every console view
9. THE SYSTEM SHALL provide a view rendering the live agent capability document, so a merchant can see what an AI buyer discovers
10. THE SYSTEM SHALL display the outcome of an external buyer agent run against this merchant, including the state transitions it traversed

---

### Requirement 33: Merchant catalog operations

**User Story:** As a merchant administrator, I want to import, validate, review, and publish a catalog with measured quality figures, so that I control what AI buyers can transact against.

#### Acceptance Criteria

1. THE SYSTEM SHALL present catalog import, validation, review, and publish as distinct, ordered steps
2. THE SYSTEM SHALL display import history with per-run provenance
3. THE SYSTEM SHALL display total imported, valid, and needs-review counts computed from the actual import run
4. THE SYSTEM SHALL display image availability figures computed from the actual manifest and download results
5. WHEN a catalog health figure is displayed THEN it SHALL equal the computed value from the import artifacts
6. THE SYSTEM SHALL NOT display a hand-entered catalog statistic
7. THE SYSTEM SHALL list products requiring review with the rule each one violated
8. THE SYSTEM SHALL require an explicit publish action and SHALL NOT publish automatically on import
9. THE SYSTEM SHALL display active offers and inventory warnings
10. THE SYSTEM SHALL indicate that offer prices are generated rather than scraped

---

### Requirement 34: Merchant AI policy control

**User Story:** As a merchant administrator, I want to see and set exactly what an AI buyer is permitted to do, so that the agent operates inside limits I chose.

#### Acceptance Criteria

1. THE SYSTEM SHALL display the maximum transaction amount, automatic approval limit, allowed categories, blocked categories, maximum discount percentage, out-of-stock selling setting, and return policy override setting
2. THE SYSTEM SHALL allow a merchant administrator to change these rules
3. WHEN a rule changes THEN the system SHALL version the change and record who made it
4. WHEN a rule changes THEN the served agent capability document SHALL reflect the new value
5. THE SYSTEM SHALL display amounts in major units while storing and transmitting minor units
6. THE SYSTEM SHALL state the effect of each rule in plain language
7. THE SYSTEM SHALL NOT allow a model to alter a merchant rule
8. THE SYSTEM SHALL require merchant administrator role for any rule change

---

### Requirement 35: Audit explorer

**User Story:** As a merchant operator, I want to search the audit ledger by any correlation identifier and read the full event record, so that any money action can be explained after the fact.

#### Acceptance Criteria

1. THE SYSTEM SHALL allow audit search by transaction, agent run, checkout, payment, order, and buyer identifier, and by date range
2. THE SYSTEM SHALL display, per event, the event type, actor type, actor identifier, aggregate reference, amount where relevant, reason code, policy version, model version, and timestamp
3. THE SYSTEM SHALL return events in chronological order for a given aggregate
4. THE SYSTEM SHALL NOT display any secret, credential, or token value in an audit view
5. THE SYSTEM SHALL allow navigation from an audit event to the related checkout, authorization, payment, and order
6. THE SYSTEM SHALL enforce merchant scoping on audit search
7. THE SYSTEM SHALL make every money action traceable to the policy decision and authorization that permitted it

---

### Requirement 36: Frontend architecture and trust boundary

**User Story:** As a security reviewer, I want the browser treated as an untrusted client, so that no control that matters can be bypassed from the front end.

#### Acceptance Criteria

1. THE SYSTEM SHALL route all browser requests through the backend API and SHALL NOT have the browser contact the payment provider secret API, the database, or the model provider directly
2. THE SYSTEM SHALL NOT include any provider secret, model API key, or database credential in the client bundle
3. THE SYSTEM SHALL compute every monetary total on the server, and the frontend SHALL display server-computed values without recalculating them
4. THE SYSTEM SHALL perform no floating-point arithmetic on monetary values in the client
5. THE SYSTEM SHALL format currency from integer minor units through a single shared utility
6. THE SYSTEM SHALL send an idempotency key on every money-mutating request it issues
7. THE SYSTEM SHALL NOT apply optimistic updates to payment, authorization, or order state
8. THE SYSTEM SHALL treat authorization expiry as server-authoritative
9. THE SYSTEM SHALL separate buyer and merchant surfaces so merchant views are unreachable without the corresponding role
10. THE SYSTEM SHALL handle session and token expiry by prompting re-authentication rather than failing silently
11. THE SYSTEM SHALL validate every response against the expected shape before rendering it

---

### Requirement 37: Interface quality and accessibility

**User Story:** As any user, including one relying on assistive technology, I want the interface to be legible, navigable, and honest about state, so that the system is usable rather than merely attractive.

#### Acceptance Criteria

1. THE SYSTEM SHALL provide keyboard operability for the entire purchase flow from intent through authorization to receipt
2. THE SYSTEM SHALL provide accessible names and roles for all interactive controls
3. THE SYSTEM SHALL announce state changes in the purchase flow to assistive technology
4. THE SYSTEM SHALL meet automated accessibility checks on the primary buyer flow and the authorization view
5. THE SYSTEM SHALL maintain sufficient colour contrast and SHALL NOT convey status by colour alone
6. THE SYSTEM SHALL render usable layouts across mobile, tablet, and desktop widths
7. THE SYSTEM SHALL present explicit empty, loading, and error states for every data-backed view
8. THE SYSTEM SHALL keep motion subtle and SHALL respect a reduced-motion preference
9. THE SYSTEM SHALL use restrained, finance-appropriate visual design and SHALL NOT use decorative artificial-intelligence imagery in place of information
10. THE SYSTEM SHALL make status indicators for offer validity, policy outcome, and payment state visually distinct and textually labelled
11. THE SYSTEM SHALL avoid layout shift when asynchronous content resolves

---

---

### Requirement 38: Verified integration with real external services

**User Story:** As a developer, I want the real model provider and the real payment provider exercised against live test endpoints, so that adapters are proven against actual behaviour rather than only against fakes.

#### Acceptance Criteria

1. THE SYSTEM SHALL provide integration tests that exercise the real model provider using configured credentials
2. THE SYSTEM SHALL provide integration tests that exercise Razorpay test mode using configured credentials
3. THE SYSTEM SHALL mark real-service integration tests separately so they are excluded from the default test run
4. WHEN credentials for a real service are absent THEN its integration tests SHALL skip rather than fail
5. THE SYSTEM SHALL keep the fake and mock providers as the default for all other suites
6. WHEN the real model provider is exercised THEN the system SHALL verify structured output validation, timeout handling, and error mapping against live responses
7. WHEN Razorpay test mode is exercised THEN the system SHALL verify order creation, payment fetch, and signature verification against live responses
8. THE SYSTEM SHALL NOT commit any credential to a tracked file
9. THE SYSTEM SHALL read all credentials from untracked environment configuration
10. THE SYSTEM SHALL label a test-mode transaction as test mode and SHALL NOT describe it as a production payment

---

### Requirement 39: Bounded offer negotiation

**User Story:** As an AI buyer, I want to propose a lower price and receive a policy-bounded answer, so that agent-to-agent bargaining happens within merchant-defined limits.

#### Acceptance Criteria

1. THE SYSTEM SHALL compute the negotiation floor as list price reduced by the merchant configured maximum discount percentage
2. WHEN a proposed price is at or above the floor THEN the system SHALL accept at the proposed price
3. WHEN a proposed price is below the floor THEN the system SHALL counter at the floor with a maximum-discount-exceeded reason
4. THE SYSTEM SHALL limit negotiation to a fixed maximum number of rounds
5. WHEN the round limit is exceeded THEN the system SHALL return the best remaining valid offer and close negotiation
6. THE SYSTEM SHALL compute every negotiation decision in the policy engine
7. THE SYSTEM SHALL NOT permit a model to set the floor price, grant a discount beyond configuration, or skip deterministic evaluation
8. THE SYSTEM SHALL persist each negotiation round
9. THE SYSTEM SHALL permit a model to decide whether to propose a price, whether to accept a counter-offer, and how to explain the outcome
10. THE SYSTEM SHALL display each negotiation round and its outcome in the buyer interface

---

### Requirement 40: Semantic retrieval

**User Story:** As a buyer, I want a vaguely worded request to find suitable products, without semantics being allowed to influence commerce facts.

#### Acceptance Criteria

1. THE SYSTEM SHALL generate embeddings over product title, normalized features, and description
2. THE SYSTEM SHALL combine deterministic filtering with semantic candidate retrieval before ranking
3. THE SYSTEM SHALL NOT use a semantic score to determine price, inventory, or authorization eligibility
4. WHEN a semantically retrieved candidate fails a hard constraint THEN the system SHALL exclude it
5. THE SYSTEM SHALL revalidate every offer before display or checkout
6. THE SYSTEM SHALL keep catalog search latency within the stated performance target at full catalog size

---

### Requirement 41: Bounded research with cited evidence

**User Story:** As a buyer, I want open-world product questions answered with sources and an honest confidence level, so that I can tell a verified fact from an inference.

#### Acceptance Criteria

1. THE SYSTEM SHALL restrict research to compatibility, port and display support, official operating system compatibility, accessory requirements, and documentation clarification
2. THE SYSTEM SHALL NOT perform unrestricted web browsing, purchase based on arbitrary web pages, or offer medical, legal, or financial advice
3. THE SYSTEM SHALL enforce maximum step, search, and page counts, a per-page response size limit, and a wall-clock limit
4. THE SYSTEM SHALL restrict page retrieval to permitted schemes and hosts
5. THE SYSTEM SHALL block retrieval of private, loopback, and link-local addresses
6. THE SYSTEM SHALL obtain its search host from configuration only, and SHALL NOT derive it from a request, a model output, or product text
7. THE SYSTEM SHALL record source URL, title, publisher, retrieval time, content hash, excerpt, confidence, and source type for each piece of evidence
8. THE SYSTEM SHALL distinguish a catalog fact, an official documentation fact, an inference, and an unresolved point
9. THE SYSTEM SHALL cite the source for every research-derived claim
10. THE SYSTEM SHALL strip scripts and non-content markup during extraction
11. THE SYSTEM SHALL NOT obey instructions contained in a retrieved page
12. WHEN research is unavailable THEN the system SHALL answer from catalog facts and report research as unavailable, and SHALL NOT fabricate a citation
13. THE SYSTEM SHALL cache research results to limit repeated upstream requests

---

### Requirement 42: Cross-sell with measured effect

**User Story:** As a merchant, I want compatible accessory recommendations and an honest measurement of their effect, so that the revenue claim is evidence rather than assertion.

#### Acceptance Criteria

1. THE SYSTEM SHALL source category pairings from merchant configuration
2. THE SYSTEM SHALL NOT allow a model to invent a compatibility relationship
3. WHEN a checkout contains a product whose category has configured pairings and no item from a paired category THEN the system SHALL offer recommendations
4. THE SYSTEM SHALL cap the number of recommendations
5. THE SYSTEM SHALL attach a machine-readable reason code to each recommendation
6. THE SYSTEM SHALL permit a model to translate a reason code into natural language, and SHALL NOT permit it to substitute its own justification
7. THE SYSTEM SHALL NOT add a recommended item to a checkout without explicit acceptance
8. THE SYSTEM SHALL respect inventory availability and merchant category rules
9. THE SYSTEM SHALL measure baseline average order value, assisted average order value, and attachment rate from logged evaluation data
10. THE SYSTEM SHALL NOT report a fabricated or estimated figure for these measurements

---

### Requirement 43: Catalog scale and performance

**User Story:** As a platform operator, I want the catalog scaled in stages with latency measured at each step, so that indexes are added because measurement justified them.

#### Acceptance Criteria

1. THE SYSTEM SHALL run a bounded validation pass over a capped number of source lines before any full-scale run
2. THE SYSTEM SHALL import in stages of approximately 100, then 1,000, then the full target
3. THE SYSTEM SHALL measure search latency at each stage
4. THE SYSTEM SHALL keep catalog search 95th-percentile latency under 300 milliseconds at 20,000 products
5. THE SYSTEM SHALL add an index only where measurement demonstrates the need

---

### Requirement 44: Reproducibility and honest documentation

**User Story:** As a new developer or reviewer, I want to reproduce the demo from documented commands and understand the system real limits, so that nothing rests on undocumented local state.

#### Acceptance Criteria

1. WHEN a clean clone follows the documented commands THEN the system SHALL start and serve the golden path
2. THE SYSTEM SHALL run its default test suite without credentials, Docker, or network access
3. THE SYSTEM SHALL document architecture, API, security, state machine, failure modes, protocol scope, and the demo script
4. THE SYSTEM SHALL record an architecture decision record for each significant decision
5. THE SYSTEM SHALL state, per referenced external protocol concept including ACP, AP2, x402, and MCP, whether it is conceptually inspired, structurally implemented, or not implemented, with justification
6. THE SYSTEM SHALL NOT claim to implement or be certified against any external protocol specification
7. THE SYSTEM SHALL state its limitations and its gaps relative to production
8. THE SYSTEM SHALL ship every schema change as a reviewed migration
9. THE SYSTEM SHALL apply and roll back migrations cleanly
10. THE SYSTEM SHALL state that product prices are generated rather than scraped wherever they are presented

## Non-Functional Requirements

### Money representation

- **NFR-1** Every monetary value SHALL be an integer in minor units. Binary floating-point for a monetary value is a defect, not a stylistic choice. This applies to the frontend as well as the backend.

### Security

- **NFR-2** The model SHALL have no write path to price, inventory, policy, authorization, or payment state. Enforced by import contracts checked in continuous integration.
- **NFR-3** Payment provider secrets SHALL remain server-side, absent from any frontend bundle, log, audit event, or model prompt.
- **NFR-4** Untrusted content SHALL be presented as quoted evidence with provenance, never as an instruction channel.
- **NFR-9** Logs SHALL be structured JSON with redaction by key name and by value shape.

### Correctness

- **NFR-5** Every consequential action SHALL emit an audit event in the same database transaction as the state change it describes.
- **NFR-8** The ingestion pipeline SHALL be resumable at every stage boundary, writing in batches of 2,000, with no unbounded in-memory accumulation.
- **NFR-10** Every schema change SHALL ship as a reviewed migration. Seed scripts SHALL be idempotent.

### Performance

- **NFR-7** Catalog search SHALL complete within 300 milliseconds at the 95th percentile with 20,000 products.
- **NFR-11** The buyer interface SHALL render a meaningful first view without waiting for an agent run to complete.

### Architecture

- **NFR-6** The system SHALL remain a modular monolith. No Kubernetes, message broker, additional microservice, second database engine, or metrics stack SHALL be introduced before the golden path, the external buyer, and the contract suite are complete.
- **NFR-12** The frontend SHALL be a single Next.js application with TypeScript, separating buyer and merchant surfaces by route group and by role.

### Honesty

- **NFR-13** No figure presented in any interface SHALL be hardcoded, estimated, or fabricated. Every displayed metric SHALL be computed from logged or imported data.
- **NFR-14** Test-mode payment SHALL be disclosed wherever a payment is presented.

---

## Track 01 Alignment

The competition brief sets the bar as: every money action explainable, bounded and gated, with the audit trail and one failure handled gracefully. Each clause maps to requirements above.

| Bar clause | Met by | Demonstrated in the interface by |
|---|---|---|
| **Explainable** | R9 audit ledger, R12 reason codes, R28 recommendation rationale | Agent run detail (R32.4), audit explorer (R35), recommendation justification (R28.3) |
| **Bounded** | R12 policy engine, R23 no model write path, R39 negotiation floor, R22 tool allowlist | Merchant AI policy screen (R34), blocked actions shown (R32.6) |
| **Gated** | R13 authorization binding, R12 approval requirement | Authorization screen with exact amount and expiry (R29) |
| **Audit trail** | R9, R35 | Transaction timeline matching the ledger (R31.1, R31.3) |
| **One failure handled gracefully** | R18 price change and provider timeout recovery | Price-changed and status-unknown screens stating no charge was made (R31.5, R31.7) |

The brief also names ACP, AP2, and x402 as the surrounding protocol race. Per D-9, the system claims none of them. `docs/protocol-scope.md` SHALL carry a row for each of ACP, AP2, x402, and MCP, marking it conceptually inspired, structurally implemented, or not implemented, with justification (Requirement 44, criterion 5).

Listed example directions and their coverage:

| Example direction | Coverage |
|---|---|
| Conversational in-app checkout | R26, R28, R29, R30 |
| Agent-readable catalog | R19 capability discovery, R32.9 merchant view of it |
| Upsell and cross-sell agent | R42 cross-sell with measured effect (post-MVP) |
| Campaign orchestrator | Out of scope, deferred |

---

## Out of Scope

| Excluded | Reason |
|---|---|
| Real-money payments | Test mode only, disclosed in the interface |
| Certified ACP, AP2, x402, or MCP compliance | Overclaiming is a credibility risk (D-9) |
| Unrestricted web browsing | Research is deliberately narrow |
| Multi-merchant marketplace | One merchant for the MVP |
| Refund user experience | Adapter method only |
| Native mobile applications | Responsive web only |
| Bulk image downloading | Manifest only until catalog selection is frozen |
| Campaign automation | Deferred |
| Simulated cryptographic mandates | Avoids implying AP2 conformance (D-6) |
| Server-side rendering of authenticated merchant data | Client-side fetch against the authenticated API is sufficient |

---

## Frontend Build Order

The first frontend milestone is one polished buyer flow, not a complete dashboard. The merchant console is built around it afterwards, once there are real runs to display.

1. Intent capture and AI activity summary (R26)
2. Offer results with deterministic filters (R26.10, R28)
3. Product detail with dynamic emphasis (R27)
4. Offer comparison and recommendation rationale (R28)
5. Authorization screen (R29)
6. Payment progress and verified receipt (R30)
7. Transaction timeline and the two failure screens (R31)
8. Merchant console, catalog, policy, agent activity, audit explorer (R32 to R35)

Product question answering (R27.4 to R27.6) depends on the research agent and lands with it.

---

## Completed Work

Task 1 is complete. Its acceptance criteria are recorded above so they remain regression-protected rather than assumed, and are already covered by 82 passing tests.

| Delivered | Requirements covered |
|---|---|
| Repository scaffold, pinned dependencies, task runner | 44.2 |
| Typed configuration with fake and mock defaults | 14.3, 14.7, 24.10, 24.11 |
| Structured JSON logging with key-name and value-shape redaction | 25.1, 25.3, 25.4, 25.6, 25.7 |
| Correlation identifiers across async boundaries and worker handoff | 9.3, 25.2 |
| Health probes that do not leak the connection string | 25.8 |
| Architecture import contracts, four passing | 23.1, 23.2, 23.3, 23.4 |
| Repository-wide credential sweep, verified by a planted key | 25.9, 38.8 |
| Web search provider decision recorded (ADR-0009) | 41.6, 44.4 |

Known open item: the container build needs its copy-before-install reorder. The host workflow is unaffected.
