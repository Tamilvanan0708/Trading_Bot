# check_local_server.ps1 - prints a short health snapshot of the local server.

$ErrorActionPreference = "Stop"
$HealthUrl = "http://localhost:8000/health"

try {
    $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 5 -UseBasicParsing
    if ($resp.StatusCode -eq 200) {
        $data = $resp.Content | ConvertFrom-Json
        Write-Host ("server: UP   status: {0}   feed_connected: {1}   db_ok: {2}" -f `
            $data.status, $data.feed_connected, $data.db_ok)
        Write-Host "Dashboard: http://localhost:8000/dashboard"
        Write-Host "Terminal:  http://localhost:8000/terminal"
        exit 0
    } else {
        Write-Host ("server responded HTTP {0}" -f $resp.StatusCode)
        exit 2
    }
} catch {
    Write-Host "server: DOWN (no /health response at $HealthUrl). Run scripts/start_local.ps1"
    exit 3
}
