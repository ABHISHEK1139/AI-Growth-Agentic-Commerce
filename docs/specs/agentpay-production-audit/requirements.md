# Requirements Document

## Introduction

The AgentPay implementation in `agentpay/` is reported complete. That claim is unproven: the README status table still lists Phases B through I as not started, `docs/` is missing two files the README links to, and the first inspection of `services/agent/guard.py` already found a fail-open defect in the layered prompt guard. This document specifies a production audit that replaces claims with execution evidence.

The audit reviews every source file, records every defect, sets up the real toolchain, runs the real gates, drives adversarial guardrail testing and human-like browser journeys, and ends in an honest report that separates what was verified by execution from what remains unverified.

The audit also measures the implementation against two external targets: the supplied frontend product vision (polished human storefront, merchant and AI control layer, developer and agent playground, contextual AI across search, product, compare, cart, checkout and orders, one shared deterministic commerce core, structured shopping-session memory, graceful AI degradation, interactive constraint relaxation, evidence-backed answers, and an audit explorer) and the Razorpay track brief for AI Growth and Agentic Commerce (a merchant transactable by an AI buyer, every money action explainable, bounded and gated, a visible audit trail, and one failure handled gracefully).

### Scope

In scope: `agentpay/` in full, including `apps/api`, `apps/worker`, `apps/web`, `services/`, `packages/`, `pipeline/`, `buyer-agent/`, `infra/` including `infra/migrations`, `tests/`, `docs/`, `Makefile`, `pyproject.toml`, `docker-compose.yml`, and `apps/web/package.json`. The measured file inventory is 163 Python files, 25 `.tsx` files, 5 `.ts` files, and 3 `.js` files.

Out of scope: adding product features that the existing specification `agentpay-commerce-gateway` does not already require, and rewriting subsystems that pass their gates.

### Locked decisions

These were settled before this document and are not reopened by it.

| # | Decision |
|---|---|
| A-1 | Audit with staged remediation. Every defect is recorded. Validated critical and high defects and any blocker are fixed. Affected evidence gates are rerun after each fix. |
| A-2 | Fake and mock providers remain the default. Razorpay test mode and Groq-compatible model checks are separate explicit opt-in gates with credential redaction, test-mode verification, bounded calls and spend, and no real-money path. |
| A-3 | Harden the existing layered guard in `services/agent/guard.py` first. An independent adversarial evaluation harness is added only after provenance, license, compatibility, and supply-chain review, pinned to an exact version. No specific GuardLLM package is assumed or hardcoded. |
| A-4 | Browser testing uses pinned Playwright, Chromium first, full buyer and merchant journeys across desktop and mobile viewports, with keyboard navigation and accessibility checks. Other engines are used only for targeted compatibility findings. |
| A-5 | Secrets in `.env` are never read out, echoed, or copied. Files under `datasets/` are never modified or decompressed, and no new dataset is downloaded. |

## Glossary

| Term | Meaning |
|---|---|
| **AgentPay** | The system under audit: the code in `agentpay/`. |
| **Audit_Program** | The overall audit process defined by this document, including its planning, sequencing, and reporting. |
| **Code_Review** | The file-by-file human-directed inspection activity that produces per-file review records. |
| **Coverage_Manifest** | The enumerated list of in-scope files with a review state for each. |
| **Defect_Ledger** | The durable record of every finding, one entry per defect, with severity, location, reproduction, expected behavior, and status. |
| **Remediation_Process** | The staged fix activity that resolves validated critical and high defects and blockers, then reruns affected gates. |
| **Toolchain_Setup** | The activity that installs and pins dependencies, runtimes, and test tooling needed to execute gates. |
| **Static_Gate** | The build, format, lint, type-check, and import-contract checks for backend and frontend. |
| **Test_Gate** | Execution of the `tests/` suites: unit, integration, contract, security, and evaluation. |
| **Migration_Check** | Verification of Alembic migrations against a real PostgreSQL instance with pgvector. |
| **Correctness_Harness** | The tests that exercise concurrency, idempotency, webhook signatures, and duplicate-charge prevention. |
| **Isolation_Harness** | The tests that exercise tenant isolation, object ownership, and credential scoping. |
| **Guard_Layer** | The layered prompt safety classifier in `services/agent/guard.py`. |
| **Fail-closed** | Treating an inconclusive or errored safety evaluation as unsafe rather than safe. |
| **Adversarial_Harness** | The pinned corpus and runner for prompt-injection, jailbreak, tool-abuse, and SSRF probes. |
| **Live_Check_Gate** | The explicit opt-in gate that performs Razorpay test-mode or model-provider calls against real endpoints. |
| **Offline_Default_Mode** | The default configuration in which `PAYMENT_PROVIDER=fake` and `MODEL_PROVIDER=mock` and no external credential is required. |
| **Journey_Harness** | The pinned Playwright suite that drives buyer and merchant journeys in a real browser. |
| **Accessibility_Audit** | The automated and keyboard-driven accessibility checks run over the storefront and merchant surfaces. |
| **Money_Audit** | The verification that all monetary values are integer minor units and that totals are computed by the deterministic core. |
| **Audit_Trail_Check** | The verification that every consequential action has a persisted, ordered, explainable audit event. |
| **Vision_Conformance_Check** | The measurement of the frontend implementation against the supplied product vision. |
| **Track_Conformance_Check** | The measurement of the implementation against the Razorpay AI Growth and Agentic Commerce track brief. |
| **Doc_Accuracy_Check** | The comparison of README, docs, and Makefile claims against measured behavior. |
| **Data_Safety_Control** | The enforced constraints covering secrets, datasets, and redaction. |
| **Evidence_Report** | The final report that separates verified from unverified claims. |
| **Evidence artifact** | A stored file recording a gate execution: the exact command, exit code, timestamp, and captured output, or a browser trace, screenshot, or video. |
| **Gate** | A check with a determinate pass or fail outcome backed by an evidence artifact. |
| **Blocker** | A condition that prevents a gate from reaching a determinate outcome. |
| **Minor units** | Integer smallest currency denomination, paise for INR. |
| **Critical severity** | Money loss, duplicated charge, unauthorized money movement, cross-tenant data exposure, secret disclosure, or a complete guard bypass. |
| **High severity** | Golden-path failure, silent safety degradation, data corruption, a money action with no audit event, or authorization bypass without money movement. |
| **Medium severity** | Incorrect non-money behavior, missing validation with no demonstrated exploit path, or a WCAG 2.1 Level A or AA violation. |
| **Low severity** | Cosmetic, documentation, naming, or style findings with no behavioral effect. |

