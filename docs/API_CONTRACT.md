# API CONTRACT — XAU/USD AI Signal Intelligence Terminal

All endpoints return JSON. Timestamps are ISO-8601 UTC. Numbers use the
project's standard precision (prices 2dp, R-multiples 3dp, percents 2dp).

## System
| Endpoint | Method | Returns |
|---|---|---|
| `/health` | GET | `{status, symbol, environment, version}` |
| `/market/system-status` | GET | full runtime: scheduler, last tick/candle/analysis, observation_mode, paper_trading_enabled, real_money_execution, strategy_grade |
| `/market/live/health` | GET | per-feed `{provider, connected, ticks_cached, buffer_capacity, latest_tick}` |
| `/market/data-quality` | GET | `{degraded, degradation_reason, candle_count, gap/dup/ooo counts, newest_candle, historical_fresh, last_history_refresh_*}` |

## Market
| Endpoint | Method | Returns |
|---|---|---|
| `/market/{symbol}/live` | GET | `MultiTimeframeSnapshot`: current_price, timestamp, m5/m15/m30/h1/h4 candle series |
| `/market/{symbol}` | GET | historical analysis snapshot |

Symbol aliasing: the system normalizes `XAUUSD`/`XAUUSDT` consistently
(provider maps to the Binance futures symbol XAUUSDT).

## Analysis
| Endpoint | Method | Returns |
|---|---|---|
| `/analysis/live/{symbol}` | GET | `{symbol, timestamp, current_price, degraded, degradation_reason, data_quality, market_bias, fibonacci_setup, smc_analysis, confluence, signal, ai_validation, explanation, live}` |
| `/analysis/{symbol}` | GET | historical analysis (CSV provider, sample data) |
| `/analysis/run` | POST | trigger a manual analysis run |

`signal` object: `{direction, strategy, entry, stop_loss, take_profit_1..3,
risk_reward, confidence_score, signal_quality, reasons}`. When no valid setup:
`direction = NO_TRADE` (entry/SL/TP are not fabricated).

## Signals
| Endpoint | Method | Returns |
|---|---|---|
| `/signals` | GET | list (limit/pagination): id, created_at, symbol, direction, strategy, strategy_version, entry/stop_loss/TPs, risk_reward, confidence_score, signal_quality, market_bias, regime, session, outcome, final_r, MFE/MAE, reasons, ai_validation |
| `/signals/{id}` | GET | single signal with full outcome + AI detail |

## Research
| Endpoint | Method | Returns |
|---|---|---|
| `/research/classification` | GET | grade (ROBUST/PROMISING/WEAK/FAILED/INCONCLUSIVE) + reasons + pooled OOS + Monte Carlo + bootstrap |
| `/research/signal-outcomes` | GET | forward-outcome stats, hit rates, per-candidate admission/version, observation duration |
| `/research/candidates` | GET | side-by-side forward comparison of CANDIDATE_A/B/C |
| `/research/mtf-report` | GET | MTF/candidate research (ranked, cost-aware) |
| `/research/observation` | GET | observation store snapshot |
| `/research/summary` | GET | research summary |
| `/research/redesign` | GET | redesign report (if present) |

## Paper / Performance
| Endpoint | Method | Returns |
|---|---|---|
| `/paper-trades` | GET | trade list (opened_at, direction, entry, exit, pnl_usd, pnl_r, status) |
| `/paper-trades/{id}` | GET | single trade |
| `/performance/account` | GET | balance, equity, realized/unrealized PnL |
| `/performance` | GET | performance summary |

## Structure / SMC / Fibonacci
| Endpoint | Method | Returns |
|---|---|---|
| `/structure/{symbol}` | GET | live MarketStructureDetector output (HH/HL/LH/LL, BOS/CHoCH, trend) |
| `/smc/{symbol}` | GET | live SMCEngine output (FVG, order blocks, sweeps, zones) |
| `/fibonacci/{symbol}` | GET | live FibonacciEngine output (retracements, golden pocket) |

## Terminal / Notifications
| Endpoint | Method | Returns |
|---|---|---|
| `/terminal` | GET | premium SPA (HTML) |
| `/terminal/{path}` | GET | static assets + SPA fallback |
| `/dashboard` | GET | **same premium SPA** (shared source) |
| `/dashboard-legacy` | GET | preserved legacy dashboard HTML |
| `/notifications` | GET | persisted notification history (read-only) |
| `/telegram/status` | GET | `{enabled, configured, candidate_alerts, status}` — no secrets |

## Error / Degraded Conventions
- Data unavailable → explicit `NO_DATA`/`UNAVAILABLE`/`DEGRADED` states, never a
  silent empty array that implies health.
- Feed offline → `degraded=true` with `degradation_reason`.
- `NO_TRADE` signal → entry/SL/TP absent (never invented).
- Secrets are never returned (Telegram token, chat id, API keys).
