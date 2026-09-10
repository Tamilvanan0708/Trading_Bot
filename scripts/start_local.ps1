# start_local.ps1 - one-click launcher for the XAU AI Terminal local server.
# Ensures a SINGLE uvicorn worker (SQLite + singleton scheduler are not
# multi-worker safe), waits for /health, and never doubles up: if a healthy
# server is already listening, the script says "already healthy" and exits
# WITHOUT spawning anything (not starting a duplicate).

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$HealthUrl = "http://localhost:8000/health"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtualenv python not found: $PythonExe. Create it first: python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
}

# 1. Detect an existing healthy server.
try {
    $probe = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 3 -UseBasicParsing
    if ($probe.StatusCode -eq 200) {
        Write-Host "Server already healthy at $HealthUrl - not starting a duplicate."
        exit 0
    }
} catch {
    Write-Host "No healthy server found at $HealthUrl; starting one..."
}

Set-Location $RepoRoot

# 2. Launch uvicorn with EXACTLY one worker.
$proc = Start-Process -FilePath $PythonExe `
    -ArgumentList "-m", "uvicorn", "app.api.app:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1" `
    -WorkingDirectory $RepoRoot -PassThru -WindowStyle Minimized

# 3. Wait for /health (up to 60s).
$deadline = (Get-Date).AddSeconds(60)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 3 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
}

if ($healthy) {
    Write-Host "XAU AI Terminal is healthy at http://localhost:8000 (pid $($proc.Id))."
    Start-Process "http://localhost:8000/terminal"
} else {
    Write-Error "Server did not become healthy within 60s. Check logs or run scripts/check_local_server.ps1"
    exit 1
}
