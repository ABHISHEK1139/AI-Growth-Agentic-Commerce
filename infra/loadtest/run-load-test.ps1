<#
.SYNOPSIS
    Runs the AgentPay load test against the compose stack and writes a summary.

.DESCRIPTION
    Wraps `docker run grafana/k6` with the settings this project's compose topology
    needs, because getting those settings wrong produces a number that looks like a
    measurement and is not.

    Three of them matter:

    * k6 runs in a container, so `localhost` is the k6 container. It has to be
      `host.docker.internal`. Pointing it at `localhost` silently produces connection
      refused on every request, which is easy to misread as "the service collapsed
      immediately" rather than "the harness cannot reach it".
    * The API runs `--workers 2`, so two concurrent requests is already the
      concurrency limit of one container. A peak rate far above what two workers can
      serve measures queueing, not capacity -- which is a real finding, but only if
      it is labelled as such.
    * The result is written next to this script as `last-run-summary.json` so two
      runs can be compared directly. Comparing numbers from memory is how a
      regression gets missed.

.PARAMETER PeakRate
    Constant arrival rate in requests per second. This is the number that locates a
    capacity ceiling: it is held steady so "offered" and "achieved" are directly
    comparable. A ramping profile reports a whole-run average, which cannot be compared
    to a target rate and will mislead you into thinking you saturated early.

.PARAMETER Duration
    Hold duration, e.g. '30s'. The default is long enough for a steady rate to dominate
    any start-up transient.

.PARAMETER SkipSeed
    Skip the idempotent seed. Safe when the data is already present, which is the usual
    case on a second run.

.EXAMPLE
    ./infra/loadtest/run-load-test.ps1 -PeakRate 150

.EXAMPLE
    # Ladder to find the ceiling.
    foreach ($r in 100, 150, 200, 250) {
        ./infra/loadtest/run-load-test.ps1 -PeakRate $r -SkipSeed
    }
#>
[CmdletBinding()]
param(
    [int]$PeakRate = 50,
    [string]$Duration = '30s',
    [int]$PreAllocatedVUs = 150,
    [switch]$SkipSeed
)

$ErrorActionPreference = 'Stop'
# `docker compose` writes normal progress ("Container ... Running") to stderr. Under
# `$ErrorActionPreference = 'Stop'` PowerShell turns that into a terminating
# NativeCommandError, so the script dies on a *successful* command. Real failures are
# caught by checking $LASTEXITCODE explicitly, which is where it belongs anyway.
$ErrorActionPreference = 'Continue'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repoRoot

# Two compose files: the normal stack, plus the load-test overlay that disables the
# per-actor rate limiter. See docker-compose.loadtest.yml for why that is required
# rather than merely convenient.
$composeFiles = @('-f', 'docker-compose.yml', '-f', 'docker-compose.loadtest.yml')
function Compose {
    & docker compose @composeFiles @args
}

Write-Host "==> Starting the stack with the load-test overlay" -ForegroundColor Cyan
Compose up -d api postgres redis | Out-Null

# Verify the overlay actually applied. This is not ceremony.
#
# A run with the rate limiter still on is not a slower run, it is a *different*
# measurement: `GET /api/v1/catalog/*` allows 60 requests per minute per actor, and
# the harness presents one actor, so the "capacity" it reports is 1 req/s. An earlier
# run produced 47 req/s and 14% errors this way while looking like a service under
# stress. Compose silently recreating the container without the overlay cost one round
# of measurements before it was noticed, so it is asserted here instead of assumed.
Write-Host "==> Verifying the rate limiter is disabled" -ForegroundColor Cyan
$rl = (Compose exec -T api printenv RATE_LIMIT_ENABLED | Select-Object -Last 1).Trim()
if ($rl -ne 'false') {
    throw "RATE_LIMIT_ENABLED is '$rl', expected 'false'. The load-test overlay did not apply, and a run now would measure the rate limiter instead of the API. Re-run: docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d api"
}

Write-Host "==> Waiting for the API to be healthy" -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds(90)
$health = ''
while ((Get-Date) -lt $deadline) {
    # `State` is "running" even while the container is still starting; readiness is
    # reported separately in `Health`/`Status` ("Up 2 minutes (healthy)"). Checking
    # only State therefore returns instantly and can race the first request.
    $svc = Compose ps api --format json | ConvertFrom-Json | Select-Object -First 1
    $health = if ($svc.Health) { $svc.Health } elseif ($svc.Status) { $svc.Status } else { $svc.State }
    if ($health -match 'healthy') { break }
    if ($svc.State -and $svc.State -notmatch 'running') { throw "The api service is '$($svc.State)'." }
    Start-Sleep -Seconds 4
}
if ($health -notmatch 'healthy') { throw "The api service never became healthy (last: '$health')." }

