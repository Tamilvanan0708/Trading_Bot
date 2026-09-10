# install_windows_service.ps1 - registers/unregisters the XAU AI Terminal
# watchdog as a Windows scheduled task (runs every minute, so a dead local
# server is RECOVERY-ed automatically).
#
#   Install:   powershell -File install_windows_service.ps1
#   Uninstall: powershell -File install_windows_service.ps1 -Uninstall

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TaskName = "XAU-AI-Terminal-Watchdog"
$WatchScript = Join-Path $PSScriptRoot "watch_local_server.ps1"

if ($Uninstall) {
    schtasks /delete /tn $TaskName /f
    Write-Host "Uninstalled scheduled task $TaskName."
    exit 0
}

# Register: run the watchdog every minute for the current user.
schtasks /create /f /tn $TaskName `
    /sc minute /mo 1 `
    /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"\"$WatchScript\"\""`

Write-Host "Installed scheduled task $TaskName (watchdog runs every minute)."
Write-Host "Health:  http://localhost:8000/health"
Write-Host "Remove with: powershell -File install_windows_service.ps1 -Uninstall"