---

## Requirements

### Requirement 1: File-by-file review coverage

**User Story:** As an engineering lead, I want every in-scope source file inspected one at a time and the coverage recorded, so that completeness of the review is a measured fact rather than an impression.

#### Acceptance Criteria

1. THE Audit_Program SHALL produce a Coverage_Manifest enumerating every in-scope file under `apps/api`, `apps/worker`, `apps/web`, `services`, `packages`, `pipeline`, `buyer-agent`, `infra`, `infra/migrations`, `tests`, and `docs`, plus `Makefile`, `pyproject.toml`, `docker-compose.yml`, and `apps/web/package.json`
2. THE Coverage_Manifest SHALL record for each file one review state from the set `reviewed`, `deferred`, or `excluded`
3. WHEN a file is recorded as `deferred` or `excluded`, THE Coverage_Manifest SHALL record a written reason for that state
4. THE Code_Review SHALL inspect one file at a time and SHALL record the file path, the reviewed revision identifier, and the defect identifiers raised from that file
5. THE Code_Review SHALL cover, for each Python module, input validation, error handling, transaction boundaries, concurrency assumptions, tenant scoping, and monetary representation
6. THE Code_Review SHALL cover, for each frontend file, state handling, error and empty states, loading behavior, accessibility of interactive elements, and use of server-computed values in place of client-computed money
7. WHEN the Code_Review reaches the end of the Coverage_Manifest, THE Audit_Program SHALL report the count of files in each review state
8. IF a file cannot be read or parsed, THEN THE Audit_Program SHALL record a Blocker entry in the Defect_Ledger naming the file and the failure

---

### Requirement 2: Defect recording

**User Story:** As a reviewer, I want every finding recorded with enough detail to reproduce and fix it, so that no defect depends on memory or conversation history.

#### Acceptance Criteria

1. THE Defect_Ledger SHALL contain one entry per defect with a stable identifier
2. THE Defect_Ledger SHALL record for each entry a severity of `critical`, `high`, `medium`, or `low`
3. THE Defect_Ledger SHALL record for each entry the file path and the line range of the defective code
4. THE Defect_Ledger SHALL record for each entry a reproduction consisting of either an exact command, an HTTP request sequence, or a browser step sequence
5. THE Defect_Ledger SHALL record for each entry the observed behavior and the expected behavior as separate fields
6. THE Defect_Ledger SHALL record for each entry a status from the set `open`, `fixed`, `deferred`, or `not-a-defect`
7. WHERE a defect maps to an acceptance criterion in the `agentpay-commerce-gateway` specification, THE Defect_Ledger SHALL record that criterion reference
8. THE Defect_Ledger SHALL contain an entry for the Guard_Layer Layer 2 fail-open behavior in which `evaluate_meta_llama_guard` returns `is_safe=True` on a transport exception and on a non-200 response
9. WHEN a defect is claimed to be fixed, THE Defect_Ledger SHALL record the identifier of the evidence artifact that demonstrates the corrected behavior

---

### Requirement 3: Staged remediation

**User Story:** As an engineering lead, I want fixes limited to validated high-impact defects and blockers, so that the audit converges instead of turning into an open-ended rewrite.

#### Acceptance Criteria

1. WHEN a defect is recorded with severity `critical` or `high`, THE Remediation_Process SHALL first confirm the defect by executing its recorded reproduction
2. IF a recorded reproduction does not reproduce the defect, THEN THE Remediation_Process SHALL set that entry status to `not-a-defect` and record the executed evidence
3. WHEN a `critical` or `high` defect is confirmed, THE Remediation_Process SHALL implement a fix and add or extend an automated test that fails before the fix and passes after it
4. WHEN a Blocker is confirmed, THE Remediation_Process SHALL resolve the Blocker before the affected gate is reported as passed
5. WHEN a fix is applied, THE Remediation_Process SHALL rerun every gate whose scope includes the changed files and SHALL store the resulting evidence artifacts
6. WHERE a defect has severity `medium` or `low`, THE Remediation_Process SHALL set the status to `deferred` with a recorded rationale, unless the fix is contained within a file already being changed for a confirmed `critical` or `high` defect
7. THE Remediation_Process SHALL keep each fix scoped to the recorded defect and SHALL record any change that extends beyond that scope as a separate entry

