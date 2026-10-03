# Security review — findings

A hands-on review of AgentPay: automated tooling plus live adversarial probing of the
running stack. Every finding below was reproduced, not inferred. Where a control held,
that is recorded too — a report that only lists problems does not tell you what was
actually checked.

Reviewed: 2026-10-02 (commit `cd063a3`) and 2026-10-03 (commit `cff3229` plus the
component walkthrough below).

## Summary

| Area | Result |
|---|---|
| Live adversarial probing (41 attacks) | **No exploitable vulnerability found** |
| bandit (HIGH severity) | 0 |
| pip-audit | 26 → **14** advisories |
| npm audit | **unrunnable → 7 found**, 2 fixed, 5 need a major upgrade |
| Test-double providers in non-local | **Vulnerable → fixed** |
| Application-level code review | **5 vulnerabilities found and fixed** |
| Component walkthrough (102 routes × 5 identities) | **0 × 5xx, 0 cross-tenant leaks, 0 anonymous mutations** |
| Agent commerce flow, end to end | **Broken → fixed, then a policy gap found** |

Three of the five application-level findings below were found by reading code, not by
scanning. No scanner reports them, because they are not patterns — they are logic errors.

Every fix here was verified the same way: revert the fix, watch the test fail, restore,
watch it pass. Finding A is a **confirmed exploit** — with the fix removed, the
cross-tenant request returns `200 {"verified": true}`.

---

## Fixed

### 1. A non-local deployment could run test-double providers (HIGH)

`validate_for_env` checked providers in one direction only — *"is `razorpay`
configured?"* — and never asked *"is the **fake** provider running?"*

Reproduced: `APP_ENV=staging` with `ALLOW_LIVE_CREDENTIALS=1` and no `PAYMENT_PROVIDER`
inherits the `"fake"` default, `validate_for_env` passes, and the deployment runs
`FakePaymentProvider`. Consequences, none of which raise:

- every payment reports success **without moving money**;
- webhooks verify against a secret **published in this repository**, so a forged
  `payment.captured` callback is accepted;
- the console shows healthy orders that do not exist at the provider.

`MODEL_PROVIDER=mock` (the agent reasons against a deterministic stub) and
`SEARCH_PROVIDER=null` (research silently returns nothing) had the same shape.

**Fix:** `validate_providers_for_env`, called from `create_app`, refuses any test double
outside `local`. `ALLOW_TEST_DOUBLE_PROVIDERS=1` is the escape hatch for a staging
environment that genuinely is a demo — so a load test or a judge's demo gets the fakes
*deliberately* rather than by omission.

Covered by `tests/security/test_test_double_providers.py` (14 tests), including one that
drives `create_app` rather than the validator — because `validate_datastore_for_env` was
written, correct, and ineffective for a whole session until something called it.

### 2. `python-multipart` 0.0.20 → 0.0.31 — 6 advisories cleared

0.0.20 carried PYSEC-2026-1852/3036/3037/3038/3039/3040 in the multipart parser used by
every file-upload endpoint. Total Python advisories: **26 → 14**.

### 3. `npm audit` could not run at all

The root `package.json` declares `workspaces: ["apps/web"]`, so npm treats the repository
root as the workspace root and requires a lockfile there. The lockfile only existed in
`apps/web`, so **every audit invocation failed with `ENOLOCK`** — meaning the frontend had
no CVE gate whatsoever, exactly as the readiness plan suspected.

**Fix:** a root `package-lock.json`. The gate immediately found 7 vulnerabilities.

### 4. `npm ci` — what CI runs — did not typecheck

Found while fixing #3. `apps/web/package.json` declared `"typescript": "^5.7.3"`, which
resolves to **5.9.3**, and `"@types/react": "^18.3.18"`, which resolves to **18.3.31**.
Together these broke `npm run typecheck` with six errors on a clean install:

- `tsconfig.json` had `"target": "es5"`, an anachronism for a Next 14 App Router app,
  which made Map iteration error TS2802;
- `@types/react` 18.3.31 dropped `src` from the resolved `ScriptProps`, breaking every
  `<Script>` usage.

