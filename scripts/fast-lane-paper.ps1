param(
    [ValidateSet("status", "summary", "tick", "run-once", "scheduler")]
    [string]$Action = "status",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot "backend"
$uvCache = Join-Path $repoRoot ".tmp\uv-cache"
$primaryPython = Join-Path $backend ".venv\Scripts\python.exe"
$fallbackPython = Join-Path $backend ".venv-codex\Scripts\python.exe"
$script:PythonExe = $primaryPython
New-Item -ItemType Directory -Force -Path $uvCache | Out-Null

Set-Location $backend

$env:UV_CACHE_DIR = $uvCache
$env:STRATEGY_POLICY_MODE = "repair_v5"
$env:MODE = "paper"
$env:LIVE_TRADING_ENABLED = "false"
$env:COLLECTORS_ENABLED = "true"

Write-Host "Weather Bot fast lane paper mode"
Write-Host "Policy: repair_v5_high_reward_v1"
Write-Host "Cities: atlanta YES, seattle YES, toronto NO"
Write-Host "Live trading enabled: $env:LIVE_TRADING_ENABLED"
function Test-PythonEnvironmentAccess {
    param([string]$PythonExe)

    if (-not (Test-Path -LiteralPath $PythonExe)) {
        return $false
    }

    $paths = @(
        (Join-Path (Split-Path -Parent (Split-Path -Parent $PythonExe)) "Lib\site-packages\sqlalchemy\util\_py_collections.py"),
        (Join-Path (Split-Path -Parent (Split-Path -Parent $PythonExe)) "Lib\site-packages\pydantic_core\_pydantic_core.cp314-win_amd64.pyd")
    )

    foreach ($path in $paths) {
        if (-not (Test-Path -LiteralPath $path)) {
            continue
        }
        try {
            $stream = [System.IO.File]::OpenRead($path)
            $stream.Close()
        } catch {
            return $false
        }
    }
    try {
        $output = & $PythonExe -c "import sqlalchemy, pydantic_core; print('ok')" 2>&1
        if ($LASTEXITCODE -ne 0 -or ($output -join "`n") -notmatch "ok") {
            return $false
        }
    } catch {
        return $false
    }
    return $true
}

function Select-PythonEnvironment {
    if (Test-PythonEnvironmentAccess $primaryPython) {
        $script:PythonExe = $primaryPython
        return
    }
    if (Test-PythonEnvironmentAccess $fallbackPython) {
        $script:PythonExe = $fallbackPython
        Write-Host "Primary backend .venv is not readable; using .venv-codex fallback."
        return
    }
    [Console]::Error.WriteLine(
        "No readable backend Python environment found. " +
        "Close stale Python/uv/PowerShell processes or recreate backend\.venv."
    )
    exit 13
}

function Invoke-Python {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $script:PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Invoke-StatusJson {
    $output = & $script:PythonExe -m analysis.high_reward_paper_status --json
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    return ($output | Select-Object -Last 1)
}

function Get-MissingCoverageSnapshots {
    param($Payload)

    $snapshots = @{}
    foreach ($city in $Payload.summary.missing_coverage) {
        $cityRow = $Payload.cities | Where-Object { $_.city_slug -eq $city } | Select-Object -First 1
        if ($null -eq $cityRow -or $null -eq $cityRow.current_candidate_diagnostics) {
            continue
        }
        $sample = $cityRow.current_candidate_diagnostics.samples | Select-Object -First 1
        if ($null -eq $sample) {
            continue
        }
        $snapshots[$city] = [PSCustomObject]@{
            reason = $sample.reason
            side = $sample.side
            bucket = $sample.bucket
            market_price = $sample.market_price
            variant_max_price = $sample.variant_max_price
            price_to_variant_max = $sample.price_to_variant_max
            probability_delta = $sample.probability_delta
            probability_delta_to_min = $sample.probability_delta_to_min
            hours_to_close = $sample.hours_to_close
        }
    }
    return $snapshots
}

function Get-ErrorSummary {
    param($Errors)

    $items = @($Errors | Where-Object { $null -ne $_ -and "$_".Length -gt 0 })
    $samples = @()
    foreach ($item in ($items | Select-Object -First 3)) {
        $text = "$item"
        $firstLine = ($text -split "`n" | Select-Object -First 1).Trim()
        if ($firstLine.Length -gt 180) {
            $firstLine = $firstLine.Substring(0, 180)
        }
        $samples += $firstLine
    }
    return [PSCustomObject]@{
        count = $items.Count
        sample = $samples
    }
}

function Show-StatusSummary {
    $payload = Invoke-StatusJson | ConvertFrom-Json
    $summary = $payload.summary
    $gates = $summary.gate_progress
    $candidateDiagnostics = $summary.current_candidate_diagnostics
    $missingReasons = @{}
    foreach ($city in $summary.missing_coverage) {
        $cityRow = $payload.cities | Where-Object { $_.city_slug -eq $city } | Select-Object -First 1
        if ($null -ne $cityRow -and $null -ne $cityRow.current_candidate_diagnostics) {
            $missingReasons[$city] = $cityRow.current_candidate_diagnostics.reason_counts
        }
    }
    $missingSnapshots = Get-MissingCoverageSnapshots $payload
    [PSCustomObject]@{
        status = $payload.status
        policy = $payload.policy_name
        active_cities = ($payload.active_cities -join ",")
        missing_coverage = ($summary.missing_coverage -join ",")
        entry_fills = $summary.entry_fills
        settlement_fills = $summary.settlement_fills
        resolved_fills = $gates.resolved_fills
        forward_days = $gates.forward_days_elapsed
        remaining_forward_days = $gates.remaining_forward_days
        remaining_resolved_fills = $gates.remaining_resolved_fills
        sample_gate = $gates.sample_gate_passed
        coverage_gate = $gates.coverage_gate_passed
        fee_failures = $summary.fee_failures
        settlement_failures = $summary.settlement_failures
        wrong_token_signals = $summary.wrong_token_signals
        candidate_reason_counts = $candidateDiagnostics.reason_counts
        candidate_actionability_counts = $candidateDiagnostics.actionability_reason_counts
        missing_coverage_reasons = $missingReasons
        missing_coverage_samples = $missingSnapshots
        pending_targets = $summary.pending_targets
        next_action = $summary.next_action
        blockers = ($payload.blockers -join ",")
    } | ConvertTo-Json -Compress
}

function Invoke-RunOnceJson {
    $output = & $script:PythonExe -m app.collectors.run_once all --high-reward-fast-lane --json
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    return ($output | Select-Object -Last 1)
}

function Invoke-Measurement {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $script:PythonExe -m analysis.measurement --json 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        $output | Out-Host
        exit $exitCode
    }
    $jsonLine = $output | Where-Object { $_ -like "{*" } | Select-Object -Last 1
    if ($null -eq $jsonLine) {
        [Console]::Error.WriteLine("Measurement did not emit JSON.")
        exit 14
    }
    return ($jsonLine | ConvertFrom-Json)
}

function Show-TickSummary {
    $collect = Invoke-RunOnceJson | ConvertFrom-Json
    $measurement = Invoke-Measurement
    $payload = Invoke-StatusJson | ConvertFrom-Json
    $summary = $payload.summary
    $gates = $summary.gate_progress
    $candidateDiagnostics = $summary.current_candidate_diagnostics
    $missingReasons = @{}
    foreach ($city in $summary.missing_coverage) {
        $cityRow = $payload.cities | Where-Object { $_.city_slug -eq $city } | Select-Object -First 1
        if ($null -ne $cityRow -and $null -ne $cityRow.current_candidate_diagnostics) {
            $missingReasons[$city] = $cityRow.current_candidate_diagnostics.reason_counts
        }
    }
    $missingSnapshots = Get-MissingCoverageSnapshots $payload
    $collectErrorSummary = Get-ErrorSummary $collect.errors
    [PSCustomObject]@{
        collect_errors_count = $collectErrorSummary.count
        collect_error_sample = $collectErrorSummary.sample
        ensemble_members = $collect.ensemble_members
        forecast_snapshots = $collect.forecast_snapshots
        price_snapshots = $collect.price_snapshots
        signals_created = $collect.signals_created
        paper_orders = $collect.paper_orders
        paper_fills = $collect.paper_fills
        paper_settlements = $collect.paper_settlements
        status = $payload.status
        missing_coverage = ($summary.missing_coverage -join ",")
        entry_fills = $summary.entry_fills
        resolved_fills = $gates.resolved_fills
        remaining_forward_days = $gates.remaining_forward_days
        remaining_resolved_fills = $gates.remaining_resolved_fills
        sample_gate = $gates.sample_gate_passed
        coverage_gate = $gates.coverage_gate_passed
        measurement_status = $measurement.status
        measurement_paper_pnl = $measurement.summary.paper_pnl
        measurement_brier_delta = $measurement.metrics.paper_brier_delta
        measurement_sample_gate = $measurement.checks.sample_size.passed
        measurement_pnl_gate = $measurement.checks.paper_pnl.passed
        measurement_brier_gate = $measurement.checks.max_edge_brier.passed
        measurement_slippage_gate = $measurement.checks.slippage_reconciliation.passed
        candidate_reason_counts = $candidateDiagnostics.reason_counts
        candidate_actionability_counts = $candidateDiagnostics.actionability_reason_counts
        missing_coverage_reasons = $missingReasons
        missing_coverage_samples = $missingSnapshots
        pending_targets = $summary.pending_targets
        next_action = $summary.next_action
        blockers = ($payload.blockers -join ",")
    } | ConvertTo-Json -Compress
}

Select-PythonEnvironment
Write-Host "Python: $script:PythonExe"
Write-Host "UV cache: $env:UV_CACHE_DIR"

switch ($Action) {
    "status" {
        Invoke-Python -m analysis.high_reward_paper_status --json
    }
    "summary" {
        Show-StatusSummary
    }
    "tick" {
        Show-TickSummary
    }
    "run-once" {
        Invoke-Python -m app.collectors.run_once all --high-reward-fast-lane --json
        Invoke-Python -m analysis.high_reward_paper_status --json
    }
    "scheduler" {
        Invoke-Python -m uvicorn app.main:app --port $Port
    }
}