---

### Requirement 4: Dependency and toolchain setup with supply-chain review

**User Story:** As a platform operator, I want the audit toolchain installed with pinned versions and reviewed provenance, so that reproducing the audit yields the same result and no unvetted package enters the build.

#### Acceptance Criteria

1. THE Toolchain_Setup SHALL create a Python 3.12 environment and install the project with its `dev` extra from `pyproject.toml`
2. THE Toolchain_Setup SHALL install Node.js 20 or later and the `apps/web` dependency tree, and SHALL record the resolved Node.js and package manager versions
3. THE Toolchain_Setup SHALL replace every caret range in `apps/web/package.json` with an exact version and SHALL commit the resulting lockfile
4. WHEN a new dependency or tool is added for the audit, THE Toolchain_Setup SHALL pin it to an exact version
5. WHEN a new dependency or tool is added for the audit, THE Toolchain_Setup SHALL record its source repository, license identifier, latest release date, and Python or Node version compatibility before installation
6. IF a candidate dependency has an unidentifiable maintainer, an incompatible license, a name resembling an existing popular package, or no release within the previous 24 months, THEN THE Toolchain_Setup SHALL reject that candidate and record the rejection
7. THE Toolchain_Setup SHALL run a dependency vulnerability scan over the Python and Node dependency trees and SHALL record findings by severity
8. THE Toolchain_Setup SHALL verify that PostgreSQL with the pgvector extension and Redis are reachable, and SHALL record the resolved server versions
9. IF a required service is unreachable, THEN THE Toolchain_Setup SHALL record a Blocker naming the service and the connection error with any credential redacted

---

### Requirement 5: Static gates for backend and frontend

**User Story:** As a reviewer, I want format, lint, type, and architecture checks executed with captured output, so that the claim "it builds and lints clean" is backed by exit codes.

#### Acceptance Criteria

1. THE Static_Gate SHALL execute `ruff check .`, `ruff format --check .`, `mypy apps packages services pipeline`, and `lint-imports` and SHALL store an evidence artifact for each
2. THE Static_Gate SHALL execute the Next.js production build, the TypeScript compiler in no-emit mode, and the frontend lint command, and SHALL store an evidence artifact for each
3. THE Static_Gate SHALL record for each check the exact command, the working directory, the exit code, the wall-clock duration, and the captured output
4. WHEN a check exits with a non-zero status, THE Static_Gate SHALL record every reported diagnostic as a Defect_Ledger entry or as a group entry referencing the affected files
5. THE Static_Gate SHALL verify that each import contract declared in `pyproject.toml` is reported as kept, including the contract that forbids `services.agent` from importing `sqlalchemy` or `psycopg` and the contract that forbids `services.payments` from importing `services.agent`
6. THE Static_Gate SHALL verify that every first-party module in the Coverage_Manifest can be imported in isolation without side effects that require a database, a Redis instance, or a network call
7. IF the frontend build depends on an environment variable that is absent from `.env.example`, THEN THE Static_Gate SHALL record that omission as a defect

---

### Requirement 6: Test suite execution gates

**User Story:** As an engineering lead, I want every test suite run and its skips explained, so that a green summary cannot hide untested behavior.

#### Acceptance Criteria

1. THE Test_Gate SHALL execute the `tests/unit`, `tests/integration`, `tests/contract`, `tests/security`, and `tests/evaluation` suites and SHALL store an evidence artifact per suite
2. THE Test_Gate SHALL record for each suite the counts of passed, failed, skipped, errored, and expected-failure results
3. WHEN a test is skipped, THE Test_Gate SHALL record the skip reason and SHALL classify the skip as `environmental`, `opt-in`, or `unexplained`
4. IF a suite reports an `unexplained` skip, THEN THE Test_Gate SHALL record a defect for that test
5. THE Test_Gate SHALL treat a suite as passed only when the failed and errored counts are both zero
6. THE Test_Gate SHALL measure line coverage over `apps`, `packages`, `services`, and `pipeline` and SHALL record the modules with coverage below 60 percent
7. THE Test_Gate SHALL run the unit suite twice with different random orderings and SHALL record any test whose outcome differs between the two runs
8. THE Test_Gate SHALL verify that the full default suite completes with no `PAYMENT_PROVIDER` or `MODEL_PROVIDER` credential present in the environment

---

### Requirement 7: Database migration verification

**User Story:** As a platform operator, I want migrations proven against a real database, so that a clean deployment and a rollback both work before anyone depends on them.

#### Acceptance Criteria

