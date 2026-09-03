# ADR-0009: Self-hosted SearXNG as the web search provider

- **Status:** Accepted (implementation deferred to Task 36)
- **Date:** 2026-08-21
- **Relates to:** base doc §18 (research agent), plan §5.8 (threat model), NFR-4, NFR-6

## Context

The research agent (Task 36) must answer narrow open-world product questions —
"does this laptop drive two external displays?" — with cited evidence. That needs
a web search capability.

Options considered:

| Option | Cost model | Control | Verdict |
|---|---|---|---|
| Commercial search API (Brave, Serper, Tavily) | per-request billing, quota | none over engine mix | rejected for a student build with an open-ended evaluation loop |
| Scraping DuckDuckGo HTML directly | free | brittle, ToS-hostile, no stable contract | rejected |
| Public SearXNG instance | free | none; JSON output is frequently disabled, limits vary | rejected as a demo dependency |
| **Self-hosted SearXNG** | own bandwidth/compute | full control of engines, formats, limits | **accepted** |

SearXNG is an open-source metasearch engine that aggregates upstream engines and
exposes a JSON search endpoint, which is exactly the shape the research agent
needs. Self-hosting removes the per-request SaaS bill and guarantees the JSON
format stays enabled.

## Decision

Use a self-hosted SearXNG instance as the search backend, behind a
provider-neutral `SearchProvider` interface, with a null/mock provider as the
default so the test suite and the golden path never require it.

Page reading uses `httpx` for transport and `trafilatura` for main-content
extraction, with `BeautifulSoup` as a fallback for pages trafilatura cannot parse.

## Consequences

### Search is NOT "unlimited"

SearXNG imposes no commercial quota, but upstream engines throttle and block.
The bounded research loop from base doc §18.2 is therefore a correctness
requirement, not a politeness measure:

- max 3 searches per research session
- max 5 pages opened
- max 6 total steps
- Redis-cached results, keyed on the normalized query
- per-page response size and wall-clock caps

An unbounded `while True: search()` loop would get the instance blocked by
upstream engines mid-demo.

### The search path and the fetch path must stay separate (security-critical)

Our SSRF control blocks private-IP targets in `open_url`. A self-hosted SearXNG
listens on a private address. Routing search through the general safe-fetcher
would require whitelisting private IPs there, which would reopen exactly the hole
the control exists to close: a prompt-injected product description convincing the
agent to fetch an internal service.

Therefore:

| Tool | Transport | Host control |
|---|---|---|
| `search_web(query)` | dedicated SearXNG client | single base URL from config; **never** caller-supplied |
| `open_url(url)` | general safe-fetcher | public scheme + host allowlist; private IPs, loopback, and link-local blocked |

The SearXNG base URL is configuration, not a parameter. No code path lets a model
output, a product description, or an API caller influence which host the search
client contacts.

### Untrusted content handling is unchanged

Retrieved pages are untrusted (NFR-4). They enter the model as delimited evidence
blocks with provenance, never as instructions. Scripts and non-content markup are
stripped during extraction. This ADR changes where evidence comes from, not how it
is handled.

### Infrastructure timing

SearXNG is a sixth Compose service. Per NFR-6 and the addendum's §9 hard rule, it
is **not** added until the golden path, the external buyer agent, and the contract
suite are complete (Task 25). Adding it earlier spends critical-path time on a
`[POST]` feature.

Landing now, because it is free:

- this ADR
- the `SEARCH_PROVIDER` / `SEARXNG_BASE_URL` configuration shape, defaulting to
  the null provider, so no config reshaping is needed later

Landing in Task 36:

- the Compose service and its hardened `settings.yml`
- the `SearchProvider` interface and SearXNG client
- the extraction pipeline and the evidence store

### Demo fallback

If the self-hosted instance is throttled during a live demo, the research agent
degrades to answering from catalog facts only and reports the research step as
unavailable. It must never fabricate a citation. A commercial search API can be
slotted behind the same interface as a contingency.
