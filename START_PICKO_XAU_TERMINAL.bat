@echo off
REM START_PICKO_XAU_TERMINAL.bat - double-click launcher for the XAU AI Terminal.
REM Opens a single-worker local server at http://localhost:8000 and the dashboard.

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\start_local.ps1"
if errorlevel 1 (
    echo.
    echo Server failed to start. See docs\LOCAL_WINDOWS_SERVICE.md and logs\recovery.log
    pause
)