1. THE Migration_Check SHALL apply all migrations to an empty PostgreSQL database and SHALL record the resulting Alembic revision
2. THE Migration_Check SHALL verify that the pgvector extension is created by migration rather than by manual setup
3. THE Migration_Check SHALL downgrade one revision and re-upgrade to head, and SHALL record the outcome of each direction
4. THE Migration_Check SHALL compare the migrated schema against the SQLAlchemy models and SHALL record every table, column, type, nullability, default, index, unique constraint, and foreign key that differs
5. THE Migration_Check SHALL verify that an Alembic autogenerate run against the migrated database produces an empty change set
6. THE Migration_Check SHALL verify that every column storing a monetary amount uses an integer type
7. THE Migration_Check SHALL verify that a uniqueness constraint exists for the idempotency key scope used by the payments service
8. IF a migration fails to apply or to roll back, THEN THE Migration_Check SHALL record the failing revision, the SQL statement, and the database error as a `critical` defect

---

### Requirement 8: Concurrency, idempotency, and duplicate-charge correctness

**User Story:** As a merchant, I want proof that concurrent requests cannot double-charge a buyer or oversell stock, so that money and inventory stay correct under load.

#### Acceptance Criteria

1. WHEN the Correctness_Harness issues concurrent payment requests carrying an identical idempotency key, THE Correctness_Harness SHALL verify that exactly one payment record is created and that every response describes that same payment
2. WHEN the Correctness_Harness issues concurrent payment requests carrying an identical idempotency key and differing request bodies, THE Correctness_Harness SHALL verify that requests after the first are rejected with a conflict outcome
3. WHEN the Correctness_Harness issues concurrent reservation requests against an offer with a single remaining unit, THE Correctness_Harness SHALL verify that exactly one reservation succeeds and that reserved quantity never exceeds available quantity
4. WHEN the Correctness_Harness issues concurrent checkout confirmations for one authorization, THE Correctness_Harness SHALL verify that exactly one order is created
5. THE Correctness_Harness SHALL verify that a payment attempt against an expired authorization is rejected and that no provider charge is initiated
6. THE Correctness_Harness SHALL verify that a payment attempt whose `price_hash` differs from the authorized `price_hash` is rejected before any provider call
7. THE Correctness_Harness SHALL verify that a failed state transition leaves no partially written state, by asserting that the audit event and the state change are either both present or both absent
8. THE Correctness_Harness SHALL verify that every state transition rejected by the checkout state machine leaves the prior state unchanged

---

### Requirement 9: Webhook signature and replay correctness

**User Story:** As a merchant, I want webhook handling to reject forged events and absorb repeats, so that payment state cannot be driven by an untrusted caller.

#### Acceptance Criteria

1. WHEN a webhook arrives with an absent, malformed, or incorrect signature, THE Correctness_Harness SHALL verify that the request is rejected and that no payment or order state changes
2. THE Correctness_Harness SHALL verify that signature comparison uses a constant-time comparison function
3. WHEN the same signed webhook event is delivered more than once, THE Correctness_Harness SHALL verify that the terminal state is identical after each delivery and that exactly one audit event records the state change
4. WHEN signed webhook events arrive in an order that contradicts the recorded lifecycle, THE Correctness_Harness SHALL verify that the recorded state remains the later lifecycle state
5. WHEN a signed webhook carries an unrecognized event type, THE Correctness_Harness SHALL verify that the request is acknowledged and that no state changes
6. WHEN a signed webhook references an unknown payment identifier, THE Correctness_Harness SHALL verify that the response carries a client-error status and that no record is created
7. THE Correctness_Harness SHALL verify that webhook payload bodies persisted for audit have credential and signature fields redacted

---

### Requirement 10: Money integrity in integer minor units

**User Story:** As a merchant, I want every amount handled as an integer in minor units and every total computed by the server, so that rounding and client tampering cannot change what a buyer pays.

#### Acceptance Criteria

1. THE Money_Audit SHALL verify that no monetary value in `apps`, `packages`, `services`, or `pipeline` is stored, computed, or serialized as a binary floating-point number
2. THE Money_Audit SHALL verify that every API response field carrying an amount is an integer in minor units accompanied by an ISO 4217 currency code
3. THE Money_Audit SHALL verify that checkout totals, tax, shipping, and discounts are computed by the deterministic core and that a client-supplied total is ignored
4. WHEN the Money_Audit submits a checkout request with a client-supplied total lower than the server computation, THE Money_Audit SHALL verify that the server total is used and that the discrepancy is recorded
5. THE Money_Audit SHALL verify that mixing two currencies in one monetary operation is rejected with an explicit error
6. THE Money_Audit SHALL verify that a negative or zero payment amount is rejected
7. THE Money_Audit SHALL verify that the frontend formats amounts from integer minor units and performs no arithmetic that alters a payable total
8. THE Money_Audit SHALL verify that the sum of order line amounts equals the order total for every order created during the audit journeys

---

### Requirement 11: Tenant isolation and ownership

**User Story:** As a merchant, I want proof that one tenant cannot read or change another tenant's data, so that multi-tenant deployment is safe.

#### Acceptance Criteria

1. THE Isolation_Harness SHALL verify for every tenant-scoped read endpoint that a credential from tenant A receives a not-found or forbidden outcome for a resource owned by tenant B
2. THE Isolation_Harness SHALL verify for every tenant-scoped write endpoint that a credential from tenant A cannot create, update, or delete a resource owned by tenant B
3. THE Isolation_Harness SHALL verify that repository queries for tenant-scoped tables include a tenant predicate
4. THE Isolation_Harness SHALL verify that an API key is accepted only for the scopes recorded against that key
5. THE Isolation_Harness SHALL verify that an expired, revoked, or unknown API key is rejected
6. THE Isolation_Harness SHALL verify that an authorization approval is accepted only from the buyer principal bound to that authorization
7. THE Isolation_Harness SHALL verify that identifiers in responses do not permit enumeration of another tenant's resources by sequential guessing
8. THE Isolation_Harness SHALL verify that error responses for cross-tenant access reveal no attribute of the target resource beyond its absence

