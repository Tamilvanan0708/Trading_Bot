# XAUUSD Multi-Timeframe Trading AI Agent

> [!WARNING]
> This is a **research and paper-trading tool**. The current strategy is classified **FAILED** — it does not have a statistically defensible edge in pooled out-of-sample testing. Real-money trading is **disabled by design**. Automatic paper trading is **blocked** by the strategy-safety gate while the classification remains FAILED.

Research on 8.5 months of real Binance XAUUSDT data (24,585 candles, 2,384
deterministic signals, 5 walk-forward windows) identifies:
- The **exit model** (TP2 at ~2.4R) destroys the signal's edge — a TP at
  1.25-1.5R produces positive pooled OOS expectancy.
- **86% of signals** cluster within 2h of a previous signal — signal selection
  (strongest-per-session, min-4h-gap) cuts trades 5-6x while preserving
  expectancy and dramatically reducing drawdown.
- The **RANGING regime** filter shows positive OOS in all 5 windows
  (n=226, WR=70.8%, PF=3.42, Exp=+0.68R).
- **Low-volatility** signals outperform high-volatility signals.

These findings are research observations, not production changes. The
candidates require forward observation validation (available via
`OBSERVATION_MODE` and the signal-outcome tracking database) before any
strategy upgrade consideration.

An institutional-grade, deterministic quantitative market analysis, backtesting, and paper trading system for **Gold (`XAU/USD`)**, augmented with an explainable AI validation layer.

> [!WARNING]
> **This is NOT a guaranteed profitable trading system.** It is an educational, analytical, backtesting, and paper-trading tool. Real-money trading is **disabled by design**. Trading involves substantial risk of loss; never risk money you cannot afford to lose.

---

## 🌟 Key Architecture & Philosophy

> [!IMPORTANT]
> **Zero LLM Hallucination**: The AI layer does **not** invent prices or compute raw indicators. All market structure, Fibonacci levels, Smart Money Concepts (SMC), and confluence calculations are **100% mathematical, deterministic, and backtestable**. The AI validation layer receives structured JSON payloads to perform qualitative conflict checks, risk reasoning, and natural language synthesis — it only outputs `APPROVE`, `REJECT`, or `CAUTION`.

```
+-----------------------------------------------------------------------------------+
|                              Presentation & Control                               |
|   FastAPI REST API  |  Interactive Web Dashboard  |  Telegram Notification Service|
+-----------------------------------------------------------------------------------+
                                          │
+────────────────────────────────────────▼──────────────────────────────────────────+
|                          Execution & Simulation Layer                             |
|       Backtesting Engine (Event-driven)    │   Paper Trading State Machine        |
+────────────────────────────────────────┬──────────────────────────────────────────+
                                          │
+────────────────────────────────────────▼──────────────────────────────────────────+
|                       AI Sanity & Narrative Layer (LLM)                          |
|         Confluence Validation  •  Conflict Detection  •  Risk Explanation        |
+────────────────────────────────────────┬──────────────────────────────────────────+
                                          │
+────────────────────────────────────────▼──────────────────────────────────────────+
|                           Deterministic Signal Engine                             |
|          Confluence Engine (Weighted 0-100 Scoring) & Dynamic Invalidation        |
+────────────────────┬──────────────────────────────────────┬───────────────────────+
                     │                                      │
+────────────────────▼──────────────────+ +─────────────────▼───────────────────────+
|     Strategy A: Fibonacci Retracement | |   Strategy B: Smart Money Concepts (SMC) |
| • Swing High/Low detection (Fractals) | | • Market Structure (HH, HL, LH, LL)     |
| • 0.236, 0.382, 0.5, 0.618, 0.786,    | | • Break of Structure (BOS) & CHoCH      |
|   1.0, 1.618 Extension                | | • Fair Value Gaps (FVG) & Order Blocks  |
| • Retracement & Golden Zone Confluence| | • Liquidity Sweeps & Eq Highs/Lows      |
| • Dynamic SL/TP & R:R Calculator      | | • Premium vs. Discount Equilibrium      |
+────────────────────┬──────────────────+ +─────────────────┬───────────────────────+
                     │                                      │
+────────────────────┴──────────────────┬───────────────────┴───────────────────────+
|                 Multi-Timeframe Market Structure & Indicators                     |
|      4H: Macro Bias  │  1H: Structural  │  30M: Setup  │  15M: Entry Trigger      |
+-----------------------------------------------------------------------------------+
```

