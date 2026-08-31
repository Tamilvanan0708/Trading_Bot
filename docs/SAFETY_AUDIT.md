# SAFETY AUDIT — XAU/USD AI Signal Intelligence Terminal

## Permanent Safety Rules
1. **Real-money execution: PERMANENTLY DISABLED.** No broker order API exists
   anywhere in the codebase. `REAL_MONEY_EXECUTION` defaults to `False` and is
   never checked by any execution path (there is none).
2. **No broker execution code.** Grep-verified: no order placement, buy/sell
   execution, broker client, execution webhook that places real orders.
   (`/market/live/tradingview-webhook` is a read-only alert webhook, no orders.)
3. **Manual execution only.** The system generates signals + notifications;
   the user decides whether to trade.
4. **Observation mode never trades.** `OBSERVATION_MODE=true` records signals
   and outcomes; paper trades are blocked.
5. **FAILED strategy cannot paper trade.** `BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY=true`
   + classification FAILED → paper trades blocked with explicit reason.
6. **AI is advisory.** AI APPROVE/REJECT/CAUTION never overrides deterministic
   gates (data quality, admission, limits, observation, FAILED block).
7. **Bad/stale data cannot generate signals.** Data-quality gate + degraded
   state block analysis/trading.
8. **No look-ahead bias.** Only closed candles enter analysis; no future
   candles, regime, or volatility information.
9. **No forced daily target.** The 30–50 pt/day preference is a research
   objective only; no thresholds are lowered to force signals.

## Safety Gates (all authoritative, tested)
| Gate | Behavior |
|---|---|
| Data-quality | degraded → analysis blocked, paper blocked |
| Feed-connected | offline → degraded |
| Historical freshness | stale → degraded |
| FAILED strategy | blocks paper trading |
| Observation mode | blocks all paper trades |
| Max drawdown (30%) | blocks new paper trades |
| Daily loss (3%) | blocks new paper trades |
| Consecutive loss (5) | blocks new paper trades |
| Duplicate signal | blocks re-entry same candle |
| Conflicting position | blocks opposite stacking |
| Position limit (MAX_OPEN) | caps concurrent positions |
| R:R minimum | rejects poor geometry |
| Real money | permanently disabled (no path) |

## Frontend safety
- Safety states (OBSERVATION ACTIVE, PAPER BLOCKED, REAL MONEY DISABLED,
  STRATEGY FAILED) are always visible — never hidden.
- No UI control can enable real-money trading.
- No secrets (Telegram token, API keys) are sent to the client
  (`/telegram/status` returns only booleans; `/notifications` has no secrets).

## Secret handling
- `.env` is gitignored; `.env.example` has placeholders only.
- CORS origins configurable, safe default (localhost), credentials disabled
  when `["*"]`.
- Production-safe exception responses (no stack traces).

## Verified by tests
`test_hardening.py` (blocked gates), `test_paper_trading_service.py`
(FAILED-block), `test_candidate_observation.py` (observation no-trade),
`test_ranking.py::test_real_money_never_enabled`,
`test_final_reports.py::test_no_broker_execution_imports`,
`test_terminal.py::test_telegram_status_endpoint` (no secrets).