---

### Requirement 12: Guardrail hardening with fail-closed behavior

**User Story:** As a merchant, I want the prompt guard to treat an inconclusive safety verdict as unsafe, so that an outage of the remote classifier cannot quietly weaken protection.

#### Acceptance Criteria

1. WHEN the Guard_Layer remote classifier call raises a transport exception, THE Guard_Layer SHALL return an unsafe assessment carrying an evaluator identifier that names the failure
2. WHEN the Guard_Layer remote classifier returns a status other than 200, THE Guard_Layer SHALL return an unsafe assessment carrying the observed status
3. WHEN the Guard_Layer remote classifier returns a body that cannot be parsed into a verdict, THE Guard_Layer SHALL return an unsafe assessment
4. WHEN the Guard_Layer remote classifier exceeds its configured timeout, THE Guard_Layer SHALL return an unsafe assessment within the configured timeout plus 2 seconds
5. WHERE the deployment configuration enables a permissive guard mode, THE Guard_Layer SHALL degrade to heuristic-only evaluation and SHALL record a degraded-mode event for every request evaluated in that mode
6. THE Guard_Layer SHALL default the permissive guard mode to disabled
7. THE Guard_Layer SHALL include in every assessment the evaluator identifier, the layer that produced the verdict, and the threat category when a verdict is unsafe
8. WHEN the Guard_Layer blocks a prompt, THE Guard_Layer SHALL cause the request to fail with the prompt-injection error code and SHALL write an audit event containing the evaluator identifier and threat category
9. THE Guard_Layer SHALL exclude prompt content, API keys, and endpoint credentials from log records and audit events, retaining a content hash and a length instead
10. THE Guard_Layer SHALL evaluate every natural-language input that reaches the agent surface, and the Audit_Program SHALL record each agent entry point together with the guard call that protects it

---

### Requirement 13: Adversarial guardrail and SSRF testing

**User Story:** As a security reviewer, I want a recorded corpus of hostile inputs run against the agent surface, so that injection and outbound-request abuse are measured rather than assumed to be handled.

#### Acceptance Criteria

1. THE Adversarial_Harness SHALL maintain a versioned corpus containing at minimum direct instruction override, system-prompt spoofing, encoded and obfuscated payloads, multilingual payloads, price and policy manipulation, credential exfiltration, payment-status falsification, tool-argument tampering, and oversized input cases
2. THE Adversarial_Harness SHALL include cases in which hostile instructions are embedded in retrieved evidence, product titles, product descriptions, and review text
3. WHEN hostile instructions arrive inside retrieved evidence, THE Adversarial_Harness SHALL verify that the deterministic outcome for price, policy, authorization, and payment is unchanged from the same journey without the hostile content
4. THE Adversarial_Harness SHALL verify that a blocked prompt produces no tool invocation that mutates state
5. THE Adversarial_Harness SHALL probe every configuration field and request field that accepts a URL with loopback addresses, link-local address 169.254.169.254, private address ranges, non-HTTP schemes, credentialed URLs, and hosts that redirect to a private address
6. WHEN an outbound request target resolves to a loopback, link-local, or private address, THE Adversarial_Harness SHALL verify that the request is refused and the refusal is recorded
7. THE Adversarial_Harness SHALL verify that outbound requests enforce a connect timeout, a read timeout, a response size limit, and a redirect limit
8. THE Adversarial_Harness SHALL record for every corpus case the input identifier, the expected outcome, the observed outcome, and the deciding evaluator
9. THE Adversarial_Harness SHALL report the count of corpus cases whose observed outcome differs from the expected outcome, and each difference SHALL become a Defect_Ledger entry

---

### Requirement 14: Independent adversarial evaluation harness

**User Story:** As a security reviewer, I want an independent evaluator added only after it has been vetted, so that a safety improvement does not become a supply-chain risk.

#### Acceptance Criteria

1. THE Audit_Program SHALL complete the Guard_Layer hardening in Requirement 12 before an independent evaluation package is installed
2. WHEN an independent evaluation package is proposed, THE Audit_Program SHALL record its source repository, maintainer, license identifier, release history, transitive dependency count, and Python 3.12 compatibility
3. IF a proposed evaluation package fails provenance, license, compatibility, or vulnerability review, THEN THE Audit_Program SHALL reject the package and record the rejection reason
4. WHERE an independent evaluation package is adopted, THE Audit_Program SHALL pin it to an exact version and SHALL confine it to the `dev` dependency group
5. THE Adversarial_Harness SHALL reference evaluators through a configuration value and SHALL operate with its default configuration when no independent evaluation package is installed
6. WHERE an independent evaluation package requires network access or a credential, THE Adversarial_Harness SHALL treat that evaluator as an opt-in gate under Requirement 15
7. THE Audit_Program SHALL record the evaluation results from the independent harness separately from the Guard_Layer results, so that agreement and disagreement between them are visible

---

### Requirement 15: Opt-in live provider checks

