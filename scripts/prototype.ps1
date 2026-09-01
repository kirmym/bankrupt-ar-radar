[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 900,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
Set-Location $repoRoot
$composeProject = if ($env:AR_RADAR_COMPOSE_PROJECT) { $env:AR_RADAR_COMPOSE_PROJECT } else { "ar-radar-prototype" }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker не найден в PATH. Установите Docker Desktop и повторите запуск."
}

$composeArgs = @("compose", "-p", $composeProject, "up", "-d")
if (-not $SkipBuild) {
    $composeArgs += "--build"
}
& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось запустить docker compose."
}

$apiBase = "http://127.0.0.1:8000"
$headers = @{}
if ($env:API_AUTH_TOKEN) {
    $headers["X-API-Key"] = $env:API_AUTH_TOKEN
}

function Invoke-ApiJson {
    param([Parameter(Mandatory = $true)][string]$Path)
    Invoke-RestMethod -Uri ($apiBase + $Path) -Headers $headers -TimeoutSec 20
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $probe = Invoke-ApiJson "/ready"
        if ($probe.status -eq "ok") {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 3
    }
}
if (-not $ready) {
    throw "API не стал ready за $TimeoutSeconds секунд. Проверьте: docker compose logs app"
}

$ingest = $null
while ((Get-Date) -lt $deadline) {
    try {
        $ingest = Invoke-ApiJson "/api/v1/ingest/status"
        if ($ingest.run -and $ingest.run.status -in @("finished", "failed")) {
            break
        }
    } catch {
        # Первый worker-cycle мог ещё не создать запись import_runs.
    }
    Start-Sleep -Seconds 5
}

$pipelineReady = $false
while ((Get-Date) -lt $deadline) {
    try {
        $stats = Invoke-ApiJson "/api/v1/stats"
        $workers = Invoke-ApiJson "/api/v1/workers/status"
        $enrichStatus = $workers.workers.enrich.status
        $scoreStatus = $workers.workers.score.status
        if ($stats.scored_lots -gt 0 -and $enrichStatus -ne "running" -and $scoreStatus -ne "running") {
            $pipelineReady = $true
            break
        }
    } catch {
        # A worker may still be opening its first database connection.
    }
    Start-Sleep -Seconds 5
}
if (-not $pipelineReady) {
    throw "Enrich/score не завершили первый цикл за $TimeoutSeconds секунд. Проверьте: docker compose -p $composeProject logs app"
}

$stats = Invoke-ApiJson "/api/v1/stats"
$review = Invoke-ApiJson "/api/v1/lots?page=1&page_size=10&view=review&sort_by=deadline&sort_order=asc"
$report = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    ready = $ready
    pipeline_ready = $pipelineReady
    ingest = $ingest
    stats = $stats
    review_top10 = $review.items
}

$outputDir = Join-Path $repoRoot "outputs"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
$outputFile = Join-Path $outputDir ("prototype-{0}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $outputFile -Encoding UTF8

Write-Host "PASS: API ready=$ready"
Write-Host ("Source status: {0}; active lots: {1}; review candidates: {2}; ready A/B: {3}" -f `
    $stats.source_status, $stats.active_lots, $stats.review_candidates, $stats.ready_recommendations)
Write-Host "Отчёт сохранён: $outputFile"
