# Start the XAU/USD Trading Agent on Windows.
# Creates a virtualenv on first run, installs deps, and runs uvicorn with a
# single worker (required for SQLite + scheduler).  Restarts automatically if
# the process exits.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv")) {
    Write-Host "[setup] Creating virtualenv..."
    python -m venv .venv
}
$Python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found at $Python"
    exit 1
}

& $Python -m pip install -q -r requirements.txt

Write-Host "[start] Launching XAU/USD agent (single worker)..."
while ($true) {
    & $Python -m uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --workers 1
    Write-Host "[restart] Process exited ($LASTEXITCODE). Restarting in 5s..."
    Start-Sleep -Seconds 5
}
