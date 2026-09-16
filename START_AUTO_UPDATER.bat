@echo off
REM START_AUTO_UPDATER.bat - Starts the automatic GitHub pull & safe restart watcher.
title XAU Terminal - Auto Updater Watcher
cd /d "%~dp0"

echo =======================================================
echo   XAU AI Terminal - Automatic GitHub Pull Watcher
echo =======================================================
echo.
echo Polling GitHub 'main' branch every 60 seconds...
echo Trade Safety Guard: ENABLED (Zero disruption to live trades)
echo.

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe scripts\auto_updater.py
) else (
    python scripts\auto_updater.py
)

if errorlevel 1 (
    echo.
    echo Auto updater stopped.
    pause
)
