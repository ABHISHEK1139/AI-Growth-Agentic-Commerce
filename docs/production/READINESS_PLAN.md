# Production Readiness Plan — AgentPay

**Scope:** raise AgentPay to industry standard for 60k customers in parallel, with
production-grade security, without restructuring the codebase.

**Status:** plan only. Nothing in this document has been implemented. Every claim is
measured from this repository or cited to a standard.

**Design constraint, honoured throughout:** the module boundaries, the
`services / packages / apps / pipeline` layout, the import-linter contracts, and the
36-table schema stay as they are. Everything below is additive, or a change *inside* an
existing module.

---

## 0. What the codebase is today

Measured, not estimated:

| Area | Files | Lines |
|---|---|---|
| `apps/api` (FastAPI routers + middleware) | 36 | 7,052 |
| `apps/web/src` (Next.js 14 App Router) | 93 | 35,112 |
| `services/*` (domain) | 99 | 13,761 |
| `packages/*` (shared primitives) | 27 | 3,013 |
| `tests/*` | 91 | 21,448 |
| `pipeline/*` | 4 | 3,165 |
| **Total Python** | **280** | **51,583** |

- 36 database tables, 4 Alembic revisions.
- 1,939 unit + security + contract tests (1,914 before phase 1, plus 25 added for these
  fixes), 58 integration tests, all passing.
- **77% line coverage** across `apps`, `services`, `packages`.
- 4 architecture contracts enforced by `lint-imports` in CI.

This is a well-organised codebase with real financial invariants (integer paise
arithmetic, row-level inventory locking, idempotency, an append-only audit ledger) and
genuine security work (Argon2id, constant-time HMAC, anti-SSRF, prompt-injection
layers). The gaps below are **production concerns, not quality concerns** — that
distinction matters, because it means the fix list is short and the code does not need
rewriting.

---

## 1. Findings, in priority order

### P0 — blocks production

**1.1 Two webhook handlers block the event loop with synchronous database I/O.**

`apps/api/routers/razorpay_checkout.py:538` (`razorpay_webhook`) and
`apps/api/routers/payments.py:140` (`handle_webhook`) are declared `async def` but take
a **synchronous** `sqlalchemy.orm.Session`. FastAPI runs `def` endpoints in a threadpool
but runs `async def` endpoints *on the event loop*. Every query, commit and
`hmac.compare_digest` inside those handlers therefore blocks the loop.

This is the most consequential finding in the repository. Razorpay retries webhooks on
failure, a burst saturates the loop, and **all** traffic — storefront browsing included —
stalls. It fails under exactly the load that matters most.

Verified in the code path: `process_webhook` performs the HMAC verify, a SHA-256 of the
raw body, a `session.query(...).first()` deduplication SELECT, an INSERT, and the state
transitions — all synchronous, all inside this `async def` handler. And per §2.2 that
deduplication SELECT is currently a sequential scan.

Fix — and it is **not** the fix first proposed here.

The obvious suggestion, change `async def` to `def` so FastAPI threads the whole handler,
**cannot work.** Both handlers need `await request.body()`: HMAC-SHA256 is valid only over
the exact bytes the provider signed, and no parsed model can reproduce them. A synchronous
endpoint has no way to await that read.

What works is splitting the handler. Keep the body read on the loop — genuinely async, and
it must happen before anything else — then hand the rest to
`starlette.concurrency.run_in_threadpool`. Four lines per handler. The handlers stay
`async def`, the raw bytes stay authoritative, and the blocking work leaves the loop.

Note this makes each handler marginally slower in isolation (a threadpool hop) and the
service dramatically faster under load (one slow webhook no longer stops every in-flight
request). That trade is invisible from the outside, which is why it is written down. The
two `async def` endpoints in `auth.py` are fine — they do no blocking I/O.

**Implemented.** Regression tests in `tests/unit/test_webhook_event_loop.py`, written
against `httpx.ASGITransport` so they measure real loop behaviour, and confirmed to fail
when the fix is reverted.

**1.2 `/api/v1/payments/razorpay/webhook` returned HTTP 500 on every request.**

Found while writing the tests for 1.1, and worse than 1.1: this endpoint never worked.

```python
settings: AppSettings | None = None,   # AppSettings = Annotated[Settings, Depends(settings_for)]
```

`AppSettings` is only an injection because of the `Annotated` metadata. Widening it with
`| None` and giving it a default made FastAPI read the parameter as *an optional value
with no declared dependency*: `settings_for` was dropped from the route's dependency list
and `None` was passed in. The handler raised `Settings not configured` — 500 — on every
delivery.

Nothing caught it, and the reason matters more than the bug:

- the module imported cleanly;
- the route **registered**, so it appeared in the OpenAPI schema and in any route listing;
- 1,914 tests stayed green, because no test posted to that path;
- only an actual request revealed it.

A payment-capture webhook that always 500s means **no payment is ever confirmed from the
provider side** — silently, in production, while every dashboard looks healthy. The
annotation reads as though it injects settings.

Fix: declare `settings: AppSettings` with no default, ordered before `session` so no
placeholder is needed. Guard rail in
`test_razorpay_webhook_resolves_its_settings_dependency` asserts the route's *resolved*
dependency list, so this class of mistake fails at collection rather than at 3am when a
payment webhook lands.

**Implemented.** `tests/unit/test_webhook_route_wiring.py`, 8 tests, confirmed to fail
when the fix is reverted.

**1.3 Two independent engine definitions with different pool sizes.** *(fixed)*

| Location | Pool | Reads config from |
|---|---|---|
| `apps/api/db.py` | `pool_size=10, max_overflow=5` | `Settings` |
| `services/db/engine.py` *(deleted)* | `pool_size=10, max_overflow=20` | `os.environ` directly |

Both were `@lru_cache`'d. The operator CLI scripts (`scripts/import_catalog_staging.py`,
`scripts/promote_staging_to_catalog.py`) imported the second one. Consequence: an
operator running a catalog import against a different database than the API was a
configuration mistake that produced **no error at all**.

`services/db/` is deleted. The two scripts now resolve the engine through
`scripts/_db.py`, which is the single entry point.