So CI's own install command did not produce a typechecking tree. **Fixed** by setting
`target: es2022` and pinning `@types/react` / `@types/react-dom` to the last versions
whose types still describe the app.

This is a deliberate exception to the caret ranges used elsewhere in
`apps/web/package.json`. A caret range on a **type** package is not a convenience — a
patch bump can delete a prop from the type surface and break the build of an unchanged
checkout page. Pinning type packages is the lesser surprise.

### 4b. Two lockfiles that disagreed — the Docker build was broken

Found while verifying #4. The repository now has two npm lockfiles:

- `apps/web/package-lock.json` — used by the Dockerfile, which copies `apps/web` only
- `package-lock.json` at the root — required because the root `package.json` declares
  `workspaces: ["apps/web"]`, so npm treats the root as the workspace root

Installing inside `apps/web` updates the **root** lockfile, not the local one. So pinning
the `@types` packages fixed my local tree and left `apps/web/package-lock.json` stale, and
`docker compose build web` failed:

```
npm error `npm ci` can only install packages when your package.json and
package-lock.json ... are in sync.
npm error Invalid: lock file's @types/react@18.3.31 does not satisfy @types/react@18.3.18
npm error Invalid: lock file's @types/react-dom@18.3.7 does not satisfy @types/react-dom@18.3.5
```

Worth stating plainly: **the container I had been probing was two hours old and did not
contain any of the fixes in this document.** `docker compose up -d --build` reported
success, `docker compose ps` showed healthy containers, and the image was stale — the only
reason it was caught is that a header I had just added (`Permissions-Policy`) was missing
from the live response. A green `docker compose up` is not evidence that your code is
running.

Both lockfiles are now regenerated from the same `package.json` and agree. Verified by
building the image and confirming the new headers are actually served.

Keeping two lockfiles is a genuine wart: the root one exists only to make `npm audit`
runnable, and the Dockerfile cannot see it. It is preferable to the alternative — no CVE
visibility at all — but it will drift again unless both are refreshed together.

---

## Fixed — application logic

Found by reading code and by tracing trust boundaries. No scanner reports these.

### A. Cross-merchant payment verification (IDOR) — HIGH

`verify_razorpay_payment` resolved the payment by `provider_order_id` **only**:

```python
payment = session.query(Payment).filter(
    Payment.provider_order_id == request.razorpay_order_id
).first()
```

The endpoint required `payments:verify` scope, so it was not open to the public — but the
lookup was never constrained to the caller's tenant. Any principal holding that scope
could POST a **valid, correctly-signed** verification payload for an order they do not own
and receive confirmation for another merchant's payment.

The signature check made this feel safe: the surrounding comment even says *"a valid
signature proves the provider signed these identifiers, not that the caller owns them."*
That reasoning is right, and the code then ignored it. Signing stops forgery; it does not
stop replay across tenants.

**Fix:** scope the query by `merchant_id` **and** `buyer_id` from the principal.

**Confirmed exploitable, not theoretical.** Reverting only the scoping predicate and
re-running the tests makes both cross-tenant cases return **200 with `verified: true`** —
the gateway confirms a payment for a merchant or buyer who does not exist in the request.
No signature knowledge is needed beyond what the provider already issued for that order.

Covered by three tests in `tests/unit/test_razorpay_standard_checkout.py`, and the
third is the one that matters:

| Test | Purpose | Reverting the fix |
|---|---|---|
| `..._accepts_the_callers_own_payment` | **positive control** — identical fixture, own payment → 200 | still 200 |
| `..._rejects_another_buyers_payment` | same merchant, foreign buyer | **200 == 404 fails** |
| `..._rejects_another_merchants_payment` | foreign merchant | **200 == 404 fails** |

The positive control is not decoration. The first version of these tests asserted only
that a foreign payment 404s — and it **passed with the fix removed**, because the mock's
unrelated failure produced its own 404. A negative assertion with no matching positive
one proves that *something* 404s, not that the tenant boundary is what stopped it.