**User Story:** As a platform operator, I want live Razorpay and model-provider checks behind an explicit gate with hard bounds, so that verification against real endpoints cannot move real money or leak a credential.

#### Acceptance Criteria

1. THE Live_Check_Gate SHALL run only when an explicit opt-in environment flag is set for that specific provider
2. WHILE the opt-in flag is unset, THE Live_Check_Gate SHALL skip with a recorded `opt-in` skip reason and the Offline_Default_Mode SHALL remain in effect
3. WHEN the Razorpay opt-in flag is set, THE Live_Check_Gate SHALL verify that the configured key identifies a test-mode account before any order or payment call is made
4. IF a configured Razorpay credential is not a test-mode credential, THEN THE Live_Check_Gate SHALL abort before the first provider call and SHALL record the abort without the credential value
5. THE Live_Check_Gate SHALL enforce a configured maximum number of provider calls per run and a configured maximum cumulative amount in minor units, and SHALL stop the run when either bound is reached
6. THE Live_Check_Gate SHALL exclude every credential, signature, and token from its evidence artifacts, substituting a fixed redaction marker
7. WHEN the Razorpay live check runs, THE Live_Check_Gate SHALL exercise order creation, payment capture in test mode, signature verification, a webhook delivery, and a refund or void path, and SHALL record the provider identifiers returned
8. WHEN the model-provider opt-in flag is set, THE Live_Check_Gate SHALL verify reachability, request the configured chat model and the configured guard model, and record latency, token counts, and the verdict for one safe and one unsafe probe
9. THE Live_Check_Gate SHALL enforce a per-request timeout and a maximum total request count for model-provider checks
10. THE Live_Check_Gate SHALL verify that the code path used by live checks contains no branch that targets a production payment endpoint

---

### Requirement 16: Offline default posture

**User Story:** As a developer on a clean clone, I want the whole system to run and pass its gates with no credentials, so that anyone can reproduce the audit.

#### Acceptance Criteria

1. THE Offline_Default_Mode SHALL be the configuration produced by copying `.env.example` to `.env` without edits
2. WHEN the stack starts in Offline_Default_Mode, THE Audit_Program SHALL verify that `PAYMENT_PROVIDER` resolves to the fake provider and `MODEL_PROVIDER` resolves to the mock provider
3. WHILE running in Offline_Default_Mode, THE Audit_Program SHALL verify that the complete purchase journey from intent to order succeeds
4. WHILE running in Offline_Default_Mode, THE Audit_Program SHALL verify that no request leaves the host to a third-party endpoint, by recording outbound connection attempts during a full journey run
5. THE Audit_Program SHALL verify that `.env.example` declares every configuration key read by the API, the worker, the pipeline, and the frontend
6. THE Audit_Program SHALL verify that `.env.example` contains no real credential value

---

### Requirement 17: Human-like end-to-end journeys in a real browser

**User Story:** As a judge, I want the product driven the way a person would drive it, so that the demo is proven in a browser rather than only in HTTP tests.

#### Acceptance Criteria

1. THE Journey_Harness SHALL use Playwright pinned to an exact version with pinned browser binaries, and SHALL execute Chromium first
2. THE Journey_Harness SHALL execute a buyer journey covering landing, search, product detail, compare, cart, checkout, authorization approval, payment, order detail, and the order timeline
3. THE Journey_Harness SHALL execute a merchant journey covering the merchant overview, catalog, policy configuration, API usage, and the audit explorer
4. THE Journey_Harness SHALL execute an agent-playground journey in which a natural-language request results in a bounded, gated purchase and the resulting audit trail is visible in the interface
5. THE Journey_Harness SHALL execute every journey at a desktop viewport of at least 1440 by 900 and at a mobile viewport of 390 by 844
6. THE Journey_Harness SHALL introduce a per-action delay and human-like input sequences, including typing character by character into search and form fields
7. THE Journey_Harness SHALL store for each journey a trace, a video, and screenshots at each named step
8. THE Journey_Harness SHALL fail a journey when a console error, an unhandled promise rejection, or a failed network request occurs during that journey
9. THE Journey_Harness SHALL assert that amounts displayed in the interface equal the amounts persisted by the API for the same order
10. WHERE a finding depends on a non-Chromium engine, THE Journey_Harness SHALL run that single case on the other engine and SHALL record the engine in the finding

---

### Requirement 18: Failure and edge-case journeys

**User Story:** As a judge, I want to see the system handle failure without losing money or coherence, so that the bounded-agent claim survives contact with the unexpected.

#### Acceptance Criteria

