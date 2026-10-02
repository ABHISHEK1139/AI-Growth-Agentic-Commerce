# Capacity report

Measured numbers, not estimates. Produced by `infra/loadtest/run-capacity-ladder.ps1`,
which probes a ladder of constant request rates and reports where the service stops
keeping up.

**Why this file exists separately from the readiness plan:** a throughput figure is a
property of the machine as much as of the code. Recording the host alongside the number
is what makes it usable later. A figure measured on a laptop says nothing about a 4 vCPU
host, and quoting it without the host is how a capacity estimate becomes a fact nobody
revisits.

## Measured host

| | |
|---|---|
| Platform | Docker Desktop on Windows (WSL2 backend) |
| Host cores | 16 |
| API | `agentpay-api:local`, uvicorn **`--workers 2`** |
| Datastore | PostgreSQL 16 (pgvector), single container, `max_connections` 100 |
| Cache | Redis 7.4, enabled (`REDIS_URL` set) |
| Pool | 10 + 5 per worker → 30 connections total |
| Fixture | 20k offers, 100k checkouts, 400k audit events, 60k provider events, 20k products |
| Date | 2026-10-02 |

**Read the shape of these results, not the absolute values.** The ladder is the finding:
where throughput stops tracking the offered rate. The absolute number moves with the
host.

## Ladder — cache enabled (offer + capability + product)

| Offered | Achieved | Errors | p50 | p95 | Dropped | Verdict |
|---|---|---|---|---|---|---|
| 300/s | 108.74/s | 0.89% | 755 ms | 16,649 ms | 4,694 | ⚠️ disturbed run |
| 400/s | 391.42/s | 0% | 77 ms | 851 ms | 186 | kept up |
| **500/s** | **494.97/s** | **0%** | **27 ms** | **510 ms** | 116 | **clean capacity** |
| 600/s | 485.09/s | 0% | 782 ms | 972 ms | 2,655 | saturated |

**Clean capacity: ~500 req/s. First saturation at 600.**

### The 300 req/s row is not a real result

It is *worse* than 400 and 500, which is impossible for a queueing system — throughput
cannot fall below a rate that a higher load exceeds. The cause is almost certainly that
Docker Desktop restarted during the session and the API was recovering; the machine
demonstrated this instability three times.

It is left in the table rather than deleted because removing an inconvenient row is how a
report starts lying. **Re-run the ladder on a stable host before quoting any of these
numbers.** The 400–600 rows are consistent with each other and with the earlier ladder;
the 300 row is not consistent with anything and should be treated as noise.

## The progression — why this file is worth reading

| Change | Clean capacity |
|---|---|
| Baseline (no cache) | 200 req/s |
| + cache on offer and capability reads | 400 req/s |
| + cache on product reads | ~500 req/s |

Three changes, and the baseline number came from a *measurement* rather than the
estimate the plan carried for the project's lifetime — which was wrong by 3.5×. The
diagnosis behind the caching work is in `READINESS_PLAN.md` §4.1: the ceiling was
database round-trips per request, not compute (6% API CPU and 0.03% Postgres CPU at the
old ceiling, while `/health` sustained 250 req/s on the same machine).

## Remaining gap

The acceptance criterion for "production ready at 60k customers" is **1,430 req/s**.
Measured clean capacity is ~500 req/s, so the gap is **~2.9×**, down from ~7×.

The remaining work is not more of the same:

- **Audit pagination** — `audit_event` is legally retained and grows forever; it is the
  slowest endpoint in the measured mix.
- **Read replicas** for catalogue reads.
- **Horizontal scale** with an explicit pool ceiling per replica.

## How to reproduce

```powershell
./infra/loadtest/run-capacity-ladder.ps1                          # default ladder
./infra/loadtest/run-capacity-ladder.ps1 -Rates 250,500,750,1000  # custom
```

For a single rate, or to reproduce a regression against a previous number:

```powershell
./infra/loadtest/run-load-test.ps1 -PeakRate 500 -SkipSeed
```

`run-load-test.ps1` refuses to measure unless the datastore is PostgreSQL and the
fixture resolves. Both checks exist because a silent fallback or a broken fixture
produces *excellent-looking* numbers: a 404 is fast, and a service serving an empty
catalogue from a local SQLite file is genuinely healthy.

### Two measurement traps, recorded because both bit

1. **A ramping profile cannot locate a ceiling.** k6 reports `http_reqs` as a whole-run
   average, so ramp stages drag the reported rate down. A run targeting 100 req/s
   reported 47 req/s and read as saturation. The scenarios use a constant rate for this
   reason.
2. **k6 omits metrics that recorded no samples.** `dropped_iterations` is absent when
   nothing was dropped — which is the best possible result. The ladder defaults it to
   zero rather than treating absence as a failure.
