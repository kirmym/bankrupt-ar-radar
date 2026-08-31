param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI не найден. Установите Docker Desktop и повторите запуск."
}

$composeArgs = @("compose", "up", "-d", "postgres")
if ($Build) {
    $composeArgs += "--build"
}
& docker @composeArgs

for ($attempt = 1; $attempt -le 30; $attempt++) {
    $null = & docker compose exec -T postgres pg_isready -U postgres -d ar_radar 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "PostgreSQL готов: localhost:5432/ar_radar"
        exit 0
    }
    Start-Sleep -Seconds 2
}

throw "PostgreSQL не перешёл в ready за 60 секунд. Проверьте: docker compose logs postgres"