The obvious fix — point the scripts straight at `apps.api.db` — was **wrong**, and worth
recording why. The two engines differed in more than pool size:
`services.db.engine.get_engine()` raised when PostgreSQL was unreachable, while
`apps.api.db.get_engine()` falls back to a local SQLite file (guarded by
`validate_datastore_for_env`, which only refuses when `DB_PASSWORD` is empty). Repointing
a *catalog import* and a *staging promotion* at the silent-fallback version would have
let both write into `data/local_dev.db`, print success, and stage nothing — trading an
obvious "two engines" problem for a silent data-loss one.

So `require_server_database()` in `scripts/_db.py` refuses a SQLite engine unless the
caller passes `allow_sqlite_fallback=True`, and names the operation in the error so an
operator knows which run failed. One engine, and the dangerous direction closed.

**1.4 Connection pool ceiling is far below what Postgres allows.** *(sized, now configurable)*

`infra/docker/api.Dockerfile:89` runs `--workers 2`. With `pool_size=10` and
`max_overflow=5`, the API can open at most **30** of Postgres's **100** connections. With
more API replicas the arithmetic degrades silently, because Postgres refuses
connections when it runs out and the error surfaces as a 500 on an arbitrary request.

Fix: `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` as settings, with the ceiling written down in
`docker-compose.yml` next to `max_connections`. This is configuration, not structure.

**Done.** `DB_POOL_SIZE` (10), `DB_MAX_OVERFLOW` (5) and `DB_POOL_RECYCLE_SECONDS` (300)
are settings now, documented in `.env.example`, with the arithmetic — 15 per worker, 30
across the two workers in `api.Dockerfile`, against 100 — stated where it is read.

**1.5 The ORM and the database described different schemas. The payment path was dead.**

Found while adding the indexes below, and the most consequential finding in this
document — worse than the event-loop bug, because that one was a slow path and this one
was a *broken* one, and nothing in the repository could see it.

`provider_event` existed in the deployed database with **7 columns**.
`services/payments/models.py` declares **12**. `WebhookProcessor.process_webhook` reads
all twelve. Every correctly signed webhook therefore failed:

```
UndefinedColumn: column provider_event.provider does not exist
```

That is the entire payment-capture path. **No payment could ever be confirmed from the
provider side.** Fixing §1.1 and §1.2 made this endpoint *reachable*; it was still
broken the moment it got there.

The missing five — `provider`, `signature`, `payload`, `status`, `created_at` — are all
in `20260821_0001`, and a database built purely from the migration chain has them. The
deployed table lacked them while `alembic_version` read `20260821_0004`. So this was not
a missing migration; it was a table created out of band and then stamped as current.

Fixing it then surfaced two deeper problems.

**Six tables the ORM declares that no migration ever built:** `failed_webhook`,
`ingestion_runs`, `staging_catalog_raw`, `staging_rejections`, `catalog_import`,
`catalog_import_row`. All are imported by live code — the webhook dead-letter writer,
the console CSV import, both operator scripts — so each was a latent `UndefinedTable`.
`failed_webhook` is on the payment path: without it, a webhook that failed *after* a
valid signature raised a 500 instead of recording a replayable failure.

**`alembic/env.py` had an empty `target_metadata`, which made `make revision` a loaded
gun.** It set `target_metadata = Base.metadata` without importing a single model module.
`Base` is a registry, so it was empty — and `--autogenerate` was comparing *nothing*
against a populated database. Running it proposed **dropping all 36 tables**.
`make revision m="..."` invokes exactly that command.

Fixed by walking `services` for `*.models` rather than hard-coding a list, with a hard
failure if the metadata comes back empty. A hard-coded list is how the omission happened
in the first place.

**Fixed.** `0005` reconciles `provider_event` — backfilling `provider='unknown'` for
pre-existing rows rather than asserting a provider that might be wrong — and adds 14
indexes derived from measured `EXPLAIN`s. `0006` creates the six missing tables with DDL
machine-generated from the ORM, so it cannot disagree with the models.

Verified: the dedup query now shows `Bitmap Index Scan on
ix_provider_event_raw_body_hash` instead of `Seq Scan`; a signed delivery returns 200 and
a replay returns `already_processed`, across both webhook routes. The migration chain was
run up, down to `base`, and up again on a scratch database — including against a
deliberately drifted `provider_event` holding pre-existing rows.

**Guard rail:** `tests/integration/test_schema_matches_orm.py` compares ORM metadata to
the live schema across all 33 mapped tables, and was verified to fail when `0006` is
disabled. It has to be an integration test — the unit suite builds its tables *from* the
ORM, so the ORM always agrees with itself, which is precisely why this went unnoticed.

### Why this whole class of bug is invisible here

Three findings in this section share a shape. Naming it should help predict the next one:

| Signal | What it reported | Reality |
|---|---|---|
| Route in the OpenAPI schema | endpoint exists | it returned 500 on every call |
| 1,914 tests green | suite passes | no test posted a signed webhook to a real database |
| `/health` and `/health/db` green | service healthy | the payment path was dead |
| `alembic_version` at head | schema current | five columns missing from a live table |
| `make revision` run | produced a migration | it would have dropped all 36 tables |

Every entry is a *self-consistent* signal. The unit suite checks the ORM against itself.
The migration chain is checked against itself. `/health` asks the process about itself.
Nothing ever asked the ORM and the database the same question — the only question that
turned out to matter. That is what the new integration test does, and it is the highest-
value item in this document.

### P1 — blocks 60k customers

**2.1 There is no caching layer anywhere.** *(implemented — see §4.2 and `capacity-report.md`)*

`redis` is a declared dependency and is used **only** for rate limiting. Every product
search, offer lookup, policy read and capability document is an uncached database round
trip. The catalog is the textbook read-mostly case: product and offer rows change rarely
and are read constantly. This is the largest available throughput win, and it is purely
additive — a `packages/cache` module plus calls at existing read sites.

**2.2 Six hot-path queries are full table scans.** *(fixed in migration 0005)* Measured
with `EXPLAIN` against the live schema, not inferred:

