@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM START_PICKO_XAU_TERMINAL.bat - One-click start for the XAU/USD Terminal
REM ----------------------------------------------------------------------------
REM 1. Starts (or reuses) the local FastAPI server via scripts\start_local.ps1
REM 2. Waits until /health returns HTTP 200
REM 3. Opens http://localhost:8000/dashboard ONLY after the server is healthy
REM ============================================================================

cd /d "%~dp0"

echo.
echo === XAU/USD AI Signal Intelligence Terminal ===
echo.

REM --- Locate PowerShell ------------------------------------------------------
set "PS=powershell.exe"
where powershell.exe >nul 2>nul
if errorlevel 1 set "PS=pwsh.exe"

REM --- Launch the robust start script ------------------------------------------
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "scripts\start_local.ps1"
set "START_RC=%ERRORLEVEL%"

if "%START_RC%" neq "0" (
    echo.
    echo [ERROR] Server startup failed. See logs\startup.log and logs\local_server.log
    echo.
    pause
    exit /b 1
)

REM --- Wait until /health returns 200 (defensive; start script already waits) --
set "HEALTHY="
for /l %%i in (1,1,45) do (
    "%PS%" -NoProfile -Command "try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
    if "!errorlevel!"=="0" (
        set "HEALTHY=1"
        goto :healthy
    )
    timeout /t 2 /nobreak >nul
)

:healthy
if not defined HEALTHY (
    echo.
    echo [ERROR] Server did not become healthy in time.
    echo         Check logs\local_server.log for details.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Server is healthy.
echo.
echo Opening dashboard: http://localhost:8000/dashboard
echo.

start "" "http://localhost:8000/dashboard"

echo.
echo   Dashboard : http://localhost:8000/dashboard
echo   Terminal  : http://localhost:8000/terminal
echo   API docs  : http://localhost:8000/docs
echo   Health    : http://localhost:8000/health
echo.
echo The server keeps running in the background. To stop it:
echo   powershell -Command "Get-Process | Where-Object {$_.ProcessName -match 'python'} | Where-Object {$_.Path -match 'trading_view'} | Stop-Process -Force"
echo.
timeout /t 5 /nobreak >nul
endlocal
