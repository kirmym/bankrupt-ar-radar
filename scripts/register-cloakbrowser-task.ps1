param(
    [string]$TaskName = "BankruptAR-CloakBrowser"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot "runtime"
if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot ".venv\Scripts\python.exe"))) {
    $runtimeRoot = Join-Path (Split-Path -Parent $repoRoot) "runtime"
}
$python = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
$launcher = Join-Path $PSScriptRoot "start_cloakbrowser_cdp.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Runtime Python не найден: $python"
}

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Write-Host "Задача $TaskName зарегистрирована для пользователя $env:USERNAME"
Write-Host "Удаление: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