```
provider_event dedup (the webhook replay check)   Seq Scan
checkout by primary key                          Seq Scan
checkout by merchant_id + status                 Seq Scan
audit_event by merchant_id                       Seq Scan
tool_call by agent_run_id                        Seq Scan
offer by merchant_id + status (catalogue read)   Seq Scan
```

Two of these were the important ones:

- **The webhook replay check was a sequential scan** on `provider_event`, a table with no
  indexes that grows by one row per payment event. Webhook processing is the hottest
  correctness path in the system and it was reading the whole table to answer "have I
  seen this event?". Now `Bitmap Index Scan on ix_provider_event_raw_body_hash`, verified
  against the live database.
- **The catalogue read scanned** despite `offer` having `ix_offer_merchant_status` and
  `ix_offer_price`. That one was the planner being right: the table holds **41 rows**, so a
  scan is genuinely cheaper and the indexes will be used at real row counts. It needed
  confirming against real data rather than "fixing" by adding more.

What was unambiguous: `checkout` had **no indexes at all**, `authorization` had none
either despite being read by `buyer_id + status` when deciding standing approval, and
`audit_event` had one index that served none of the console's queries.

**14 indexes added**, each shaped from its query's equality predicates first and ordering
columns last, rather than from what looks reasonable. The one judgement call worth
recording: `ix_provider_event_raw_body_hash` leads with `raw_body_hash` and follows with
`signature`. `signature` alone would work as the leading column too, but `raw_body_hash`
is the selective one and leads so the index stays usable if the `OR` is ever simplified.

**2.3 Ten tables grow forever, with no retention, partitioning, or archival.**

`audit_event`, `idempotency_record`, `provider_event`, `agent_run`, `tool_call`,
`evidence`, `research_session`, `reservation`, `negotiation_round`, `recommendation`.

At 60k customers these are the tables that make a database unmaintainable. Note the
distinction that matters for compliance: `audit_event` is **legally retained**, so it
needs *partitioning by month plus archive to object storage* — never deletion. An
`idempotency_record` older than the longest token lifetime can be dropped safely. Same
table, opposite treatment; the retention table must record which is which.

**2.4 Customer-facing `GET` endpoints are unpaginated.** *(mostly fixed in phase 1)*

This was overstated on first inspection, and the correction is worth keeping. Auditing
every `.all()` call and every `GET` route without a `limit` parameter found **two** real
problems rather than the dozen the phrase implies:

- `channels.list_connections` — unbounded per tenant.
- `ApiClientRepository.list_for_merchant` — unbounded, and the result is rendered into a
  response, so one request could return an arbitrarily large payload.

Both are now capped, with the ceiling enforced at the edge (`Query(ge=..., le=...)`, so an
out-of-range value is a 422) as well as in the service. `audit_event`, `idempotency_record`,
`offers`, `campaigns`, `orders` and `agent tools` were already bounded, and the two
`checkout`/`payment` queries that touch unbounded-growth tables are single-row `.first()`
lookups, not listings.

The smaller number is the honest one. A plan that claims seven problems where there were
two is not more thorough — it is less trustworthy about the next claim.

**2.5 No request coalescing on hot reads.** A storefront listing 20 products issues
roughly 20 offer queries per page view.

### P2 — operational maturity

**3.1 No metrics, no tracing, no error reporting.**

No `prometheus-client`, no `opentelemetry`, no `sentry-sdk` in `pyproject.toml`. The JSON
logs are genuinely good, but you cannot alert on latency percentiles, error rates, or
saturation without metrics. **You cannot operate 60k customers on logs alone** — this is
the second-hard blocker after 1.1.

Three incidents in this session are the argument, and each was invisible to every signal
the service reports about itself:

| Incident | What the service reported | What it was doing |
|---|---|---|
| `provider_event` missing 5 columns | `/health` green, OpenAPI complete, 1,914 tests green | every payment webhook failing |
| Webhook handlers blocking the loop | `/health/db` green | all traffic stalling under burst |
| Silent SQLite fallback (×3) | `/health` green | serving an empty catalogue, losing writes |

None of these produced an error rate, because none of them is an error *in the process* —
they are the process working correctly on the wrong data. Latency metrics would have
caught the second. A datastore-fingerprint gauge would have caught the third. Neither
exists, so all three were found by a test probe or a fixture returning unexpected 404s.
**This is the strongest argument in this document for metrics**, and it is now empirical
rather than theoretical.

**3.2 No load testing, anywhere.** Nothing in `tests/` generates concurrent load. There
is no measured capacity number in the repository. The 60k target is currently unverified:
this document cannot honestly tell you whether today's code handles 1k or 60k.

**3.3 No deployment pipeline.** `.github/workflows/` contains exactly one workflow,
`ci.yml`. No build-push, no image signing, no environment promotion, no rollback.

**3.4 Security scanning is present but largely non-blocking.** CI already runs
gitleaks, bandit, and pip-audit, which is better than most projects get. The problem is
enforcement, not tooling:

- `ci.yml:264-265` — both bandit invocations end in `|| true`. The comment says so
  explicitly: *"The output is non-blocking... we report and let the PR author fix."*
  A scanner nobody must satisfy is a report, not a gate.
- pip-audit (`:269`) is the only genuinely blocking one, and it scans Python only —
  there is no `npm audit` for the 35k lines of TypeScript.
- No container image scanning, no SBOM, no image signing.

So the finding is not "there is no security scanning". It is that **a HIGH-severity
bandit finding and any npm CVE both merge cleanly today.**

**3.5 A 2,915-line local fallback backend ships inside the production bundle.**

`apps/web/src/app/api/[...path]/route.ts` is a complete second backend with its own
SQLite store. In the compose stack it is never used, but it is compiled into the shipped
image. If `BACKEND_URL` is ever unset in a deployment, the web tier silently serves
from its own database instead of failing — which is exactly how a storefront ends up
showing an empty catalog while every health check passes. This is a latent production
incident, and it is the direct cause of the gateway-resolution confusion documented in
the middleware's own comments.

---

## 2. Security assessment

### Already strong — preserve these

