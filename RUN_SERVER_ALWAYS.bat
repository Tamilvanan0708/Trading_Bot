@echo off
title XAU AI Bot 24/7 Server Runner
cd /d "%~dp0"

echo ===================================================
echo   XAU/USD AI Trading Bot - 24/7 Auto-Restart Server
echo ===================================================

set PYTHON_CMD=python
if exist .venv\Scripts\python.exe (
    set PYTHON_CMD=.venv\Scripts\python.exe
)

:loop
echo [%date% %time%] Syncing latest updates from GitHub...
git pull origin main
echo [%date% %time%] Starting Uvicorn Server with %PYTHON_CMD%...
%PYTHON_CMD% -m uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --workers 1

echo.
echo [WARNING] Server stopped or crashed at %time%!
echo [INFO] Auto-restarting in 5 seconds... (Press Ctrl+C to cancel)
timeout /t 5 >nul
goto loop