---

## 📊 Multi-Timeframe Confluence Scoring Matrix

| Timeframe | Role | Evaluated Elements |
| :--- | :--- | :--- |
| **4H** | Macro Bias | 200/50 EMA slope, Macro Swing Structure, Supply/Demand equilibrium |
| **1H** | Structural Confirmation | 1H BOS / CHoCH, Institutional Order Blocks, Range Equilibrium |
| **30M** | Setup Development | Fibonacci Retracement (Golden Pocket: 50%–61.8%–78.6%), FVGs, Liquidity Sweeps |
| **15M** | Entry Trigger | Lower-timeframe CHoCH, Rejection wicks, Confirmation candle closes |

### Weighted Score (0 – 100)
- **Higher Timeframe Bias (4H)**: `+20 pts`
- **Market Structure (1H)**: `+20 pts`
- **SMC Confirmation (FVG / OB / Discount)**: `+20 pts`
- **Fibonacci Golden Zone (50%-61.8%-78.6%)**: `+15 pts`
- **Liquidity Sweep / Equal Highs/Lows**: `+10 pts`
- **Entry Trigger Candle Confirmation (15M)**: `+10 pts`
- **Risk / Reward Ratio ≥ 1:1.5**: `+5 pts` *(awarded only when the actual projected R:R from Entry/SL/TP meets the configured `MIN_RISK_REWARD`)*

**Signal Thresholds** (all configurable in `.env`):
- `90 - 100`: **VERY STRONG** (Tradable)
- `75 - 89`: **STRONG** (Tradable)
- `60 - 74`: **MODERATE** (WATCH — not auto-traded)
- `40 - 59`: **WEAK** (No trade)
- `0 - 39`: **NO TRADE**

The confluence engine is the **single source of truth** for tradability: a signal is tradable only when its final quality is `STRONG` or `VERY_STRONG`.

---

## 🚀 Installation

```bash
# 1. Clone or navigate to the project directory
cd d:/trading_view

# 2. Create & activate virtual environment (Windows)
python -m venv .venv
.\.venv\Scripts\activate

# 3. Install requirements
pip install -r requirements.txt
```

> **Note**: `MetaTrader5` is only installed on Windows (`platform_system == "Windows"` marker). On other platforms the MT5 bridge degrades gracefully.

---

## ⚙️ Environment Configuration

Copy `.env.example` to `.env` and adjust:

```ini
APP_ENV=development
DEFAULT_SYMBOL=XAUUSD
LOT_CONTRACT_SIZE=100.0
PIP_SIZE=0.1

# Risk
RISK_PERCENT=1.0
MAX_RISK_PERCENT=3.0
MAX_OPEN_TRADES=3
ACCOUNT_BALANCE=10000.0
MIN_RISK_REWARD=1.5

# ATR
ATR_PERIOD=14
ATR_FALLBACK=2.5

# Confluence weights & thresholds (see above)
WEIGHT_HTF_BIAS=20
...
THRESHOLD_VERY_STRONG=90
THRESHOLD_STRONG=75
THRESHOLD_MODERATE=60
THRESHOLD_WEAK=40

# AI Validation
AI_PROVIDER=mock            # mock | openai | gemini
AI_API_KEY=

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ENABLED=false

# Paper Trading
PAPER_TRADING_ENABLED=true
AUTO_TRADE_ON_CAUTION=false

# Live market feed
LIVE_FEED_PROVIDER=mock     # mock | binance | ctrader | tradingview
BINANCE_WS_URL=wss://fstream.binance.com/stream
CTRADER_WS_URL=wss://connect.spotware.com/apps/
CTRADER_ACCESS_TOKEN=
CTRADER_ACCOUNT_ID=0
CTRADER_SYMBOL_ID=0

# Backtesting
BACKTEST_SPREAD_POINTS=0.5
BACKTEST_SLIPPAGE_PCT=0.01
BACKTEST_TRANSACTION_COST_USD=0.0
BACKTEST_ENTRY_ON_NEXT_OPEN=false

# MT5 bridge
MT5_ENABLED=false
MT5_LOGIN=0
MT5_SERVER=
MT5_PASSWORD=
MT5_MAGIC=0
MT5_SYMBOL=XAUUSD
MT5_TZ_OFFSET_MINUTES=0

# Database
DATABASE_URL=sqlite+aiosqlite:///./data/xauusd_agent.db
```