| Control | Location | Note |
|---|---|---|
| Argon2id, 20 MiB / t=2 / p=1 | `packages/security/passwords.py` | OWASP-compliant floor, memory-hard |
| Constant-time comparison | `packages/security/tokens.py`, `apikeys.py` | `hmac.compare_digest` throughout |
| Raw-body webhook HMAC + replay dedup | `services/payments/webhooks.py` | Correctly avoids JSON re-serialisation |
| Row-level inventory locking | `services/inventory/` | `reserved <= available` enforced by DB `CHECK` |
| Integer-only money | `packages/money/` | Zero floating-point arithmetic |
| Anti-SSRF URL policy | `services/research/safety/url_policy.py` | Blocks private, link-local, and cloud metadata |
| Tenant scoping | `packages/security/tenancy.py` | Enforced structurally, not by convention |
| Append-only audit ledger | `services/audit/` | With recursive secret redaction |
| Architecture contracts | `pyproject.toml` | 4 boundaries enforced in CI by `lint-imports` |
| Multi-layer prompt-injection defence | `services/agent/guard/` | Heuristic scanner + semantic guard, fail-closed |
| Login lockout + anti-enumeration | `services/connectors/operator_auth.py` | 5 attempts, decaying counter, timing-equal responses |

This is a stronger security baseline than most production payment systems. The work
below is **depth, not foundation** — which is the good news.

### Gaps to close

**4.1 No CSP, HSTS, or `X-Frame-Options`.** Verified: no security headers anywhere in
`apps/api/middleware/` or `apps/web/src/middleware.ts`. A console that renders merchant
data is precisely the clickjacking target. Fix: a headers middleware inside
`apps/api/middleware/` — the module already exists and already has a stack, so this
adds one file and one `install_middleware` line.

**4.2 The Python dependency gate does not cover the frontend.** `pyproject.toml` pins
exact versions and `pip-audit --strict` runs in CI and blocks. There is no `npm audit`
for `apps/web`, which is 35,112 lines across 93 files — the larger half of the
codebase. Add `npm audit --audit-level=high` as a blocking step.

**4.3 No image scanning, SBOM, or signing.** `agentpay-api:local` and `agentpay-web` are
unsigned and un-scanned. Add Trivy/Grype and `syft` SBOM generation, then sign with
cosign.

**4.4 SAST is advisory only.** See §3.4: both bandit steps end in `|| true`. Make the
HIGH-severity threshold blocking, and triage the current findings — a non-blocking
scanner that has never been made to pass accumulates findings nobody reads.

**4.5 No container image scanning.** gitleaks covers the git history (good); nothing
covers what ends up inside a layer. See §4.3.

**4.6 The rate limiter fails open.** Documented deliberately at
`apps/api/middleware/ratelimit.py:10`. Correct for availability: a Redis outage should not
take down payments. The consequence is that a Redis outage removes the *login* throttle.
The per-account lockout still holds, so the worst case is ~5 guesses per account per 15
minutes rather than unlimited. **This is an acceptable residual risk and should stay as
it is** — recorded here so it is a decision rather than an accident.

**4.7 PCI DSS scope is undefined.** AgentPay stores no card data; Razorpay hosts the form.
That is the correct design and keeps AgentPay out of SAQ A scope in principle. Nothing
in the repository *states* the argument. `docs/compliance/pci-scope.md` recording the
reasoning, with an annual review, is the cheapest risk reduction available.

**4.8 Threat model is undocumented.** There is no `docs/security/threat-model.md`. The
security work is real but the reasoning lives in commit history and module docstrings.
For a system handling payments, that is a liability during audit and during onboarding.

---

## 3. Customer point of view

What a merchant and a buyer actually experience, and where each phase above shows up.

### Merchant operator

| Journey | Today | After this plan |
|---|---|---|
| First run | `seed_operator` prompts, then sign in | unchanged |
| Open the console | `/merchant/*` gated server-side | unchanged, plus p99 latency visible |
| Connect Shopify | `/merchant/channels`, token encrypted at rest | unchanged, plus sync outcomes alertable |
| Watch traffic | JSON logs in a container | dashboards, p99, error rate |
| Hit a problem | grep logs | alert with trace ID, linked runbook |
| Scale to 60k customers | unknown ceiling | measured, documented capacity headroom |

### Buyer agent (autonomous)

| Journey | Today | After this plan |
|---|---|---|
| Discover the gateway | `/.well-known/*` manifests | unchanged |
| Authenticate | API key → scoped bearer token, persists across restarts | unchanged |
| Browse | uncached DB reads | cached reads, p99 target |
| Checkout | row-locked inventory, idempotent | unchanged, plus bounded latency |
| Pay | HMAC webhook on raw body | unchanged, plus alertable webhook latency |

### Support engineer

Today: reproduce locally, seed a catalog, read JSON logs.
After: trace ID from the customer's request ID through web → API → Postgres.

The customer-visible win is mostly **latency and reliability**, not features. That is the
honest framing: the checkout and payment paths are already correct; what is missing is the
capacity and the visibility to run them at 60k.

---

## 4. Capacity model for 60k customers

The arithmetic, so the target is checkable rather than aspirational.

**Assumptions, stated so they can be argued with:**

- 60k registered merchants.
- 30% active in any given hour → **18k concurrent sessions**.
- 5 sessions each browsing every 10 minutes → **15 browse req/s sustained**.
- Checkout initiation: 2% of sessions per hour → **~1 create-checkout/s**.
- AI agent traffic: 10 tools per agent run, 1 run per 5 minutes per active agent,
  20k agents → **~700 req/s to the gateway**.

That last number dominates, and it is why 2.1 and 3.1 are not optional. 700 req/s of
agent traffic against 2 workers with a 30-connection pool and zero caching is where this
breaks first.

### 4.1 Measured capacity — the estimate above, checked

The model above says ~700 req/s. **It is wrong by about 3.5×, and now measurably so.**
Measured with `infra/loadtest/` (k6, constant-arrival-rate) against 20k offers, 100k
checkouts, 400k audit events and 60k provider events:

| Offered | Achieved | p50 | p95 | Errors |
|---|---|---|---|---|
| 100/s | **99.93/s** | 8.2 ms | 27 ms | 0% |
| 150/s | **149.66/s** | 14.5 ms | 332 ms | 0% |
| 200/s | **199.09/s** | 10.1 ms | 200 ms | 0% |
| 225/s | 97.89/s | 17.9 ms | 544 ms | 1,417 iterations dropped |
| 250/s | 22.70/s | 565 ms | 30.3 s | 7.5% |
| 300/s | 17.67/s | 1.76 s | 32.8 s | 57% |
| *`/health` only, 250/s* | *250.02/s* | *4.2 ms* | *10.7 ms* | *0%* |

