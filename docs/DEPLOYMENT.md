# DEPLOYMENT — XAU/USD AI Signal Intelligence Terminal

## Modes
- **Development / laptop:** `uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --workers 1`
- **Production / VPS (recommended):** Docker Compose or systemd, laptop OFF.

## Docker
`Dockerfile` — `python:3.12-slim`, installs requirements, healthcheck via curl.
`docker-compose.yml` — one application container, `env_file: .env`, `/data`
volume (persistent SQLite), `restart: unless-stopped`, healthcheck on `/health`,
`command: uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --workers 1`.

```bash
cp .env.example .env          # LIVE_FEED_PROVIDER=binance, OBSERVATION_MODE=true
docker compose up -d --build
curl http://localhost:8000/health   # {"status":"healthy",...}
```

## systemd (Linux VPS)
```ini
# /etc/systemd/system/xauusd.service
[Unit]
Description=XAU/USD Signal Intelligence Terminal
After=network.target
[Service]
WorkingDirectory=/opt/trading_view
ExecStart=/opt/trading_view/.venv/bin/python -m uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

## Windows / NSSM
`scripts/start_windows.ps1`; NSSM wraps the same uvicorn command.

## Scheduled maintenance (cron on VPS)
```cron
0 2 * * *  cd /opt/trading_view && .venv/bin/python scripts/backup_db.py          # WAL-safe backup (7-day retention)
0 7 * * *  cd /opt/trading_view && .venv/bin/python scripts/daily_forward_report.py --days 1
0 7 * * 1  cd /opt/trading_view && .venv/bin/python scripts/weekly_forward_report.py --weeks 1
```

## Reliability properties
- WebSocket auto-reconnect (exponential backoff 5→80s), REST refresh retries
  (4×), emergency backfill on reconnect, data-quality gates.
- Single scheduler worker (SQLite + singleton scheduler are not multi-worker
  safe). Never run `--workers > 1`.
- Persistent `/data` volume keeps DB/WAL/research across container restarts.
- Logs: structured `app.core.logging`; `scripts/runtime_validation.py` for
  post-deploy checks.

## Environment (.env, gitignored)
Key vars: `LIVE_FEED_PROVIDER`, `OBSERVATION_MODE`, `BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY`,
`DATABASE_URL`, `TELEGRAM_*`, `AI_*`, `MAX_TOTAL_DRAWDOWN_PCT`, `DASHBOARD_REFRESH_SECONDS`.
`REAL_MONEY_EXECUTION` is always `false` (no broker path exists).
