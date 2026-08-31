# =============================================================================
# check_local_server.ps1 - Real health verification (not just TCP port open)
# -----------------------------------------------------------------------------
# Verifies the FastAPI /health endpoint and reports:
#   SERVER / API / DATABASE / FEED / SCHEDULER / PORT
# Also verifies /dashboard, /terminal, /docs.
# Exits 0 when the server is genuinely healthy, non-zero otherwise.
# =============================================================================

$ErrorActionPreference = "Stop"
$BaseUrl = "http://127.0.0.1:8000"
$HostUrl = "http://localhost:8000"

function Write-Line($Label, $Value, $Color = "White") {
    Write-Host ("{0,-12}: {1}" -f $Label, $Value) -ForegroundColor $Color
}

# --- 1. TCP port check -------------------------------------------------------
$portOwner = $null
$lines = netstat -ano | Select-String ":8000\s" | Select-String "LISTENING"
foreach ($ln in $lines) {
    $tokens = ($ln.ToString().Trim() -split "\s+")
    if ($tokens.Count -ge 5) { $portOwner = $tokens[$tokens.Count - 1] }
}
$portOpen = ($portOwner -ne $null)
Write-Line "PORT" "8000 $(if ($portOpen) { "(LISTENING, PID $portOwner)" } else { "(not listening)" })" $(if ($portOpen) { "Green" } else { "Red" })

# --- 2. /health ---------------------------------------------------------------
$health = $null
try {
    $r = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
    if ($r.StatusCode -eq 200) {
        $health = $r.Content | ConvertFrom-Json
        Write-Line "SERVER" "UP" "Green"
    } else {
        Write-Line "SERVER" "UP (HTTP $($r.StatusCode), but not 200)" "Yellow"
    }
} catch {
    Write-Line "SERVER" "DOWN" "Red"
    Write-Host "  /health request failed: $($_.Exception.Message)" -ForegroundColor Red
}

if ($health -eq $null) {
    Write-Line "API" "UNHEALTHY" "Red"
    Write-Line "DATABASE" "UNKNOWN" "Yellow"
    Write-Line "FEED" "UNKNOWN" "Yellow"
    Write-Line "SCHEDULER" "UNKNOWN" "Yellow"
    exit 1
}

$apiStatus = if ($health.status -eq "healthy") { "HEALTHY" } else { "UNHEALTHY" }
Write-Line "API" $apiStatus $(if ($health.status -eq "healthy") { "Green" } else { "Red" })

$dbStatus = if ($health.db_ok) { "OK" } else { "FAILED" }
Write-Line "DATABASE" $dbStatus $(if ($health.db_ok) { "Green" } else { "Red" })

$feedConnected = $health.feed_connected
$feedStatus = if ($feedConnected) { "CONNECTED" } else { "DISCONNECTED" }
Write-Line "FEED" $feedStatus $(if ($feedConnected) { "Green" } else { "Yellow" })

$schedRunning = $health.scheduler_running
$schedStatus = if ($schedRunning) { "RUNNING" } else { "DEGRADED" }
Write-Line "SCHEDULER" $schedStatus $(if ($schedRunning) { "Green" } else { "Yellow" })

if ($health.data_status) { Write-Line "DATA_STATUS" $health.data_status }
if ($health.ai_provider) {
    Write-Line "AI_PROVIDER" $health.ai_provider.status $(if ($health.ai_provider.status -eq "AVAILABLE") { "Green" } else { "Yellow" })
}

# --- 3. Core pages ------------------------------------------------------------
$allOk = $true
foreach ($path in @("/dashboard", "/terminal", "/docs")) {
    try {
        $r = Invoke-WebRequest -Uri "$BaseUrl$path" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        $label = $path.TrimStart("/").ToUpper()
        if ($r.StatusCode -eq 200) { Write-Line $label "200 OK" "Green" }
        else { Write-Line $label "HTTP $($r.StatusCode)" "Red"; $allOk = $false }
    } catch {
        Write-Line $path "FAILED" "Red"
        $allOk = $false
    }
}

Write-Host ""
Write-Host "  Dashboard : $HostUrl/dashboard" -ForegroundColor Cyan
Write-Host "  Terminal  : $HostUrl/terminal"  -ForegroundColor Cyan
Write-Host "  API docs  : $HostUrl/docs"      -ForegroundColor Cyan
Write-Host "  Health    : $HostUrl/health"    -ForegroundColor Cyan
Write-Host ""

if ($health.status -eq "healthy" -and $allOk) { exit 0 } else { exit 1 }
