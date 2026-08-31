# LOCAL WINDOWS SERVICE - XAU/USD AI Signal Intelligence Terminal

This guide makes the terminal reliably reachable on `http://localhost:8000`
**without** requiring you to keep a terminal/PowerShell window open, and it
**auto-recovers** if the server crashes.

No Docker, no WSL, no NSSM download. It uses the built-in **Windows Task
Scheduler** to run a lightweight watchdog script.

---

## How it works

```
Windows Task Scheduler  (at logon + at boot)
        |
        v
 scripts\watch_local_server.ps1   (hidden, keeps running)
        |  checks /health every 10s
        v
  healthy? -- yes --> do nothing
        |
        no
        v
 scripts\start_local.ps1   (starts uvicorn detached, waits for health)
        |
        v
  /health 200 -> /dashboard /terminal /docs verified -> logs recovery
```

The actual uvicorn process is started **detached** (`Start-Process ... -WindowStyle
Hidden`) so closing any console never kills it. The watchdog keeps it alive and
restarts it on crash with exponential backoff (10s -> 20s -> 40s -> 60s max).

The server runs with `--workers 1` (single worker). SQLite + the scheduler
require exactly one worker. Never run with `--workers > 1`.

---

## Install (persistent auto-start service)

Run once (from `D:\trading_view`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows_service.ps1
```

This registers a scheduled task named `XAU_Terminal_Watchdog` that:

- Starts **at logon**
- Starts **at boot** (30s delay)
- Restarts itself on failure
- Runs `watch_local_server.ps1` hidden

Start it now:

```powershell
schtasks /Run /TN XAU_Terminal_Watchdog
```

---

## Status / Stop / Restart / Uninstall

| Action | Command |
|--------|---------|
| Status | `schtasks /Query /TN XAU_Terminal_Watchdog` |
| Stop | `schtasks /End /TN XAU_Terminal_Watchdog` |
| Restart | `schtasks /Run /TN XAU_Terminal_Watchdog` |
| Uninstall | `powershell -ExecutionPolicy Bypass -File scripts\install_windows_service.ps1 -Uninstall` |

After `schtasks /End`, any already-running uvicorn stays up until it is
stopped (the watchdog is what restarts it on crash). To fully stop the server:

```powershell
powershell -Command "Get-Process | Where-Object { $_.ProcessName -match 'python' -and $_.Path -match 'trading_view' } | Stop-Process -Force"
```

To uninstall the service (remove the scheduled task), run the uninstall command
from the table above.

---

## Automatic startup after Windows reboot

The `BootTrigger` (30s delay) + `LogonTrigger` in the task XML handles this.
The watchdog then starts `start_local.ps1`, which brings uvicorn up and waits
for `/health` before reporting success.

---

## Automatic restart after crash

Two layers:

1. **Task Scheduler** `RestartOnFailure` -- restarts the watchdog if it dies.
2. **Watchdog** -- detects `/health` failure every 10s, calls `start_local.ps1`
   to restart uvicorn, waits for health, verifies pages, logs the event.

The watchdog uses a lock file (`logs\watchdog.lock`) so multiple watchdog
instances never run and never spawn duplicate uvicorn processes.

---

## Logs

All logs live in `D:\trading_view\logs\`:

| File | Contents |
|------|----------|
| `local_server.log` | uvicorn + application stdout (structured `app.core.logging`) |
| `local_server.err.log` | uvicorn stderr |
| `startup.log` | startup script actions (start, health-wait, page verification, errors) |
| `recovery.log` | watchdog recovery events (crash detected, restart, success/failure) |

No API keys, Telegram tokens, passwords, or secrets are ever logged. The
application logger writes only structured operational messages.

---

## Manual start (no service)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1
```

## Manual health check

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_local_server.ps1
```

## One-click start

Double-click `START_PICKO_XAU_TERMINAL.bat` -- it starts the server (or reuses
the running one), waits for health, then opens the dashboard.

---

## URLs

| Page | URL |
|------|-----|
| Main UI | `http://localhost:8000/dashboard` |
| Terminal | `http://localhost:8000/terminal` |
| API docs | `http://localhost:8000/docs` |
| Health | `http://localhost:8000/health` |

---

## Troubleshooting

**"Port 8000 is occupied by another process: PID XXXX"**

The start script refuses to kill unrelated applications. Check what owns the
port:

```powershell
netstat -ano | findstr :8000
tasklist /FI "PID eq XXXX"
```

If it is a **stale XAU uvicorn** (python.exe under `trading_view`), the script
stops it automatically. If it is an unrelated app, free the port manually and
retry.

**Dashboard loads but shows FEED: DISCONNECTED**

That is expected when Binance is offline. `localhost:8000` still serves
`/health`, `/dashboard`, `/terminal`, `/docs`, and the historical fallback
continues to provide real persisted candles. The server is healthy -- only the
market feed is degraded.

**Server healthy but browser can't reach localhost**

Try `http://127.0.0.1:8000` directly. If that works, the issue is
local-hostname/IPv6 resolution, not the application. Confirm with
`check_local_server.ps1` (it reports genuine health, not just an open TCP port).

---

## Safety note

Real-money execution and broker execution remain **permanently disabled** in
this codebase. Paper trading stays blocked while the strategy classification is
FAILED, observation mode never trades, and AI validation is advisory only. This
deployment work does not change any of those rules.