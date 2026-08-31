param(
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI не найден. Установите Docker Desktop и повторите запуск."
}

$composeArgs = @("compose", "up", "-d")
if (-not $NoBuild) {
    $composeArgs += "--build"
}
& docker @composeArgs

for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 3
        if ([int]$health.StatusCode -eq 200) {
            break
        }
    } catch {
        if ($attempt -eq 30) { throw }
    }
    Start-Sleep -Seconds 2
}

$ready = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/ready" -TimeoutSec 10
if ([int]$ready.StatusCode -ne 200) {
    throw "API health is up but readiness failed with HTTP $($ready.StatusCode): $($ready.Content)"
}

$openapi = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/openapi.json" -TimeoutSec 10
if ([int]$openapi.StatusCode -ne 200) {
    throw "OpenAPI smoke failed with HTTP $($openapi.StatusCode)"
}

Write-Host "PASS: /health=200, /ready=200, /openapi.json=200"
Write-Host "Стек оставлен запущенным; остановка: docker compose down"