### B. Open redirect into the payment callback — MEDIUM

`return_url` was passed straight to Razorpay as `callback_url` and echoed back as
`checkout_url`. Unvalidated, any caller could set it to an attacker host — so the buyer,
and the signed payment query string in the URL, are redirected off-site immediately after
a completed checkout. That is a credential-in-URL disclosure path with a plausible
phishing delivery mechanism.

**Fix:** `resolve_checkout_return_url` accepts only a root-relative path or an absolute URL
on a configured `cors_origins` entry. It rejects `//evil.com`, backslash variants
(`/\evil.com`, which several browsers normalise to `//`), embedded `://`, and any
absolute origin not in the allow-list. Two tests cover it; disabling the allow-list check
fails both.

### C. HSTS was never actually sent — MEDIUM

`SecurityHeadersMiddleware` gated HSTS on `request.url.scheme == "https"`. The container
listens on plain HTTP behind a TLS-terminating proxy, so `url.scheme` is always `http` and
**the header was never emitted in the actual deployment** — while the test suite, which
uses an ASGI transport with an https scope, passed. The control looked verified and was
not.

**Fix:** `_arrived_over_https` also honours the first hop of `X-Forwarded-Proto`. Only the
first value is trusted, so a client cannot append a spoofed `https` after a real `http` to
manufacture the header. 13 header tests in `tests/security/test_security_headers.py`.

### D. Unbounded in-memory growth in a rate limiter — MEDIUM

`/api/ai/test-connection` kept a `Map<string, number[]>` keyed on `X-Forwarded-For` and
**never evicted anything**. The key is fully attacker-controlled, so varying it grew the
map without bound — a slow memory exhaustion that survives until the process restarts.
It also never parsed the header, so `X-Forwarded-For: a, b` from a single client produced a
different key per request, making the limit trivially bypassable.

**Fix:** extracted to `apps/web/src/lib/simpleRateLimit.ts` with a sliding window, a
10k-bucket sweep that drops expired entries, and first-hop-only parsing. The same
limiter now guards the `gemini` and `grok` chat routes, which previously had none — both
proxy directly to a **paid** model key, so an unauthenticated caller could otherwise bill
you without limit.

### E. Internal detail in error responses

- Webhook failure returned `f"Webhook processing failed: {exc}"` to the caller — which for
  a database error leaks the driver, table and column names. Now logged server-side and
  replaced with a generic message.
- `/api/ai/test-connection` echoed the **outbound URL** and upstream response body back to
  the browser, disclosing internal hostnames and whatever the upstream said. Both removed.

### F. Login `next` parameter

Already rejected `//host`, but not `/\evil.com` or `/path?x=://evil`. Tightened. Low
severity — Next.js routing is the real control — but the check now covers the variants.

### G. Missing `Permissions-Policy`

Added `camera=(), microphone=(), geolocation=()` to both the API middleware and the Next
config. Cheap defence in depth.

---

## Found, not fixed — and why

### 5. 14 `starlette` advisories (blocked upstream)

| Package | Version | Advisories | Fixed in |
|---|---|---|---|
| `python-multipart` | ~~0.0.20~~ → 0.0.31 | ~~6~~ cleared | — |
| `starlette` | 0.41.3 | 14 | 0.47.2 … 1.3.1 |

Clearing all 14 requires `starlette >= 1.3.1`, which requires `fastapi >= 0.128`; the
installed `fastapi 0.115.6` pins `starlette < 0.42`.

**Attempted and rejected.** Upgrading to `fastapi 0.142.2` + `starlette 1.3.1` installs
cleanly and passes `import`, but **silently registers every router with an empty path**:

```
TOTAL 26 routes, API routes 0
```

`include_router` produced routes with `path == ''` instead of the declared paths. Five
tests failed; all 104 `/api` routes were simply gone. Shipping that would have been far
worse than the advisories, so the upgrade was reverted and the tree verified back at
116 routes / 104 API routes.

**Recommended path:** treat the FastAPI upgrade as its own task with the full suite and
browser e2e as the gate, and land a CI assertion that counts API routes so this specific
silent failure can never pass again.

