# =============================================================================
# watch_local_server.ps1 - Auto-recovery watchdog for the local FastAPI server
# -----------------------------------------------------------------------------
# - Checks /health every 10s
# - If healthy -> does nothing
# - If unreachable -> waits briefly, restarts via start_local.ps1, waits for
#   /health, verifies /dashboard, logs the recovery event
# - Exponential backoff on repeated startup failures: 10s -> 20s -> 40s -> 60s (max)
# - Never spawns a second uvicorn (start_local.ps1 detects an existing healthy
#   server and exits)
# - Run with:  powershell -ExecutionPolicy Bypass -File scripts\watch_local_server.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$LogsDir   = Join-Path $Root "logs"
$RecoveryLog = Join-Path $LogsDir "recovery.log"
$BaseUrl   = "http://127.0.0.1:8000"

New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$stamp] [$Level] [watchdog] $Message"
    Write-Host $line
    Add-Content -Path $RecoveryLog -Value $line -Encoding utf8
}

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Test-Page {
    param([string]$Path)
    try {
        $r = Invoke-WebRequest -Uri "$BaseUrl$Path" -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

# Exponential backoff intervals (seconds)
$backoff = @(10, 20, 40, 60)
$failIdx = 0
$MAX_BACKOFF = 60

# Guard against multiple watchdogs racing: use a simple mutex file lock.
$lockPath = Join-Path $LogsDir "watchdog.lock"
$lock = $null
try {
    $lock = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    Write-Log "Another watchdog instance appears to be running ($lockPath locked). Exiting." "WARN"
    exit 0
}

Write-Log "Watchdog started. Checking $BaseUrl/health every 10s."
Write-Log "NOTE: This console must remain open while the watchdog runs."

try {
    while ($true) {
        if (Test-Health) {
            $failIdx = 0   # reset backoff on success
            Start-Sleep -Seconds 10
            continue
        }

        # Server unreachable -> attempt recovery
        $delay = $backoff[[Math]::Min($failIdx, $backoff.Count - 1)]
        Write-Log "Server unreachable. Restarting in ${delay}s (attempt $($failIdx + 1))." "WARN"
        Start-Sleep -Seconds $delay

        # Restart via start_local.ps1 (idempotent - will not double-start)
        try {
            & (Join-Path $ScriptDir "start_local.ps1") | Out-Host
        } catch {
            Write-Log "start_local.ps1 threw: $($_.Exception.Message)" "ERROR"
        }

        # Wait up to 90s for health after restart
        $deadline = (Get-Date).AddSeconds(90)
        $recovered = $false
        while ((Get-Date) -lt $deadline) {
            if (Test-Health) { $recovered = $true; break }
            Start-Sleep -Seconds 3
        }

        if ($recovered) {
            $dash = Test-Page "/dashboard"
            $term = Test-Page "/terminal"
            Write-Log "RECOVERY: server is back up. /dashboard=$dash /terminal=$term."
            $failIdx = 0
        } else {
            Write-Log "RECOVERY FAILED: server did not become healthy after restart. Will retry with longer backoff." "ERROR"
            $failIdx = [Math]::Min($failIdx + 1, $backoff.Count - 1)
        }
    }
}
finally {
    if ($lock) { $lock.Dispose() }
    Remove-Item $lockPath -ErrorAction SilentlyContinue
}
