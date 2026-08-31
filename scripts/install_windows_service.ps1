# =============================================================================
# install_windows_service.ps1 - Register/Unregister the XAU Terminal watchdog
# as a Windows Scheduled Task so the server survives terminal close + reboots.
# -----------------------------------------------------------------------------
# INSTALL:
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows_service.ps1
#
# UNINSTALL:
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows_service.ps1 -Uninstall
#
# The scheduled task runs scripts\watch_local_server.ps1 at logon and boot.
# That script keeps uvicorn alive on localhost:8000 and auto-recovers crashes.
# =============================================================================

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$XmlPath   = Join-Path $ScriptDir "xau_terminal_watchdog.xml"
$TaskName  = "XAU_Terminal_Watchdog"

if ($Uninstall) {
    Write-Host "Unregistering scheduled task '$TaskName'..."
    schtasks /Delete /TN $TaskName /F 2>$null
    Write-Host "Done. The server will no longer auto-start at logon/boot."
    exit 0
}

if (-not (Test-Path $XmlPath)) {
    Write-Host "ERROR: task XML not found at $XmlPath" -ForegroundColor Red
    exit 1
}

# Replace the hardcoded %USERNAME% placeholder with the current user
$user = $env:USERNAME
$content = Get-Content $XmlPath -Raw
$content = $content -replace "%USERNAME%", $user
$tmpXml = Join-Path $env:TEMP "xau_terminal_watchdog.xml"
Set-Content -Path $tmpXml -Value $content -Encoding UTF8

Write-Host "Registering scheduled task '$TaskName' for user $user ..."
schtasks /Create /TN $TaskName /XML $tmpXml /F

Remove-Item $tmpXml -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Scheduled task installed successfully." -ForegroundColor Green
Write-Host "  Name      : $TaskName"
Write-Host "  Triggers  : At logon + at boot (30s delay)"
Write-Host "  Action    : $Root\scripts\watch_local_server.ps1"
Write-Host "  Behavior  : keeps localhost:8000 healthy, auto-recovers crashes"
Write-Host ""
Write-Host "Run it now:" -ForegroundColor Cyan
Write-Host "  schtasks /Run /TN $TaskName"
Write-Host ""
Write-Host "Manual control:" -ForegroundColor Cyan
Write-Host "  Status   : schtasks /Query /TN $TaskName"
Write-Host "  Stop     : schtasks /End /TN $TaskName"
Write-Host "  Restart  : schtasks /Run /TN $TaskName"
Write-Host "  Remove   : powershell -ExecutionPolicy Bypass -File scripts\install_windows_service.ps1 -Uninstall"