### 6. 5 frontend advisories (needs a major upgrade)

| Package | Severity | Issue |
|---|---|---|
| `next` | **critical** | HTTP request smuggling in rewrites; RSC deserialization DoS; Image Optimizer DoS |
| `postcss` | high | XSS via unescaped `</style>`; arbitrary file read via `sourceMap` |
| `eslint-config-next` | high | bundles vulnerable `glob` |
| `@next/eslint-plugin-next` | high | same |
| `glob` | high | CLI command injection |

All five resolve only at `next@16.3.8` / `eslint-config-next@16.3.8` — a **major** upgrade
from `next@14.2.35`. Not attempted here for the same reason as #5: a framework major on a
1,986-test codebase needs its own change window.

The `next` advisories are the ones that matter most for this deployment, because
`next.config.js` uses `rewrites()` to proxy `/api/*` to the gateway — **request smuggling
in rewrites is exactly the deployed configuration**. Two mitigations available now
without an upgrade: terminate TLS at a proxy that rejects ambiguous framing, and avoid
exposing the Next.js server directly to untrusted clients.

---

## Live probing — what held

41 attacks against the running compose stack. None succeeded.

| Probe | Result |
|---|---|
| `alg:none` JWT, garbage bearer, tampered/tampered-role session cookie | all rejected, `authenticated: false` |
| 7 protected routes with no credential | all `401` |
| Webhook: no / empty / all-zero / short / correct-length-garbage signature | all `400 WEBHOOK_SIGNATURE_INVALID` |
| SQLi in login email, SQLi in offer id | `422` / `404`, no error leakage |
| SSRF: `169.254.169.254`, `127.0.0.1`, `file://`, decimal-encoded IP | all refused |
| 200 KB body | `422` |
| Path traversal, `/actuator/env`, `/.env`, route enumeration | all `404` |
| Security headers | `x-frame-options: DENY`, `nosniff`, no `x-powered-by` |

`POST /auth/demo-session` returning 200 is **correct** here: this stack runs
`APP_ENV=local`, and that endpoint is disabled outside local — asserted in
`tests/unit/test_login_api.py`.

Minor: `server: uvicorn` is disclosed. It does not include a version, and suppressing it
is cosmetic; noted rather than fixed.

### What the probe did *not* find — read this before trusting the table above

The 41 live probes found **nothing**, while reading the same two files turned up the IDOR
(A) and the open redirect (B). That is not an accident of luck, it is a limitation of the
method, and it is the most useful thing in this document:

- A probe needs a payload. Finding A required reasoning about *which* predicate a query
  was missing, not about what to send.
- Finding B needed no authentication and no clever string. A `POST` with
  `return_url: "https://evil.example/steal"` is a well-formed request that passes every
  generic check. Probing looks for malformed input; this was well-formed and wrong.
- The signature check in A made the endpoint *look* defended, which is precisely why a
  scanner — and a probe — looks past it.

So the passing probe table is evidence about **input handling** (injection, traversal,
authn, forgery): those controls genuinely hold. It is not evidence about **authorization
logic**, and A is the proof. Authorization needs reading, or a test per trust boundary,
not a payload list.

---

## Component walkthrough — 2026-10-03

