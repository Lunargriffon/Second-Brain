$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$syncScript = Join-Path $repoRoot "scripts\sync-monthly.ps1"
$taskName = "Second-Brain Monthly Sync"
$taskRun = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$syncScript`""

& schtasks.exe /Create `
    /TN $taskName `
    /TR $taskRun `
    /SC MONTHLY `
    /D 1 `
    /ST 03:00 `
    /F | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw "Failed to register scheduled task: $taskName"
}

Write-Output "Registered scheduled task: $taskName"