if (-not $SkipSeed) {
    Write-Host "==> Seeding load-test volumes (idempotent)" -ForegroundColor Cyan
    Get-Content 'infra/loadtest/seed.sql' -Raw |
        Compose exec -T postgres psql -U agentpay -d agentpay -v ON_ERROR_STOP=1 -q
    if ($LASTEXITCODE -ne 0) { throw "Seeding failed." }
    Compose exec -T postgres psql -U agentpay -d agentpay -c "ANALYZE;" | Out-Null
}

Write-Host "==> Minting a session token for the seeded merchant" -ForegroundColor Cyan
$token = (Compose exec -T api python infra/loadtest/mint_session.py | Select-Object -Last 1).Trim()
if (-not $token) { throw "Could not mint a session token. Is SESSION_SECRET set?" }

# Verify the fixture before spending a minute of load on it. See not_found_rate in
# scenarios.js: a broken fixture produces 404s, which are fast, which looks like a
# healthy service.
Write-Host "==> Verifying the fixture resolves" -ForegroundColor Cyan
$probe = curl.exe -s -o NUL -w "%{http_code}" --cookie "agentpay_session=$token" `
    'http://localhost:8000/api/v1/catalog/offers/ld_offer_1'
if ($probe -ne '200') {
    throw "Seeded offer returned $probe, expected 200. Apply seed.sql and check MERCHANT_OFFER_BASE."
}

# Refuse to measure a service that has silently fallen back to SQLite.
#
# This happened for real: Docker Desktop restarted, the API process came up before
# PostgreSQL was accepting connections, and `get_engine()` fell back to a local file.
# Every seeded row became invisible, so every catalogue read 404'd -- and `/health` was
# green throughout, because the process genuinely was healthy. It was only caught by a
# fixture probe returning 404, which is a slow way to learn that the datastore moved.
#
# The API logs a WARNING when this happens, so the check is a grep of the container log
# rather than an inference from the probe. Compose runs APP_ENV=local, so the fallback
# is *permitted* there on purpose; what matters is that a load run never measures it
# silently.
Write-Host "==> Verifying the datastore is PostgreSQL" -ForegroundColor Cyan
$fallback = Compose logs --since 10m api 2>&1 | Select-String 'FALLING BACK'
if ($fallback) {
    Compose logs --since 10m api 2>&1 | Select-String 'FALLING BACK' | Select-Object -First 2
    throw @"
The API fell back to a local SQLite file, so it is serving an empty catalog. Every
catalogue read will 404 and the run would measure nothing. Restart the API once
PostgreSQL is healthy:

    docker compose -f docker-compose.yml -f docker-compose.loadtest.yml restart api
"@
}

Write-Host "==> Running k6 (constant ${PeakRate} req/s for ${Duration})" -ForegroundColor Cyan
$summaryPath = Join-Path $repoRoot 'infra/loadtest/last-run-summary.json'

# The scenarios directory is mounted read-only: k6 must not be able to edit the test
# it is running. The summary is written to a *separate* writable mount for the same
# reason -- with a single `:ro` mount above, `--summary-export` fails silently and the
# run produces numbers on stdout but no artifact to compare against.
$outDir = Join-Path $repoRoot 'infra/loadtest'
docker run --rm `
    -e "BASE_URL=http://host.docker.internal:8000" `
    -e "SESSION_TOKEN=$token" `
    -e "PEAK_RATE=$PeakRate" `
    -e "HOLD_SECONDS=$Duration" `
    -e "PREALLOCATED_VUS=$PreAllocatedVUs" `
    -v "${outDir}:/scripts:ro" `
    -v "${outDir}:/out" `
    grafana/k6:latest `
    run --summary-export /out/last-run-summary.json /scripts/scenarios.js

$exit = $LASTEXITCODE

if (Test-Path $summaryPath) {
    Write-Host "`n==> Summary written to infra/loadtest/last-run-summary.json" -ForegroundColor Cyan
} else {
    Write-Warning @"
k6 produced no summary, so this run cannot be compared against another one. The stdout
above is still a valid measurement. This happens when the summary path is not writable
inside the container -- check the `-v` mounts in this script.
"@
}

Write-Host "==> Postgres connection usage during the run" -ForegroundColor Cyan
Compose exec -T postgres psql -U agentpay -d agentpay -c `
    "SELECT state, count(*) FROM pg_stat_activity WHERE datname='agentpay' GROUP BY state;"

exit $exit