Every route in the OpenAPI document (102 paths) called as five identities
(`platform_admin`, `merchant_admin`, `merchant_operator`, `buyer`, and a second
merchant's admin) plus anonymous, with schema-valid bodies generated from the spec.

| Check | Result |
|---|---|
| 5xx responses | **none**, across ~600 calls |
| Cross-tenant leakage (tenant B seeing tenant A's ids) | **none** |
| Mutations reachable without a credential | **none** — every one returns 401/403 |
| Credential lifecycle (issue → exchange → use → revoke) | holds; scope narrowing, revocation, and brute-force rate limiting all correct |

Driving this from the schema mattered. A hand-written first pass produced 40
"failures" that were almost entirely my own bad payloads, and three of my own probe
bugs looked like product bugs:

- a 404 that was really a wrong response key (`offers`, not `candidates`);
- an "agent key authenticates nowhere" conclusion that was really the response being
  nested under `data.key`;
- a "merchant B can delete merchant A's agent key" finding that was a status-code-only
  check — the endpoint returns 200 with `revoked: false` by design, and A's key survived.

The last one is the most useful lesson here: **check the body, not the status.**

### Fixed — agent and console buyers could not check out at all

`checkout`, `order` and `payment` all carry a foreign key to `buyer`. Nothing
provisioned that row on demand:

- `signup` creates an `OperatorAccount`, not a `Buyer`;
- an exchanged agent token carries a synthetic `buyer_akc_...` that exists only as a string.

So `POST /api/v1/checkout` died on `checkout_buyer_id_fkey` and returned
**503 SERVICE_UNAVAILABLE** — for every signing-up console buyer and every external
agent. 503 is the worst possible answer, because it says "retry later" for a condition
that can never resolve.

Fixed in two places: `CheckoutService._ensure_buyer` provisions the row at the insert
that needs it (so every caller is covered at once rather than one route at a time), and
`register_agent_key` provisions it in the same transaction as the credential, so a valid
key can never exist without a buyer behind it. Live: agent checkout 503 → 200, human
buyer checkout 503 → 200.

### Fixed — an agent could approve its own spending (finding E)

`approve`/`reject` were gated on `require_scopes(CHECKOUT_WRITE)`. An exchanged agent
token holds exactly that scope — it must, to build a cart — so the agent approved its own
authorization and then drew money. The capability document advertises
`explicit_approval_required`, and this deleted that control in exactly the place it
matters: above the auto-approval limit, where the point is that a person looks at it.

Requirement 20.5 in `apps/api/auth.py` already says a session-only administrative action
must not be performable with a long-lived agent credential. This was that action, and
the gate simply was not applied. Fixed with `require_session_scopes`, and five tests in
`tests/security/test_agent_cannot_self_approve.py` — including one proving a session is
*still* allowed to approve, so the fix cannot be "reject everyone".

### Open — agent payments cannot complete, and this needs a policy decision

With self-approval closed, an agent purchase above the auto-approval limit has **no
approver at all**, and I did not fix this because the fix is a policy choice, not a bug:

1. `auto_approval_limit_minor` is stored on merchant rules and buyer policy and
   advertised in the capability document, but **the authorization service never reads
   it**. Nothing auto-approves. Every authorization is created `pending`.
2. Approval requires `checkout:write` **and** a matching `buyer_id`. Only the `buyer`
   role holds `checkout:write`; `merchant_admin` and `merchant_operator` are capped at
   `catalog:read` — deliberately, per the comment in `principals.py`: a merchant-side
   credential that can create a payment can charge a buyer.
3. An agent's `buyer_id` is `buyer_akc_...`, which no human session holds.

Net effect: **agent search, checkout, and authorization all work; the payment step always
returns 403 "The approval has not been granted."** The seeded catalog's cheapest item is
₹7,990 against a ₹5,000 ceiling, so even the smallest purchase dead-ends.

I prototyped the obvious fix — a merchant-scoped `approve_authorization_as_merchant` —
and then **removed it**. With current role scopes it is unreachable dead code, and making
it reachable means widening `merchant_admin`, which contradicts a deliberate documented
decision. The options, for whoever owns that policy:

- give a merchant role an approve-only scope (no payment creation), or
- make the approver a merchant-side role rather than the buyer identity, or
- implement the auto-approval the capability document already promises.

Until one is chosen, `explicit_approval_required` should be read as "no agent purchase can
complete" rather than "a human reviews agent spending".

`tests/security/test_agent_cannot_self_approve.py` pins the current behaviour with a test
named for the limitation, so it cannot drift unnoticed.

---

## Not covered by this review

- Dependency CVEs on transitive packages not in either lockfile's audit scope.
- Runtime/authentication logic of the payment *state machine* under concurrent capture
  (covered by unit tests, not by adversarial probing here).
- The buyer-agent's own outbound behaviour.
- Anything requiring live Razorpay credentials.