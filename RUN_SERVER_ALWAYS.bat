@echo off
title XAU AI Bot 24/7 Server Runner
cd /d "%~dp0"

echo ===================================================
echo   XAU/USD AI Trading Bot - 24/7 Auto-Restart Server
echo ===================================================

:loop
echo [%date% %time%] Starting Uvicorn Server...
python -m uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --workers 1

echo.
echo [WARNING] Server stopped or crashed at %time%!
echo [INFO] Auto-restarting in 5 seconds... (Press Ctrl+C to cancel)
timeout /t 5 >nul
goto loop
