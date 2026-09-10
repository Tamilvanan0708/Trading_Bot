# Local Windows Service

This guide runs the XAU AI Terminal as an auto-restarting background process
on Windows, WITHOUT a real Windows service, so a closed terminal cannot kill
the bot. The scheduled watchdog re-launches the server within ~1 minute if the
`/health` endpoint on **http://localhost:8000** stops responding.

> **Single worker only.** The scheduler and SQLite are not multi-worker safe.
> All launch paths force `--workers 1`. Never configure more than one process
> (one worker) — see `docs/DEPLOYMENT.md` for the same constraint on Linux/VPS.

## One-click start (no install)

Double-click `START_PICKO_XAU_TERMINAL.bat` in the repo root, or run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1
```

`start_local.ps1`:

* probes `http://localhost:8000/health` first; if a health server is already
  running it prints *"already healthy"* and does **NOT** start a
  **duplicate** process;
* otherwise launches `uvicorn app.api.app:app --host 127.0.0.1 --port 8000
  --workers 1`;
* waits up to 60 seconds for `/health` to return 200.

Check status any time:

```powershell
powershell -File scripts\check_local_server.ps1
```

## Auto-restart every minute (scheduled task)

`watch_local_server.ps1` is the watchdog. It uses a `watchdog.lock` file as a
**single-instance guard** (overlapping runs exit immediately), calls
`/health`, and on failure performs **RECOVERY**: it invokes
`start_local.ps1` with a backoff growing from 10 seconds up to 60 seconds
between attempts, logging each recovery to `logs/recovery.log`.

### Install

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_windows_service.ps1
```

This registers a `schtasks` task named `XAU-AI-Terminal-Watchdog` that runs
`watch_local_server.ps1` **every minute** for the current user. You can do the
same by hand:

```bat
schtasks /create /f /tn "XAU-AI-Terminal-Watchdog" /sc minute /mo 1 ^
  /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"\"%CD%\scripts\watch_local_server.ps1\"\""
```

The equivalent Task Scheduler definition is checked in at
`scripts/xau_terminal_watchdog.xml` (import with
`schtasks /create /xml scripts\xau_terminal_watchdog.xml /tn "XAU-AI-Terminal-Watchdog"`,
editing `%WD%` to your repo path first).

### Uninstall

To remove the scheduled task (uninstall):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_windows_service.ps1 -Uninstall
```

or:

```bat
schtasks /delete /tn "XAU-AI-Terminal-Watchdog" /f
```

Uninstalling the task does **not** stop an already-running server. To stop the
server, close the `start_local.ps1` window or:

```bat
taskkill /IM python.exe /FI "WINDOWTITLE eq *uvicorn*"
```

## Verifying

```powershell
powershell -File scripts\check_local_server.ps1
curl http://localhost:8000/health
```

Expected keys in `/health`: `status`, `feed_connected`, `db_ok`, `ai_provider`.

## Files

| Path | Purpose |
|---|---|
| `START_PICKO_XAU_TERMINAL.bat` | Double-click launcher (single worker) |
| `scripts/start_local.ps1` | Start/reuse the local server, `--workers 1` |
| `scripts/check_local_server.ps1` | Print health snapshot |
| `scripts/watch_local_server.ps1` | RECOVERY watchdog with backoff + lock guard |
| `scripts/install_windows_service.ps1` | Install (schtasks) / uninstall the watchdog |
| `scripts/xau_terminal_watchdog.xml` | Task Scheduler definition for the watchdog |

All PowerShell scripts and this doc are pure ASCII so Windows PowerShell 5.1
parses them without a BOM.