1. WHEN an offer price changes between checkout creation and payment, THE Journey_Harness SHALL verify that payment is refused, the interface explains the price change, and a fresh authorization is required
2. WHEN the payment provider does not respond within its timeout, THE Journey_Harness SHALL verify that the interface reports a pending outcome, that a retry with the same idempotency key creates no second charge, and that the final state matches the provider outcome
3. WHEN a merchant policy blocks a purchase, THE Journey_Harness SHALL verify that the block is presented with its machine-readable reason code and that no payment is created
4. WHILE the model provider is unavailable, THE Journey_Harness SHALL verify that search, product detail, cart, and checkout remain operable and that the interface states that AI assistance is unavailable
5. WHEN a buyer states constraints that no product satisfies, THE Journey_Harness SHALL verify that the interface reports which constraint eliminated the remaining candidates and offers a specific relaxation
6. WHEN a buyer states two constraints that cannot both hold, THE Journey_Harness SHALL verify that the conflict is named explicitly and that the buyer is asked to choose between the conflicting constraints
7. WHEN an authorization expires before payment, THE Journey_Harness SHALL verify that payment is refused and the interface offers re-authorization
8. WHEN inventory is exhausted between offer selection and checkout, THE Journey_Harness SHALL verify that the interface reports the unavailability and that no charge occurs
9. WHEN a network request fails during a browser journey, THE Journey_Harness SHALL verify that the interface shows an error state with a retry affordance rather than an empty or frozen view
10. THE Journey_Harness SHALL verify that every failure case above appears in the audit trail with a reason code

---

### Requirement 19: Accessibility and keyboard operability

**User Story:** As a buyer using a keyboard and a screen reader, I want the storefront and merchant surfaces operable without a mouse, so that the product is usable and the accessibility claim is measured.

#### Acceptance Criteria

1. THE Accessibility_Audit SHALL run an automated WCAG 2.1 Level A and AA rule set against every route exercised by the Journey_Harness
2. THE Accessibility_Audit SHALL record each violation with its rule identifier, impact level, route, and the selector of the offending element
3. THE Accessibility_Audit SHALL verify that a buyer can complete search, product selection, cart, checkout, and authorization approval using only keyboard input
4. THE Accessibility_Audit SHALL verify that focus order follows the visual reading order on every audited route
5. THE Accessibility_Audit SHALL verify that every focusable element has a visible focus indicator
6. THE Accessibility_Audit SHALL verify that every form control has a programmatically associated label and that validation errors are announced through an accessible status region
7. THE Accessibility_Audit SHALL verify that the AI assistant surface announces streamed responses through a live region and that focus is not stolen from the buyer while a response streams
8. THE Accessibility_Audit SHALL verify that a keyboard user can dismiss every overlay and drawer and that focus returns to the element that opened it
9. THE Accessibility_Audit SHALL verify that text and interactive elements meet the AA contrast ratio thresholds
10. WHEN an automated violation of Level A or AA is found, THE Accessibility_Audit SHALL record it as a `medium` or higher Defect_Ledger entry

---

### Requirement 20: Frontend product-vision conformance

**User Story:** As a product owner, I want the implemented frontend measured against the stated vision, so that gaps between the vision and the build are explicit.

#### Acceptance Criteria

1. THE Vision_Conformance_Check SHALL evaluate the storefront for a coherent human shopping surface covering landing, category, search, product detail, compare, cart, checkout, and order views, and SHALL record any view that is absent or non-functional
2. THE Vision_Conformance_Check SHALL evaluate the merchant and AI control layer for catalog control, policy configuration, API usage visibility, and audit inspection
3. THE Vision_Conformance_Check SHALL evaluate the developer and agent playground for capability discovery, request construction, response inspection, and a visible link from an agent action to its audit record
4. THE Vision_Conformance_Check SHALL verify that contextual AI assistance is reachable from search, product detail, compare, cart, checkout, and order views
5. THE Vision_Conformance_Check SHALL verify that the human storefront and the agent surface obtain prices, availability, policy, and totals from the same deterministic core, by comparing the values returned to each surface for one identical offer
6. THE Vision_Conformance_Check SHALL verify that a shopping session retains structured memory of stated constraints, rejected candidates, and selected items across page navigations within that session
7. WHILE AI assistance is unavailable, THE Vision_Conformance_Check SHALL verify that every commerce action remains available and that the degradation is stated in the interface
8. WHEN the AI narrows candidates to none, THE Vision_Conformance_Check SHALL verify that the interface offers an interactive relaxation of a named constraint and that accepting the relaxation returns candidates
9. THE Vision_Conformance_Check SHALL verify that AI answers about a product cite the field or evidence record they are derived from and that the cited source is inspectable
10. THE Vision_Conformance_Check SHALL verify that the audit explorer lists events for a selected order in causal order with actor, action, reason code, and amount where applicable
11. THE Vision_Conformance_Check SHALL record each vision element as `implemented`, `partial`, or `absent` with the evidence artifact that supports the classification

---

### Requirement 21: Track brief conformance

**User Story:** As a hackathon judge, I want the four track requirements demonstrated by an executed run, so that the submission can be assessed without trusting a checklist.

#### Acceptance Criteria

1. THE Track_Conformance_Check SHALL demonstrate a purchase completed end to end by an AI buyer that holds only the documented public agent credential
2. THE Track_Conformance_Check SHALL verify for every money action in that run that the persisted record names the actor, the authorization that permitted it, the amount in minor units, and the reason for the outcome
3. THE Track_Conformance_Check SHALL verify that each money action is bounded by an amount ceiling, a currency, a category, and an expiry recorded before the action
4. THE Track_Conformance_Check SHALL verify that each money action passes a policy decision and an explicit authorization gate before the provider is called
5. THE Track_Conformance_Check SHALL produce the audit trail for that run as a stored artifact and SHALL show the same trail rendered in the interface
6. THE Track_Conformance_Check SHALL demonstrate at least one failure from Requirement 18 handled without money movement and with a buyer-facing explanation
7. THE Track_Conformance_Check SHALL record the exact commands and browser steps needed to reproduce the demonstration

