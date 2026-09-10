# watch_local_server.ps1 - watchdog: if the local server stops answering
# /health, restart it via start_local.ps1. Runs under the scheduled task
# (see xau_terminal_watchdog.xml / install_windows_service.ps1) every minute.
#
# - Single-instance guard: a watchdog.lock file prevents overlapping runs.
# - Linear backoff between 10 and 60 seconds between restart attempts.

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs"
$HealthUrl = "http://localhost:8000/health"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# --- single-instance guard via watchdog.lock ---------------------------------
$LockFile = Join-Path $LogDir "watchdog.lock"
if (Test-Path $LockFile) {
    $age = ((Get-Date) - (Get-Item $LockFile).LastWriteTime).TotalSeconds
    if ($age -lt 120) {
        Write-Output "watchdog: another instance active (lock age $([int]$age)s); exiting."
        exit 0
    }
}
Set-Content -Path $LockFile -Value (Get-Date -Format "o")

try {
    $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 5 -UseBasicParsing
    if ($resp.StatusCode -eq 200) {
        Remove-Item $LockFile -Force
        exit 0   # healthy: nothing to do
    }
} catch { }

# --- RECOVERY pass -------------------------------------------------------------
Add-Content -Path (Join-Path $LogDir "recovery.log") -Value ("{0} RECOVERY: /health unreachable; restarting local server" -f (Get-Date -Format "o"))

$attempt = 1
$BackoffSeconds = 10          # first wait: 10s, growing to at most 60s between attempts
while ($attempt -le 5) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start_local.ps1")
    Start-Sleep -Seconds $BackoffSeconds
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 5 -UseBasicParsing
        if ($resp.StatusCode -eq 200) {
            Add-Content -Path (Join-Path $LogDir "recovery.log") -Value ("{0} RECOVERY: server healthy after attempt {1}" -f (Get-Date -Format "o"), $attempt)
            break
        }
    } catch { }
    $attempt = $attempt + 1
    if ($BackoffSeconds -lt 60) { $BackoffSeconds = [Math]::Min(60, $BackoffSeconds + 10) }
}

Remove-Item $LockFile -Force
exit 0
