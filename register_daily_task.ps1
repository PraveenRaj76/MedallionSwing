# Register Windows Scheduled Task: Medallion Swing daily refresh at 01:00 IST
# Run once (PowerShell as your user; Admin not required for current-user task):
#   .\register_daily_task.ps1
#   .\register_daily_task.ps1 -Unregister

param(
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$taskName = "MedallionSwingDailyRefresh"
$py = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$job = Join-Path $PSScriptRoot "daily_refresh_job.py"
$log = Join-Path $PSScriptRoot "daily_refresh_task_stdout.txt"

if (-not (Test-Path $py)) {
    Write-Error "venv python not found: $py"
}
if (-not (Test-Path $job)) {
    Write-Error "Job script not found: $job"
}

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $taskName"
    exit 0
}

# India Standard Time = IST (UTC+5:30). Windows uses this timezone id.
$tz = "India Standard Time"
$action = New-ScheduledTaskAction `
    -Execute $py `
    -Argument "`"$job`"" `
    -WorkingDirectory $PSScriptRoot

# 01:00 every day
$trigger = New-ScheduledTaskTrigger -Daily -At "01:00"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15)

# Run whether user is logged on or not requires password; use Interactive for simplicity.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Medallion Swing: wipe + reload Nifty prices/fundamentals/technicals at 01:00 IST so Screener is ready by morning." `
    | Out-Null

# Force timezone on the trigger if possible (Daily -At uses local PC clock).
# Ensure PC timezone is India Standard Time for true 1 AM IST.
$pcTz = (Get-TimeZone).Id
Write-Host "Registered task: $taskName"
Write-Host "Schedule: daily 01:00 (PC clock)"
Write-Host "PC timezone: $pcTz"
if ($pcTz -ne $tz) {
    Write-Host "WARNING: PC timezone is not '$tz'. Set Windows time zone to India for true 1 AM IST,"
    Write-Host "         or change the task trigger time to match IST on your clock."
}
Write-Host "Job: $py $job"
Write-Host "App log: daily_refresh_log.txt (created when job runs)"
Write-Host ""
Write-Host "Test now (optional):"
Write-Host "  Start-ScheduledTask -TaskName $taskName"
Write-Host "Or run once manually:"
Write-Host "  .\venv\Scripts\python.exe daily_refresh_job.py"