---

### Requirement 22: Audit-trail completeness and explainability

**User Story:** As a merchant, I want every consequential action to leave an ordered, explainable record, so that any outcome can be reconstructed after the fact.

#### Acceptance Criteria

1. THE Audit_Trail_Check SHALL verify that every state change to a checkout, authorization, payment, order, or inventory reservation writes an audit event
2. THE Audit_Trail_Check SHALL verify that an audit event is written in the same database transaction as the state change it describes, by asserting that a rolled-back state change leaves no audit event
3. THE Audit_Trail_Check SHALL verify that every audit event carries a correlation identifier that links it to the originating request
4. THE Audit_Trail_Check SHALL verify that audit events for one order are retrievable in causal order and that the ordering is stable across repeated reads
5. THE Audit_Trail_Check SHALL verify that every policy decision event carries a machine-readable reason code and the rule version that produced it
6. THE Audit_Trail_Check SHALL verify that every rejection event states the deciding component and the deciding input
7. THE Audit_Trail_Check SHALL verify that audit events exclude API keys, tokens, signatures, and raw prompt content
8. THE Audit_Trail_Check SHALL verify that the audit surface exposed to a merchant returns only events belonging to that merchant
9. THE Audit_Trail_Check SHALL verify that the event count and content shown in the audit explorer match the persisted events for the same order

---

### Requirement 23: Documentation and status accuracy

**User Story:** As a reader of the repository, I want the documentation to match the measured state of the code, so that status claims stop conflicting with each other.

#### Acceptance Criteria

1. THE Doc_Accuracy_Check SHALL compare the README status table against the measured gate results and SHALL record every phase whose stated state differs from the evidence
2. THE Doc_Accuracy_Check SHALL verify that every document path referenced by the README exists, including `docs/architecture.md` and `docs/state-machine.md`
3. IF a referenced document is absent, THEN THE Doc_Accuracy_Check SHALL record a defect naming the referencing file and the missing path
4. THE Doc_Accuracy_Check SHALL execute every `Makefile` target that is safe to run in the audit environment and SHALL record the outcome of each
5. IF a `Makefile` target references a module, script, or file that does not exist, THEN THE Doc_Accuracy_Check SHALL record a defect naming the target and the missing reference, including the `seed` target reference to `pipeline.import_to_postgres`
6. THE Doc_Accuracy_Check SHALL verify that every endpoint documented as public is present in the running API and that every public endpoint in the running API is documented
7. THE Doc_Accuracy_Check SHALL verify that the quick-start instructions in the README succeed as written on the audit machine
8. WHEN documentation is corrected, THE Doc_Accuracy_Check SHALL base the correction on a recorded gate result rather than on an intended state

---

### Requirement 24: Secret and dataset safety during the audit

**User Story:** As a platform operator, I want the audit itself to be safe, so that verification does not leak a credential or damage source data.

#### Acceptance Criteria

1. THE Data_Safety_Control SHALL exclude the contents of `.env` and any other credential file from evidence artifacts, reports, and terminal output
2. WHEN a configuration value is needed for a gate, THE Data_Safety_Control SHALL reference the value by key name and SHALL record only whether the key is present
3. THE Data_Safety_Control SHALL leave every file under `datasets/` unmodified and uncompressed-in-place, and SHALL verify this by comparing file sizes and modification times before and after the audit
4. THE Data_Safety_Control SHALL perform no dataset download
5. THE Data_Safety_Control SHALL verify that structured logs redact credentials, tokens, signatures, and card-like numeric sequences
6. IF an evidence artifact is found to contain a credential-shaped value, THEN THE Data_Safety_Control SHALL replace the value with a redaction marker and SHALL record a `critical` defect
7. THE Data_Safety_Control SHALL restrict every destructive database operation to a database whose name identifies it as an audit or test database

---

### Requirement 25: Final evidence report

**User Story:** As an engineering lead, I want a final report that distinguishes what was proven from what was not, so that the completion claim is honest and decisions rest on facts.

#### Acceptance Criteria

1. THE Evidence_Report SHALL contain a section of verified claims in which each claim references at least one evidence artifact
2. THE Evidence_Report SHALL contain a section of unverified claims in which each entry states why verification did not occur, using one of `blocked`, `out-of-scope`, `opt-in-not-enabled`, or `deferred`
3. THE Evidence_Report SHALL record for every gate the exact command, the working directory, the exit code, the timestamp, and the artifact path
4. THE Evidence_Report SHALL summarize the Defect_Ledger by severity and status, and SHALL list every `critical` and `high` entry individually
5. THE Evidence_Report SHALL state the Coverage_Manifest totals for files reviewed, deferred, and excluded
6. THE Evidence_Report SHALL state the residual risk that remains after remediation, one entry per unresolved defect of `high` severity or greater
7. THE Evidence_Report SHALL derive every status statement from a recorded gate result, and SHALL mark any statement without a gate result as unverified
8. THE Evidence_Report SHALL state the software and service versions used for the audit, including Python, Node.js, PostgreSQL, Redis, browser binaries, and every pinned audit tool
9. WHEN a live opt-in gate was skipped, THE Evidence_Report SHALL state which provider verification is therefore unproven
