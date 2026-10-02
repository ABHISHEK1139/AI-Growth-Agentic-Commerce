<#
.SYNOPSIS
    Measures the capacity ladder in one command and writes a report.

.DESCRIPTION
    Runs a sequence of constant request rates, records achieved throughput and latency
    at each, and reports where the service stops keeping up. This exists because
    "what can it handle?" was an estimate in the readiness plan for as long as the
    project existed, and the estimate was wrong by 3.5x.

    Run it on whatever host you intend to deploy to. That is the whole point: the
    absolute numbers here are a property of the machine, not of the code, and a
    figure measured on a laptop says nothing useful about a 4 vCPU host.

    Two rules make the output trustworthy, and both are enforced here rather than left
    to the reader:

    * **Constant rate, not a ramp.** k6 reports http_reqs as a whole-run average, so a
      ramping profile reports a number dragged down by its own ramp and reads as
      saturation. See infra/loadtest/scenarios.js.
    * **Never measure a broken fixture.** A 404 is fast, so a wrong fixture produces
      excellent-looking latency. run-load-test.ps1 asserts the datastore is PostgreSQL
      and the fixture resolves before any load is applied.

.PARAMETER Rates
    Rates to probe, in requests per second. The default ladder brackets the known
    knee: below it the service keeps up, above it throughput falls as load rises.

.PARAMETER Duration
    Hold duration per rate.

.PARAMETER SkipSeed
    Skip the idempotent seed. Correct when the fixture is already loaded, which is the
    usual case for a second run.

.EXAMPLE
    ./infra/loadtest/run-capacity-ladder.ps1

.EXAMPLE
    ./infra/loadtest/run-capacity-ladder.ps1 -Rates 100,200,400,800,1200
#>
[CmdletBinding()]
param(
    [int[]]$Rates = @(100, 200, 400, 600, 800),
    [string]$Duration = '25s',
    [switch]$SkipSeed
)

$ErrorActionPreference = 'Continue'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repoRoot
$runner = Join-Path $PSScriptRoot 'run-load-test.ps1'
$summaryPath = Join-Path $PSScriptRoot 'last-run-summary.json'

$results = @()

foreach ($rate in $Rates) {
    Write-Host "`n=================== probing $rate req/s ===================" -ForegroundColor Cyan

    if (Test-Path $summaryPath) { Remove-Item $summaryPath -Force }

    & $runner -PeakRate $rate -Duration $Duration -SkipSeed:$SkipSeed 2>&1 |
        Where-Object { $_ -notmatch '^\s*Container |docker.exe' } | ForEach-Object { "  $_" }

    if (-not (Test-Path $summaryPath)) {
        Write-Warning "no summary produced at $rate req/s; recording as inconclusive"
        $results += [pscustomobject]@{
            offered = $rate; achieved = $null; errors_pct = $null
            p50_ms   = $null; p95_ms   = $null; dropped = $null; note = 'no summary'
        }
        continue
    }

    $summary = Get-Content $summaryPath -Raw | ConvertFrom-Json
    $metrics = $summary.metrics

    # k6's `--summary-export` JSON is flat: `metrics.http_reqs.rate`, not
    # `metrics.http_reqs.values.rate`. The earlier version of this script assumed the
    # nested shape and reported zeros for every probe -- which is worse than crashing,
    # because a table of zeros looks like a measurement.
    #
    # `dropped_iterations` is genuinely optional: k6 omits a metric that recorded no
    # samples, and zero drops is exactly the case worth recording. So it defaults to 0
    # while the three that must exist are required. The earlier version required it too
    # and threw on the best possible result.
    foreach ($required in 'http_reqs', 'http_req_duration', 'http_req_failed', 'iterations') {
        if (-not $metrics.$required) {
            throw "summary is missing '$required'; refusing to report a partial result"
        }
    }

    $achieved = [math]::Round($metrics.http_reqs.rate, 2)
    $p50 = [math]::Round($metrics.http_req_duration.med, 2)
    $p95 = [math]::Round($metrics.http_req_duration.'p(95)', 2)
    # `value` is the failure *rate* (0..1); `fails` is a count of failed checks, which
    # is a different thing and was the second wrong assumption.
    $errorsPct = [math]::Round($metrics.http_req_failed.value * 100, 2)
    $dropped = if ($metrics.dropped_iterations) {
        [math]::Round($metrics.dropped_iterations.count, 0)
    } else { 0 }

    # "Kept up" means all three of: achieved within 3% of offered, essentially no dropped
    # iterations, and under 1% errors.
    #
    # The dropped-iteration allowance matters. An earlier version demanded *zero* drops
    # and reported every probe as saturated, including one at 99.3% of the offered rate,
    # because a handful of iterations were dropped during start-up. A criterion that
    # strict cannot distinguish "at capacity" from "over it", which is the only
    # distinction this script exists to draw.
    $offeredIterations = $metrics.iterations.count
    $droppedShare = if ($offeredIterations -gt 0) { $dropped / $offeredIterations } else { 1 }
    $keptUp = ($achieved -ge ($rate * 0.97)) -and ($droppedShare -lt 0.01) -and ($errorsPct -lt 1)

    $results += [pscustomobject]@{
        offered    = $rate
        achieved   = $achieved
        errors_pct = $errorsPct
        p50_ms     = $p50
        p95_ms     = $p95
        dropped    = $dropped
        note       = if ($keptUp) { 'kept up' } else { 'saturated' }
    }
}

Write-Host "`n=================== capacity ladder ===================" -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-String | Write-Host

$clean = $results | Where-Object { $_.note -eq 'kept up' }
if ($clean) {
    $best = ($clean | Sort-Object offered -Descending | Select-Object -First 1)
    Write-Host "Clean capacity: $($best.offered) req/s  (p95 $($best.p95_ms) ms)" -ForegroundColor Green
    $next = $results | Where-Object { $_.offered -gt $best.offered } | Select-Object -First 1
    if ($next) {
        Write-Host "First saturation at: $($next.offered) req/s" -ForegroundColor Yellow
    }
} else {
    Write-Host "No rate kept up. Lower the ladder." -ForegroundColor Yellow
}

$report = Join-Path $repoRoot 'docs/production/capacity-report.md'
Write-Host "`nPaste this table into $report" -ForegroundColor Cyan
Write-Host "  (that file records the measured host, which is what makes a number usable)"
