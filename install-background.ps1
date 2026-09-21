# Registers Email Triage as a background job that starts when you log in and keeps running.
# Run once:  powershell -ExecutionPolicy Bypass -File install-background.ps1
# Remove:    powershell -ExecutionPolicy Bypass -File uninstall-background.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$taskName = "Email Triage Watcher"

# Prefer the project's virtual environment; fall back to the Python on PATH.
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { Write-Host "Python not found. Run setup.ps1 first." -ForegroundColor Red; exit 1 }
    $pyw = Join-Path (Split-Path $py) "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $py }
}

$action = New-ScheduledTaskAction -Execute $pyw -Argument "-m emailtriage serve --no-browser" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew -StartWhenAvailable

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Email Triage: polls the Outlook inbox, tags mail, drafts replies, serves the dashboard on http://127.0.0.1:8765" | Out-Null

Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 4
$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "Installed '$taskName' (state: $state)." -ForegroundColor Green
Write-Host "It starts automatically at logon and restarts itself if it stops."
Write-Host "Dashboard: http://127.0.0.1:8765   Log: $root\data\watcher.log"
Write-Host "Note: classic Outlook must be running (or able to start) for the watcher to read mail."