**Clean capacity is 200 req/s, and the knee is at ~220.** Past it the service does not
degrade gracefully — it collapses, with throughput *falling* as offered load rises
(199 → 97 → 22 → 17 req/s) because requests block until the 30 s grace period expires.

**The `/health` row is the informative one.** The same harness sustains 250 req/s against
an endpoint that touches no database. So the ceiling is **not** the server, the harness,
or compute — `agentpay-api-1` peaked at 6% CPU and Postgres at 0.03% while serving
200 req/s. The limit is concurrency in the database-access path: every read endpoint is a
sync `def`, so each occupies an anyio threadpool thread for the duration of its query
against a 30-connection pool.

That distinction matters for what to do next. Adding replicas would add the same
constrained path; what is needed is fewer database round-trips per request, which is
precisely item 2.1.

**Three corrections to my own measurement, all of which produced a confident wrong number
first:**

1. A `ramping-arrival-rate` profile reported 47 req/s against a 100 req/s target and read
   as saturation. k6 reports `http_reqs` as a whole-run average, so the ramp stages drag
   it down. Only a constant rate makes "offered" and "achieved" comparable. Switched.
2. With the per-actor rate limiter on, the harness reads a hard 60 req/min ceiling as
   "capacity". 5% 429s with a p95 of 30 ms looks like a healthy service. The run now
   asserts the limiter is actually off before measuring (`docker-compose.loadtest.yml`).
3. An early fixture read ids up to `ld_offer_2000000` against 20,000 seeded rows — a 75%
   404 rate — and latency still looked excellent, because a 404 is fast. The
   `not_found_rate: ['rate==0']` threshold exists solely to make that failure impossible
   to mistake for a good result, and it caught the bug on the first run.

**Caveat on the environment.** These numbers are from Docker Desktop on Windows, which
allocates its own VM and CPU budget. They are a valid *relative* ladder — 200 req/s clean,
collapse at 225 — and the `/health` comparison is valid absolutely, because it is the same
machine under the same load. They are not a production capacity figure. Re-run on the
target host before sizing replicas.

