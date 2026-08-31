# =============================================================================
# start_local.ps1 - Reliable local startup for the XAU/USD AI Signal Terminal
# -----------------------------------------------------------------------------
# - Resolves the project root automatically (scripts/..)
# - Verifies the venv + application import
# - Detects an already-running healthy server (does NOT start a duplicate)
# - Detects a stale/dead process owning port 8000 and stops ONLY that process
# - Never kills unrelated applications on port 8000
# - Starts uvicorn detached from this console (survives console close)
# - Waits for /health, then verifies /dashboard /terminal /docs
# - Writes server logs to logs\local_server.log
# =============================================================================

$ErrorActionPreference = "Stop"

# --- 1. Resolve project root + paths ----------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$LogsDir   = Join-Path $Root "logs"
$VenvDir   = Join-Path $Root ".venv"
$Python    = Join-Path $VenvDir "Scripts\python.exe"
$ServerLog = Join-Path $LogsDir "local_server.log"
$StartupLog= Join-Path $LogsDir "startup.log"
$PidFile   = Join-Path $LogsDir "uvicorn.pid"

$Port    = 8000
$BaseUrl = "http://127.0.0.1:$Port"
$HostUrl = "http://localhost:$Port"

# --- 2. Helper functions -----------------------------------------------------
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$stamp] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $StartupLog -Value $line -Encoding utf8
}

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { return $true }
    } catch { }
    return $false
}

function Get-PortOwner {
    # Returns the PID listening on $Port, or $null
    $lines = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"
    foreach ($ln in $lines) {
        $tokens = ($ln.ToString().Trim() -split "\s+")
        if ($tokens.Count -ge 5) {
            $p = $tokens[$tokens.Count - 1]
            if ($p -match "^\d+$" -and [int]$p -gt 0) { return [int]$p }
        }
    }
    return $null
}

# --- 3. Verify venv + app import --------------------------------------------
if (-not (Test-Path $Python)) {
    Write-Log "ERROR: venv not found at $VenvDir - run: python -m venv .venv"
    Write-Log "ERROR: then: .venv\Scripts\pip install -r requirements.txt"
    exit 1
}
Set-Location $Root

try {
    $importCheck = & $Python -c "import app.api.app; print('OK')" 2>&1
    if ("$importCheck" -notmatch "OK") {
        Write-Log "ERROR: application import failed:"
        Write-Log $importCheck
        exit 1
    }
} catch {
    Write-Log "ERROR: application import raised: $($_.Exception.Message)"
    exit 1
}

# --- 4. Port / existing server detection ------------------------------------
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

if (Test-Health) {
    Write-Log "Server already healthy at $HostUrl - not starting a duplicate."
    Write-Host ""
    Write-Host "  Dashboard : $HostUrl/dashboard" -ForegroundColor Green
    Write-Host "  Terminal  : $HostUrl/terminal"  -ForegroundColor Green
    Write-Host "  API docs  : $HostUrl/docs"      -ForegroundColor Green
    Write-Host "  Health    : $HostUrl/health"    -ForegroundColor Green
    exit 0
}

$ownerPid = Get-PortOwner
if ($ownerPid -ne $null) {
    # Port occupied but /health failed -> possible stale/dead uvicorn.
    $ownerProc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    $isPython = ($ownerProc -ne $null) -and ($ownerProc.ProcessName -match "python")
    if ($isPython) {
        Write-Log "Port $Port is owned by a stale/dead Python process (PID $ownerPid). Stopping it..."
        try {
            Stop-Process -Id $ownerPid -Force -ErrorAction Stop
            Write-Log "Stopped stale PID $ownerPid."
            Start-Sleep -Seconds 2
        } catch {
            Write-Log "Failed to stop stale PID ${ownerPid}: $($_.Exception.Message)" "ERROR"
            Write-Host "Port $Port is occupied by process PID $ownerPid and could not be stopped." -ForegroundColor Red
            Write-Host "Run:  taskkill /PID $ownerPid /F   (only if you are sure it is a stale XAU server)" -ForegroundColor Yellow
            exit 1
        }
    } else {
        Write-Host "Port $Port is occupied by another process: PID $ownerPid" -ForegroundColor Red
        if ($ownerProc) { Write-Host "  Process: $($ownerProc.ProcessName)" -ForegroundColor Yellow }
        Write-Host "Do NOT kill unrelated applications. Resolve the conflict manually, then retry." -ForegroundColor Yellow
        exit 1
    }
}

# --- 5. Start uvicorn (detached, logged) ------------------------------------
Write-Log "Starting uvicorn (single worker) from $Root ..."
$uvicornArgs = @(
    "-m", "uvicorn",
    "app.api.app:app",
    "--host", "0.0.0.0",
    "--port", "8000",
    "--workers", "1"
)

$env:PYTHONUNBUFFERED = "1"
try {
    $proc = Start-Process -FilePath $Python `
        -ArgumentList $uvicornArgs `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $ServerLog `
        -RedirectStandardError (Join-Path $LogsDir "local_server.err.log") `
        -WindowStyle Hidden `
        -PassThru
    Write-Log "Started uvicorn PID $($proc.Id)."
    Set-Content -Path $PidFile -Value $proc.Id -Encoding utf8
} catch {
    Write-Log "ERROR: could not start uvicorn: $($_.Exception.Message)" "ERROR"
    exit 1
}

# --- 6. Wait for /health ----------------------------------------------------
$deadline = (Get-Date).AddSeconds(90)
$healthy  = $false
while ((Get-Date) -lt $deadline) {
    if (Test-Health) { $healthy = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    Write-Log "ERROR: server did not become healthy within 90s. See $ServerLog" "ERROR"
    Get-Content $ServerLog -Tail 40 -ErrorAction SilentlyContinue
    exit 1
}

# --- 7. Verify core pages ---------------------------------------------------
$ok = $true
foreach ($path in @("/dashboard", "/terminal", "/docs")) {
    try {
        $r = Invoke-WebRequest -Uri "$BaseUrl$path" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        if ($r.StatusCode -ne 200) { $ok = $false; Write-Log "WARN: $path returned $($r.StatusCode)" "WARN" }
        else { Write-Log "Verified $path -> 200" }
    } catch {
        $ok = $false
        Write-Log "ERROR: $path failed: $($_.Exception.Message)" "ERROR"
    }
}

# --- 8. Report ---------------------------------------------------------------
Write-Host ""
Write-Host "  Server is UP at $HostUrl" -ForegroundColor Green
Write-Host "  Dashboard : $HostUrl/dashboard" -ForegroundColor Green
Write-Host "  Terminal  : $HostUrl/terminal"  -ForegroundColor Green
Write-Host "  API docs  : $HostUrl/docs"      -ForegroundColor Green
Write-Host "  Health    : $HostUrl/health"    -ForegroundColor Green
Write-Host "  Logs      : $ServerLog"          -ForegroundColor Cyan
Write-Host ""

if ($ok) { exit 0 } else { exit 1 }
