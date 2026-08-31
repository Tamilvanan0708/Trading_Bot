# TEST REPORT — XAU/USD AI Signal Intelligence Terminal

## Result
`pytest -v` → **302 passed / 0 failed** (1 deprecation warning).

## Coverage by area
| Area | Files | Notable tests |
|---|---|---|
| Data / live | `test_data_quality.py`, `test_history_refresh.py`, `test_ingestion.py`, `test_live_providers.py`, `test_dashboard_live.py` | gaps/dup/ooo, refresh, reconnect, live-price integrity (no synthetic fallback) |
| Resampling / MTF | `test_resampler_equivalence.py`, `test_mtf_research.py` | incremental==pandas, M5, MTF config roles, collector determinism |
| Signal / confluence | `test_signal_engine.py`, `test_confluence_engine.py`, `test_strategy_*.py` | tradability, conflicts, R:R, strategy candidates |
| Structure / SMC / Fib | `test_market_structure.py`, `test_smc_engine.py`, `test_fibonacci_engine.py` | swings, BOS/CHoCH, FVG/OB/sweeps, golden pocket |
| AI | `test_ai_validator.py` | advisory, never overrides gates |
| Scheduler | `test_scheduler.py` | restart-skip, weekend skip, no duplicate candle |
| Paper / risk | `test_paper_trading_service.py`, `test_limits.py`, `test_state_machine.py`, `test_hardening.py` | FAILED-block, limits, restart-safe state machine |
| Research | `test_research_metrics.py`, `test_research_walkforward_integration.py`, `test_replay_engine.py`, `test_ranking.py` | classification, pooled OOS, determinism, ranking, promotion states |
| Candidates / observation | `test_candidate_observation.py`, `test_strategy_version_and_outcomes.py` | side-by-side A/B/C, dedup, versioning, outcome tracking, timezone |
| Telegram | `test_telegram_service.py`, `test_hardening.py` | format, dedup, persistence |
| API / frontend | `test_pipeline_and_api.py`, `test_terminal.py`, `test_dashboard_live.py` | endpoint contracts, terminal/dashboard parity, SPA assets, notifications, telegram status (no secrets) |
| Final reports / safety | `test_final_reports.py` | daily/weekly/mtf reports run, real-money never enabled, no broker imports |

## Mandatory safety tests (all pass)
1. FAILED strategy cannot paper trade ✅
2. Observation mode cannot trade ✅
3. Real-money execution impossible ✅
4. AI cannot override safety ✅
5. Duplicate candle cannot duplicate signal ✅
6. Restart cannot duplicate signal ✅
7. Bad data cannot generate signal ✅
8. Reconnect cannot duplicate candles ✅
9. DB recovery preserves state ✅
10. Future candle cannot enter analysis ✅
11. Terminal & dashboard identical core state ✅
12. Telegram contains no secret ✅
13. No broker execution endpoint exists ✅

## Notable regressions caught & fixed
- `/notifications` 500 (model name/columns) — fixed
- Terminal static asset shadowed by SPA fallback — fixed
- E2E `/dashboard` title assertion updated for premium terminal