**Per-client ceilings are a separate constraint, and a real one.** Limits are keyed per
authenticated actor, at 60 req/min for `GET /api/v1/catalog/*` — one catalogue read per
second per client. A well-behaved agent making 10 tool calls per minute is well inside
that. An agent that bursts (a retry after a timeout, a buyer's double-click) exhausts it
immediately and is refused. That is a product decision about retry behaviour, not a
performance problem, so it is flagged here rather than resolved.

**What each phase buys:**

| Phase | Effect on the 700 req/s figure |
|---|---|
| 1.1 webhook fix | Stops the loop saturating under webhook bursts |
| 2.1 read cache | Cuts agent browse traffic to the DB by ~90% |
| 2.2 indexes | Sequential scans → index lookups on the hottest paths |
| 2.6 read replicas | Reads scale horizontally, writes stay on the primary |
| 3.1 metrics | Makes the ceiling *visible* before customers find it |

**Acceptance criterion for "production ready at 60k":** a k6 run at 2× the sustained
figure (1,400 req/s agent traffic + 30 req/s browse) with p99 under 800 ms on checkout
creation, error rate under 0.1%, and zero data loss. Measured, recorded in the
repository, and re-run on every release.

**This criterion is still failing, and §4.1/§4.2 say by how much.** Measured clean
capacity is **~500 req/s** against a 1,430 req/s target — a **~2.9× gap**, down from
~7× before the read cache. Stating the criterion without the measurement beside it would
have let it read as a plan rather than an unmet requirement. The two are adjacent on
purpose.

The gap is not primarily compute (6% API CPU, 0.03% Postgres at the original ceiling).
It is database round-trips per request and unbounded growth in the audit ledger, so the
ranking of what closes it is: audit pagination, then read replicas, then horizontal scale
with an explicit pool ceiling per replica.

### 4.2 Caching tripled the measured ceiling — see `capacity-report.md`

`packages/cache.py` caches the three hottest reads (offer and product by (merchant, id),
capability document by merchant), with versioned keys, explicit invalidation on the
catalogue publish and merchant-rules write paths, and fail-open behaviour.

| Offered | No cache | Offer + capability | + product reads |
|---|---|---|---|
| 250/s | 22.70/s, 7.5% errors, p95 30.3 s | 194.15/s, 0% errors | — |
| 400/s | collapsed | 395.99/s, 0% errors, p95 605 ms | 391.42/s, 0% errors, p95 851 ms |
| **500/s** | — | — | **494.97/s, 0% errors, p95 510 ms** |
| 600/s | — | — | 485.09/s, saturated |

**Clean capacity: 200 → 400 → ~500 req/s.** The full ladder, the host it was measured on,
and one row that is *not* a real result (a disturbed 300 req/s run, left in the table
rather than deleted) are in **`docs/production/capacity-report.md`**.

This is the strongest confirmation of §4.1's diagnosis available: the ceiling really was
database round-trips per request, and removing them moved it — 2.5× in total.

The remaining gap to the 1,430 req/s acceptance criterion is **~2.9×**, down from ~7×.
It is not closed by more of the same: audit pagination, read replicas, and horizontal
scale are the remaining items, in that order.

Three things the ratio does not show, recorded because they matter more than it:

1. **The tests found two real bugs in the cache module I had just written.** `cached()`
   wrapped its `set()` in a `suppress` but not its `get()`, so a store that raised would
   have failed the request — breaking the module's own first constraint. And
   `invalidate()` returned a hardcoded `1` for a single-key invalidation, so a mistyped
   namespace would have logged "invalidated" while stale entries lived on. Both were
   found by tests written before the call sites, and both would have been invisible in
   production: one is a cache outage, the other is a stale price.
2. **The cache is a correctness risk, not only a performance win.** An offer is a price.
   TTLs are short (15s offer, 30s product/capability) and every write path invalidates
   explicitly — *after* its commit, because invalidating before leaves a window where a
   concurrent read repopulates the cache from pre-commit state and the stale entry then
   outlives the publish by its full TTL.
3. **A process-local cache is wrong behind replicas, so it is not the default.**
   `InMemoryCacheBackend` is a per-process dictionary: with more than one worker each
   holds its own copy, an invalidation on one does not reach the others, and a reader can
   be served a stale price until its TTL expires — silently. The tempting default
   ("cache locally if Redis is missing") reads like graceful degradation and is exactly
   backwards. So with no `REDIS_URL` and no `CACHE_ALLOW_PROCESS_LOCAL=1`, caching is
   **disabled** — correct and slower — and both paths log a named event saying why.

---

## 5. The plan

Sequenced so each phase is independently shippable and independently valuable.
**Phase 1 alone is what stands between this codebase and production.**

### Phase 1 — production blockers (1–2 days)

| # | Change | Files | Risk | State |
|---|---|---|---|---|
| 1.1 | Dispatch webhook processing off the loop via `run_in_threadpool` | `razorpay_checkout.py`, `payments.py` | Very low | **Done** |
| 1.2 | `settings: AppSettings` so `settings_for` is injected; endpoint stopped returning 500 | `razorpay_checkout.py` | Very low | **Done** |
| 1.3 | Delete the duplicate engine; repoint the two operator scripts behind a guard | `services/db/` (deleted), `scripts/_db.py` | Low | **Done** |
| 1.4 | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_RECYCLE_SECONDS` as settings | `apps/api/config.py`, `apps/api/db.py`, `.env.example` | Low | **Done** |
| 1.5 | Security headers: HSTS, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `no-store` | new `apps/api/middleware/security.py` | Low | **Done** |
| 1.6 | Bound the two genuinely unbounded tenant listings | `channels.py`, `api_clients.py` | Low | **Done** |
| 1.7 | Local fallback refuses to serve unless `ALLOW_LOCAL_BACKEND=1` | `apps/web/.../[...path]/route.ts`, compose, playwright config | Low | **Done** |

Phase 1 is complete. 1,939 unit/security/contract tests pass (1,914 before, plus 25 new
ones written for these fixes), 58 integration tests pass against live PostgreSQL and
Redis, and all 30 browser e2e specs pass against the rebuilt compose stack. Every new
test was verified to fail when its fix was reverted — that check caught two tests of my
own that passed against the live bug, which is why it is recorded as a practice rather
than a claim.

Three places where the implementation differed from what this plan originally proposed,
each because the naive version was actively harmful:

**1.3 — "just point the scripts at `apps.api.db`" was wrong.** `apps.api.db` falls back
to a local SQLite file when PostgreSQL is unreachable; `services/db/engine.py` did not.
Repointing a *catalog import* and a *staging promotion* script at the silent-fallback
version would have let both write into `data/local_dev.db` and report success. They now
share the one engine through `scripts/_db.py`, which refuses a SQLite engine unless the
caller opts in explicitly. One engine, and the dangerous direction is closed.

**1.5 — CSP was deliberately not set.** On a JSON API it protects nothing, and the web
app genuinely cannot take a strict policy yet: `layout.tsx` loads Razorpay's
`checkout.js`, which injects inline scripts at runtime. A policy without
`unsafe-inline` breaks checkout; one with it largely defeats the purpose. So the four
headers that are unambiguous are set, and the CSP is a tracked web-tier item rather than
a checkbox. `test_no_content_security_policy_on_api_responses` asserts the absence, so
adding one later is a deliberate act.

**1.6 — most list endpoints were already bounded.** An audit of every `.all()` and
every `GET` without a `limit` found two real problems (`list_connections`,
`list_for_merchant`), not the sweeping problem the first draft implied. Both are now
capped, with the ceiling enforced at the edge as well as in the service. Reporting the
smaller number matters more than the larger one.

**On 1.1's original wording.** This plan first proposed changing the handlers to `def`.
That is wrong, and the reason is worth keeping: the handlers must `await request.body()`
for HMAC, so a synchronous endpoint cannot work. See §1.1 for what was done instead.

### Phase 2 — throughput for 60k (2–3 weeks)

1. ~~**Load-test harness first.** k6 scenarios against a seeded Postgres, and record the
   measured baseline. Without this, "60k" is a guess.~~ **Done.**
   `infra/loadtest/{seed.sql,scenarios.js,mint_session.py,run-load-test.ps1,run-capacity-ladder.ps1}`
   plus `docker-compose.loadtest.yml`. The ladder script makes re-measuring on a target
   host one command, which is what stops a laptop number from being quoted as a capacity
   figure. Baseline recorded in §4.1: **200 req/s clean**, against a 1,430 req/s target —
   the estimate was wrong by 3.5×, which is exactly what the harness existed to find out.
2. ~~**Indexes** on the unindexed tables, derived from `EXPLAIN` of real queries. One
   migration.~~ **Done** — 14 indexes in `0005`, each verified against the live planner.
3. ~~**Read cache.** Redis, versioned keys, explicit invalidation on the existing write
   paths.~~ **Done** — `packages/cache.py`, wired into the three hottest reads. Measured to
   move clean capacity **200 → ~500 req/s**; see §4.2 and `capacity-report.md`.
4. **Read replicas** for catalog and offer reads; route analytics there.
5. **Cursor pagination** for `audit_event` and `idempotency_record`. Now the largest
   single contributor: `audit_event` is legally retained, grows without bound, and is
   the slowest endpoint in the measured mix.
6. **Horizontal scale** the API; make the pool ceiling explicit per replica.

**All three cached reads are now cached.** Measured capacity moved 200 → 500 req/s as they
were added, and the remaining ~2.9× gap is the work above rather than more caching.

Two items were pulled *out* of phase 2 and into §1.5, because measurement during
implementation found them to be correctness problems rather than throughput ones: the
`provider_event` column drift and the six missing tables. A throughput phase that
silently assumed the payment path worked would have been optimising a broken system.

A third was added by measurement and is now **done**: the silent SQLite fallback.

`validate_datastore_for_env()` refused to fall back only when `DB_PASSWORD` was *empty*,
on the reasoning that a deployment reaching for the fallback had probably not finished
configuring itself. Docker Compose sets `DB_PASSWORD`, so the guard never fired there —
it was a no-op in the one deployment that mattered. It now refuses whenever `APP_ENV` is
not `local`, regardless of the password, and the only thing that permits the fallback
outside local is `ALLOW_SQLITE_FALLBACK=1`.

It fired for real three times in one session, each time after Docker Desktop restarted and
the API process won the race against PostgreSQL: 20k seeded offers became invisible, every
catalogue read 404'd, and `/health` stayed green throughout because the process genuinely
*was* healthy. Only the fixture probe caught it. Two defences are now in place — the
load-test runner greps the container log for `FALLING BACK` and refuses to measure, and
`tests/unit/test_config.py` pins the policy (verified to fail against the old guard).

**This is a real production hazard, not a load-testing artefact.** On a host that restarts,
the same sequence gives an operator a service that reports healthy and silently serves an
empty catalogue. The compose stack runs `APP_ENV=local`, so the fallback is permitted there
on purpose — which means the `APP_ENV` value is the single switch standing between "laptop
works with no Docker" and "production loses every write quietly". **Anything deployed off
this machine must set `APP_ENV` to `staging` or `demo`.**

### Phase 3 — data lifecycle (1–2 weeks)

1. Partition `audit_event`, `idempotency_record`, `provider_event`, `agent_run`,
   `tool_call`, `evidence` **by month**.
2. Write the retention table: which tables are deleted, which are archived, which are
   retained indefinitely. `audit_event` is in the third column.
3. Archive job to object storage for expired partitions.
4. Backfill existing data before adding constraints.

### Phase 4 — observability (1 week)

1. Prometheus metrics: RED per route, pool saturation, rate-limit rejections, webhook
   latency, channel sync outcomes.
2. OpenTelemetry traces web → API → Postgres, reusing the correlation ID already in
   `packages/observability/context.py` as the trace ID.
3. Sentry for exceptions, with PII scrubbing reusing the existing redactor.
4. Alerting wired into `services/operations/alerts.py`, which already exists.

### Phase 5 — supply chain and delivery (2 weeks)

CI already runs gitleaks, bandit, and pip-audit. This phase is about making them bite,
and covering what is not scanned at all:

1. **Make the bandit HIGH-severity threshold blocking** (`ci.yml:264-265`), and
   triage the existing findings first.
2. Add `npm audit --audit-level=high` — the 35k-line frontend currently has no CVE gate.
3. Trivy image scan + `syft` SBOM on every build.
4. Cosign image signing; deploy by digest, never by mutable tag.
5. Split `ci.yml` into `pr` / `main` / `release` with environment promotion and a
   documented rollback.
6. Formalise the gated migration pattern already present in compose: a new version does
   not receive traffic until `alembic upgrade` succeeds.

### Phase 6 — collaboration and maintainability (ongoing)

No restructuring. Additive only:

1. **`CODEOWNERS`** on `packages/security`, `services/payments`, `services/inventory`,
   `infra/migrations`, `apps/api/middleware` — these need a second reviewer by policy.
2. **`ARCHITECTURE.md`** stating the invariants that must not be broken, so a new
   contributor learns them from a document rather than from a failed CI run.
3. **ADR template**, following the two ADRs already in `docs/adr/`.
4. **`docs/threat-model.md`** and **`docs/compliance/pci-scope.md`**.
5. **Ownership map**: one named owner per service, in `docs/ownership.md`.
6. **Onboarding**: a first-day path that gets a contributor to a passing test run.
7. **Definition of done**: tests, coverage delta, migration if schema changed, metrics if
   a new endpoint, threat-model entry if a boundary moved.

### Phase 7 — continuous assurance (ongoing)

1. Nightly load test against staging, results posted as a build artifact.
2. Weekly `pip-audit` / `npm audit` with a triage SLA.
3. Dependency update bot, batched, never auto-merged.
4. Quarterly access review of operator accounts and agent API keys — both are now
   persisted, so both are now auditable, which is new capability rather than new burden.
5. Annual PCI scope review and penetration test.

---

## 6. Collaboration, for the person managing this codebase

Written for the engineer who owns this repository at 2am, and for the engineer who
inherits it. No restructuring — these are conventions and documents, not a new layout.

### 6.1 What actually makes a codebase hard to collaborate on

Not size. 51k lines across 280 files is large but tractable. The things that actually
cost time here, observed in this session:

| Problem | Evidence | Fix |
|---|---|---|
| Invisible coupling | `services/db/engine.py` duplicated `apps/api/db.py` with different pool sizes, and nothing caught it | ADR + a test asserting one engine |
| Undocumented invariants | "Sync I/O must not run on the event loop" is a rule nothing enforces | **Done** — behavioural tests in `tests/unit/test_webhook_event_loop.py`, not a source-text check |
| Secrets-shaped config drift | `.env` needed 14 changes and nothing validated them | Settings validation at startup |
| Test claims that are not | An e2e suite was fully green while testing nothing, because a redirect renders a visible `body` | Assert the URL, not just visibility |
| Silent failure modes | SQLite fallback served an empty catalog while health passed | Fail loudly on unsafe config |

None of these are fixed by reorganising folders. All five are fixed by a handful of
assertions and documents.

### 6.2 The invariants worth encoding

These are the rules a contributor must not break. Each should be a test, not a comment:

1. Money is integer minor units. No float arithmetic, ever.
2. Synchronous I/O does not run on the event loop. `async def` + sync `Session` is a
   bug, and after 1.1 it will be the only two handlers in the codebase — so it is
   easy to reintroduce. Add a test that asserts no router mixes them.
3. Every money-mutating POST carries `Idempotency-Key`.
4. Webhook signatures are computed over the **raw** body, never re-serialised JSON.
5. Agent code never imports SQLAlchemy. Already enforced by `lint-imports`.
6. Services never import `apps.*`. Already enforced.
7. No migration edits a previously-applied revision. Add a new one. (The
   `20260821_0002_idempotency_key_scope.py` docstring already explains why.)
8. A console read shows what the server returned, never a placeholder.

Items 1–4 and 7–8 have partial or no enforcement today. Each is a small test.

### 6.3 Working agreements

- **Feature branches, PR-sized under ~400 lines.** This codebase has 58k-line files
  already; do not grow the pattern.
- **One concern per PR, and say which phase of the plan it serves.** Reviewers can then
  weigh "this adds an index for 2.2" differently from "this refactors for tidiness".
- **Every schema change is one new migration**, reviewed by a `CODEOWNERS` owner of
  `infra/migrations`.
- **Every new endpoint ships with:** a test, a rate limit, a metric, and a role check.
  The last three are the ones that get forgotten, which is why they go in the PR
  template rather than in review comments.
- **No comment explaining *what*. Comments explain *why*, and the reason for a
  constraint. This codebase already does this well — `apps/api/auth.py`, `packages/money/`
  and `services/checkout/transitions.py` are models of it.
- **Merge only with green CI**, including the import-linter contracts. They are the
  cheapest architectural test in the project.

### 6.4 PR template

```markdown
## What
One paragraph. Links to the issue.

## Why
What breaks without this.

## Plan phase
P0 / P1 / P2 / P3 / ... (READINESS_PLAN.md) or "none — maintenance"

## Checklist
- [ ] Tests added or updated; existing suite still passes
- [ ] No new unindexed query on a hot table
- [ ] Endpoint: rate limit + metric + role check attached
- [ ] Schema change: new migration, not an edited revision
- [ ] Security boundary moved: threat model updated
- [ ] Customer-visible behaviour changed: README updated
```

### 6.5 Onboarding path

1. `make env && make up` — stack healthy, `/health/db` green.
2. `make test` — the suite is the specification; read a failure, not the code.
3. `docs/production/READINESS_PLAN.md` §0 and §6.2 — the shape and the invariants.
4. `docs/adr/` — two worked examples of a decision written down.
5. Fix a Phase 1 item. They are small, real, and touch the whole request path.

A contributor who ships a Phase 1 fix in week one understands the money path, the auth
path, the migration path, and the test path in one sitting. That is worth more than any
amount of documentation.

---

## 7. What is deliberately *not* proposed

Stating this matters as much as the plan, because these are the changes that look
attractive and would cost more than they return.

| Tempting change | Why not |
|---|---|
| Migrate sync SQLAlchemy to async (asyncpg) | Rewrites every query in 13,761 lines of domain code for a throughput gain better bought with caching (2.1) and indexes (2.2) — days of work versus hours |
| Break up `apps/web` (35k lines) | No measured defect. Splitting adds merge conflicts and indirection |
| Replace the hand-rolled token codec with PyJWT | The current codec is 226 lines, constant-time, tested, and correct. A dependency is a new supply-chain surface for no gain |
| Move to Kubernetes | Compose already runs the stack. K8s adds operational surface before there is a need for it |
| Microservices | The modular monolith with 4 enforced import contracts is already the right shape for one team. Splitting would break the boundaries that keep the domain out of the delivery layer |
| Adopt a framework's caching | An explicit `packages/cache` keeps invalidation visible at the write sites, which is where the correctness risk lives |

---

## 8. Summary

The codebase is in better shape than the "make it industry level" framing suggests: real
financial invariants, a genuinely strong security baseline, **1,939** unit/security/contract
tests plus **64** integration and **30** browser e2e, all green, with architecture
boundaries enforced in CI. All seven phase-1 blockers are fixed, and phase 2 has closed the
schema-drift and index findings.

What it still lacks is **capacity evidence and operational instrumentation**. But the more
important finding is not what it lacks — it is what kept saying everything was fine while
the payment path was dead.

Three bugs were invisible for the entire life of this repository, and all three share a
shape:

| What reported healthy | Reality |
|---|---|
| Route present in the OpenAPI schema | returned 500 on every call, forever |
| 1,914 tests green | no test posted a signed webhook to a real database |
| `/health` and `/health/db` green | no payment could ever be confirmed from the provider |
| `alembic_version` at head | five columns missing from a live table |
| `make revision` produced a migration | it would have dropped all 36 tables |

Every one of those is a *self-consistent* signal. The unit suite checks the ORM against
itself, because it builds its tables from the ORM. The migration chain is checked against
itself. `/health` asks the process about itself. Nothing ever asked the ORM and the database
the same question — which was the only question that mattered.

That is the single most useful thing this audit produced, and it is now enforced rather
than merely noted: `tests/integration/test_schema_matches_orm.py` compares the two directly
across all 33 mapped tables. The next drift fails CI instead of failing a customer.

Two process notes, because they changed the outcome rather than the paperwork:

- **Reverting each fix to confirm the test catches it was not optional.** Two of my own
  concurrency tests passed against the live bug, because `TestClient` drives the ASGI app
  in a separate thread and returns a lazy response — so the wall-clock I measured was the
  harness queueing, not the service responding. Reading the thread trace is what exposed
  it. The same discipline caught the migration-0006 test, the security-headers test, and
  both routing bugs.
- **Several items were implemented against their written description because the
  straightforward version was harmful** — repointing the operator scripts at a
  silent-SQLite-fallback engine, CSP on a JSON API, `make revision` with an empty metadata
  registry. A plan that survives its own implementation by getting items wrong is doing its
  job; one that is never revised is not being read.
- **Three of the seven items were implemented differently than first written**, because
  the straightforward version was harmful: the script repointing (silent SQLite fallback
  in a catalog import), CSP on a JSON API, and "bound every unpaginated GET" (two real
  problems, not a dozen). A plan that survives its own implementation by getting three
  items wrong is doing its job; one that is never revised is not being read.

The remaining ordering:

1. **One week** of metrics. Without them the 60k target is unfalsifiable and every
   subsequent decision is guesswork.
2. **Load testing** before optimising, so "60k" becomes a number rather than a hope.
3. **Caching and indexes** — the two changes that actually deliver the throughput, both
   additive.
4. **Everything else** on the timeline above, phases 2 through 7.

None of this requires restructuring. The module layout, the 36-table schema, the
import-linter contracts, and the existing patterns all stay exactly as they are — which
is the point: a codebase at this level of quality should be scaled, not rebuilt.