All secrets (`AI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `MT5_PASSWORD`, `CTRADER_ACCESS_TOKEN`) are loaded **only** from environment variables / `.env`. They are never hardcoded.

---

## 🌐 Running the API

```bash
uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --reload
```

> **IMPORTANT**: The app uses SQLite and a single background scheduler. It MUST run with `--workers 1`. Do NOT use multiple uvicorn workers.

- **Developer Dashboard**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)
- **Interactive OpenAPI Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

### Key API Endpoints

| Endpoint | Description |
| :--- | :--- |
| `GET /analysis/{symbol}` | Read-only multi-timeframe analysis |
| `GET /analysis/live/{symbol}` | Analysis on live feed data (closed candles) |
| `POST /analysis/run` | Full analysis cycle (persists, AI-validates, alerts) |
| `POST /backtest` | Run event-driven backtest |
| `GET /paper-trades` | Paper trade history (persisted) |
| `GET /performance/account` | Account statement (balance, equity, P&L, win rate) |
| `GET /market/{symbol}` | Historical OHLCV |
| `GET /market/live/health` | Live feed health status |
| `GET /market/{symbol}/live` | Live multi-timeframe snapshot |
| `POST /webhook/tradingview` | TradingView alert webhook receiver |

---

## 📂 Loading Historical Data

The sample data is synthetic and used for unit tests. To import **real** XAU/USD OHLCV data:

```bash
# From a broker-exported CSV
python scripts/import_historical_data.py --csv data/raw/mt5_xauusd_m15.csv --validate --output-dir data/processed

# From MetaTrader 5 (requires MT5 terminal running + MT5_ENABLED=true)
python scripts/import_historical_data.py --mt5 --symbol XAUUSD --timeframe 15m --bars 5000 --validate
```

The importer validates:
- timestamp ordering, duplicates, gaps (strict mode via `--strict-gaps`)
- invalid OHLC values and timezone handling
- then resamples the base timeframe into 15M / 30M / 1H / 4H datasets written to `data/processed/`.

---

## 📊 Running Backtests

```bash
python scripts/run_backtest_cli.py
```

The backtest engine guarantees:
- **Zero look-ahead bias** (only data up to the current bar is used)
- Chronological processing and correct multi-timeframe alignment
- Same-candle **SL-priority** handling (conservative)
- Configurable **spread**, **slippage**, and **transaction costs**
- Correct equity curve, drawdown, and P&L accounting
- Near-linear-time incremental MTF resampling (identical results to the
  pandas reference — verified by determinism tests)

---

## 🔬 Strategy Research

The production strategy is **never modified by research**.  Research scripts
collect deterministic signals once, then replay exit/filter variants over the
recorded signals:

```bash
# Scientific diagnosis: entry quality, target reachability, session/regime,
# feature attribution, signal-selection filters, walk-forward, robustness
python scripts/run_scientific_diagnosis.py --data data/research/xauusd_15m_full.json

# Candidate OOS validation on strict chronological walk-forward TEST windows
python scripts/run_candidate_oos.py

# Full authoritative validation report (drives the strategy grade + safety gate)
python scripts/run_strategy_research.py --data data/research/xauusd_15m_full.json \
    --train-months 2 --val-months 1 --test-months 1
```

Reports are written to `data/research/` (`latest_report.json`,
`diagnosis_report.json`, `candidate_oos_windows.json`).

### Strategy classification

Classification uses **pooled OOS metrics** across all walk-forward TEST windows
(not the average of tiny per-window estimates) plus the positive-window ratio
as a stability check.  The strategy is **never upgraded** from FAILED without
positive pooled OOS expectancy.  Grades: `ROBUST`, `PROMISING`, `WEAK`,
`FAILED`, `INCONCLUSIVE`.

> 📄 **Strategy promotion policy:** `docs/strategy_promotion_policy.md` defines
> the only acceptable path for a candidate to move from research to production.
> Backtests are never sufficient — forward observation evidence is required.

---

## 📈 Signal Outcome Tracking (Forward Validation)

Every LONG/SHORT signal is automatically registered for forward-outcome
tracking.  As new candles close, the system records — for every signal —
max favorable / adverse excursion in R, which TP levels were reached, whether
the SL was hit, time to outcome, final R, market regime, session, and the
strategy version that produced it.

This is independent of paper trading: it answers *"if this signal had been
taken, what would the outcome have been?"*  Data is stored in the `signals`
table and survives restarts.  Query it via:

- `GET /research/signal-outcomes` — forward-outcome statistics
- `GET /signals` — per-signal outcome fields

### Strategy versioning

Every signal is stamped with a deterministic `strategy_version` derived from
the strategy-critical settings (confluence weights, thresholds, R:R minimum,
ATR, costs).  Any config change produces a different version so every signal,
paper trade and research outcome can be traced to the exact strategy
configuration that produced it.

---

## 💰 Paper Trading

Paper trading is **on by default** (`PAPER_TRADING_ENABLED=true`).  A paper
position opens **only** when ALL of these pass:

1. The deterministic confluence threshold (`STRONG` / `VERY_STRONG`),
2. Risk management validation,
3. AI validation (`APPROVE`, or `CAUTION` when `AUTO_TRADE_ON_CAUTION=true`),
4. The authoritative **admission gate** (data quality, regime filter, limits,
   duplicates, conflicts),
5. **Observation mode is OFF** and the strategy is **not classified FAILED**.

The background **scheduler is the single authority** that opens paper trades
(after the admission gate).  The analysis pipeline itself never opens trades.
All state transitions are persisted to the database and survive server
restarts.

> **Paper trading never places real orders.** There is no broker order-execution path in the codebase.

---

## 📡 Enabling Live Market Monitoring

1. Set `LIVE_FEED_PROVIDER` in `.env`:
   - `binance` — free public WebSocket for `XAUUSDT` futures (no key required)
   - `ctrader` — cTrader Open API (requires `CTRADER_ACCESS_TOKEN`, `CTRADER_ACCOUNT_ID`, `CTRADER_SYMBOL_ID`)
   - `tradingview` — passive webhook receiver (`POST /webhook/tradingview`)
   - `mock` — disabled (default)
2. Restart the API. The lifespan hook connects the feed, registers it with the feed registry, and begins aggregating ticks into closed 15M candles.
3. Live analysis uses **only closed candles** by default (`include_forming=false`), guaranteeing no partially-formed candle bias.

---

## 🤖 AI Configuration

| Provider | `AI_PROVIDER` | `AI_API_KEY` |
| :--- | :--- | :--- |
| Mock (deterministic heuristic fallback) | `mock` | not required |
| OpenAI | `openai` | OpenAI API key |
| Gemini | `gemini` | Google AI Studio key |

The AI layer:
- **Can**: validate, reject, explain, identify conflicts & missing confirmations, assign qualitative confidence.
- **Cannot**: invent market data, invent signals, override hard risk rules, bypass minimum R:R, or bypass signal thresholds. Deterministic guardrails are enforced after every LLM response.

If the AI provider is unavailable, the deterministic heuristic validator takes over safely.

---

## 📡 Telegram Configuration

1. Create a bot with [@BotFather](https://t.me/botfather) and obtain the token.
2. Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `TELEGRAM_ENABLED=true`.
3. The service:
   - sends **structured** signal alerts,
   - **prevents duplicate** alerts for the same signal,
   - logs send/failure status to the `notification_logs` table.

---

## 🧪 Testing

```bash
pytest -v
```

Test coverage includes:
- unit tests (indicators, SMC, Fibonacci, confluence, signals, risk, AI, live feeds, ingestion, backtesting, paper trading)
- integration tests (FastAPI endpoints, pipeline, persistence)
- API tests (route contracts, webhooks, feed health)
- database tests (CRUD, paper trade persistence & restore)
- look-ahead bias detection tests for the backtester

---

## 🔧 Troubleshooting

| Problem | Solution |
| :--- | :--- |
| `No module named MetaTrader5` | MT5 is Windows-only. Run on Windows or use a CSV/other feed. |
| MT5 connect fails | Ensure the MT5 terminal is installed, running, and logged in. Set `MT5_ENABLED=true`. |
| Live feed stays `mock` | Set `LIVE_FEED_PROVIDER=binance` (or ctrader/tradingview) in `.env`. |
| Telegram alerts not sent | Check `TELEGRAM_ENABLED=true` and the bot/chat ID; inspect `notification_logs`. |
| AI API errors | The heuristic validator takes over; check `AI_API_KEY` and `AI_PROVIDER`. |
| Backtest needs more candles | `warmup_bars` defaults to 150; provide a longer dataset. |

---

## 📚 Project Layout

```
app/
├── ai/                # AI validation layer (LLM + heuristic guardrails)
├── api/               # FastAPI application & routes
├── backtesting/       # Event-driven backtest engine
├── confluence/        # Multi-timeframe confluence scoring
├── config/            # Pydantic settings + strategy versioning
├── core/              # Enums, exceptions, logging, market hours
├── data/              # Providers, ingestion, validation, live feeds, resampling
├── database/          # SQLAlchemy async models & repository
├── fibonacci/         # Fibonacci engine
├── indicators/        # ATR, EMA, swings
├── market_regime/     # Regime detection (trend/range/vol)
├── market_structure/  # HH/HL/LH/LL trend detection
├── news/              # News blackout filter
├── notifications/     # Telegram service & formatters
├── paper_trading/     # Paper position lifecycle, state machine, limits
├── research/          # Walk-forward, Monte Carlo, bootstrap, diagnosis,
│                      # replay engine, outcome tracker, observation store
├── risk/              # Position sizing, admission gate
├── services/          # Pipeline orchestrator, scheduler, status
├── signals/           # Central signal engine
├── smc/               # SMC (BOS/CHoCH/FVG/OB/Liquidity)
└── strategies/        # Fib & SMC strategy candidates
data/                  # Databases and raw/processed candle data
scripts/               # CLI runners (analysis, backtest, import)
tests/                 # Unit, integration, and API tests
```

---

## 🚀 Deployment

### Docker (recommended for 24/7 Linux VPS paper trading)

```bash
cp .env.example .env       # set LIVE_FEED_PROVIDER, etc.
docker compose up -d --build
curl http://localhost:8000/health   # -> {"status":"healthy"}
```

- `docker-compose.yml` mounts `./data` (SQLite DB + backups) and restarts `unless-stopped`.
- A healthcheck polls `/health` every 60s.
- **Single worker only** (SQLite + scheduler are not multi-worker safe).

### Windows (local machine / MT5 host)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1
```

For a real Windows service use [NSSM](https://nssm.cc/):
```cmd
nssm install XauusdAgent "C:\trading_view\.venv\Scripts\python.exe" "-m uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --workers 1"
nssm set XauusdAgent AppDirectory C:\trading_view
nssm set XauusdAgent AppStdout C:\trading_view\logs\app.log
nssm set XauusdAgent AppStderr C:\trading_view\logs\err.log
nssm start XauusdAgent
```

### Linux (systemd)

```ini
# /etc/systemd/system/xauusd.service
[Unit]
Description=XAU/USD Trading Agent
After=network.target
[Service]
WorkingDirectory=/opt/trading_view
ExecStart=/opt/trading_view/scripts/start_linux.sh
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

### Laptop-off operation (VPS/cloud)

The signal system does NOT depend on your laptop being powered on. Deploy on a
VPS/cloud server:

```
Internet → VPS → Python/FastAPI Agent → Binance WebSocket + REST
           → Signal Engine → Telegram → Your Mobile
```

The laptop is used only for development / dashboard / configuration / research.
Production steps:

```bash
# 1. Create a VPS (e.g. 1 vCPU / 1-2 GB RAM), install Docker.
# 2. Clone the repo and configure:
cp .env.example .env          # LIVE_FEED_PROVIDER=binance, OBSERVATION_MODE=true
# 3. Build + start:
docker compose up -d --build
# 4. Verify:
curl http://<vps-ip>:8000/health
# 5. (Optional) systemd fallback:
cp scripts/start_linux.sh /opt/trading_view/scripts/
```

Automated maintenance (cron on the VPS):

```cron
0 2 * * *   cd /opt/trading_view && .venv/bin/python scripts/backup_db.py
0 7 * * *   cd /opt/trading_view && .venv/bin/python scripts/daily_research_summary.py --days 1
```

The scheduler runs as a SINGLE worker (SQLite + singleton scheduler are not
multi-worker safe). Telegram alerts + the dashboard work remotely.

### VPS + nginx reverse proxy + HTTPS

```nginx
server {
    listen 80;
    server_name example.com;
    location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
}
# Then: certbot --nginx -d example.com  (Let's Encrypt)
```

### Monitoring

- UptimeRobot / Healthchecks.io → `https://example.com/health`
- `GET /market/system-status` — scheduler + last-tick + last-analysis state
- `GET /market/data-quality` — live data-quality + history-refresh state
- `GET /research/classification` — current strategy grade
- `GET /research/signal-outcomes` — forward-outcome statistics (Phase 8)

### Database backups

```bash
# daily (cron: 0 2 * * * cd /opt/trading_view && .venv/bin/python scripts/backup_db.py)
python scripts/backup_db.py   # WAL-safe copy, keeps last 7 backups in data/backups/

# restore (stops required; makes a pre-restore snapshot automatically)
python scripts/restore_db.py --backup data/backups/xauusd_YYYYMMDD_HHMMSS.db --yes
```

### Daily forward-observation summary

```bash
# cron: 0 7 * * *  (daily)
python scripts/daily_research_summary.py --days 1
# weekly:
python scripts/daily_research_summary.py --days 7
```
Writes `data/research/forward_summary_YYYYMMDD.md`.

### Weekly candidate forward report (A/B/C side-by-side)

```bash
python scripts/weekly_forward_report.py --weeks 1
```
Writes `data/research/weekly_forward_YYYYMMDD.md` with per-candidate OOS vs
forward comparison, session/regime breakdown, positive/negative/flat days,
**promotion status** and a **balanced risk-adjusted ranking**.

### Formal research publications

```bash
python scripts/generate_mtf_comparison.py   # reports/mtf_comparison.{json,md}
python scripts/daily_forward_report.py      # reports/daily/forward_YYYYMMDD.md
```

### Candidate ranking & promotion

Candidates are ranked by a **balanced risk-adjusted score** (25% OOS
expectancy, 20% PF, 15% drawdown, 10% window consistency, 10% robustness,
10% bootstrap CI, 5% sample size, 5% Monte Carlo) — **never primarily by win
rate**.

Promotion is a strict, human-gated state machine:
`RESEARCH → OOS_VALIDATED → FORWARD_OBSERVATION → FORWARD_VALIDATED →
PAPER_VALIDATION → HUMAN_REVIEW → PROMOTED`. There is **no automatic
promotion** and no automatic broker activation.

### Forward-observation candidates

Three frozen research candidates are evaluated INDEPENDENTLY on every closed
candle while `OBSERVATION_MODE=true`:

| Candidate | Config | Regime | Conf | TP | SL |
|---|---|---|---|---|---|
| CANDIDATE_A_V1 | 4H→1H→30M→15M | RANGING | ≥75 | 1.75R | 1.5 ATR |
| CANDIDATE_B_V1 | 4H→1H→30M→5M  | RANGING | ≥85 | 1.75R | 1.5 ATR |
| CANDIDATE_C_V1 | 4H→1H→15M→5M  | RANGING | ≥85 | 2.0R  | 1.5 ATR |

They never affect the production strategy. Every signal is stored with its own
`strategy_version`, deduplicated (`strategy_version + candle + direction`), and
forward-tracked (MFE/MAE/TP/SL/final R). Compare them via:

- `GET /research/candidates` — side-by-side forward comparison
- `GET /research/signal-outcomes` — outcome statistics

Parameters are FROZEN while forward observation runs. Promotion requires
evidence per `docs/strategy_promotion_policy.md` (100+ forward signals,
4-8 weeks, stable expectancy).

### Telegram system alerts

- `TELEGRAM_ENABLED=true` sends signal alerts.
- **Data-quality degradation alert** (once/hour, deduplicated) when the pipeline degrades.
- **Data-quality recovery alert** when a degraded episode ends.
- **Paper-trading-blocked alert** (once/day) when the FAILED-strategy gate blocks trades.
- **Strategy-status alert** when the classification grade changes.
- **Signal-rejection alert** (once/30 min, throttled) with the admission reasons.
- All notification attempts are persisted with status `SENT` / `FAILED` / `SKIPPED` in the `notifications` table.

### Paper-trading safety limits

- `MAX_TOTAL_DRAWDOWN_PCT` (default 30) halts automatic paper trading if the account drawdown from the starting balance reaches the threshold.
- `BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY` (default true) keeps auto-paper-trading OFF while the research classification is FAILED.
- `OBSERVATION_MODE=true` records hypothetical signals with zero trades.

---

## ⚠️ Risk Disclaimer

**This system is provided for educational and research purposes only.** The current strategy is classified **FAILED** by out-of-sample validation (negative expectancy, 100% Monte Carlo negative-return probability). It is **not** financial advice and does **not** guarantee profitability. Real-money trading is deliberately disabled. Gold trading carries substantial risk, including the potential loss of your entire capital. Always backtest thoroughly, paper-trade extensively, and consult a licensed financial advisor before considering any live deployment.
