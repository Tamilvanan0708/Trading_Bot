# XAU/USD Signal Intelligence System — Final Audit

## 1. What Already Works (Production-Grade)

### Data Pipeline
- Binance WebSocket live feed (fstream.binance.com, bookTicker + aggTrade)
- WebSocket auto-reconnect with exponential backoff
- Binance REST historical data (paginated, 1000-candle limit, HTTP retry)
- Automatic history refresh/backfill on cooldown
- Data-quality validation (gaps, duplicates, out-of-order, invalid OHLC, stale data)
- Emergency refresh on degraded state
- Closed-candle-only processing (no forming candle enters analysis)
- Live price from tick stream (not candle close)

### Multi-Timeframe
- M5/M15/M30/H1/H4 resampling (IncrementalResampler, near-linear O(n), verified == pandas reference)
- `IncrementalResampler` shared across research collector and backtest engine
- MTF configurable (4 configurations: 4H→1H→30M→15M, 4H→1H→15M→5M, 4H→1H→30M→5M, 1H→30M→15M→5M)
- `MultiTimeframeSnapshot` with get_series(tf) helper

### Analysis Engine
- Market Structure (HH/HL/LH/LL, swing detection)
- BOS (Break of Structure) / CHoCH (Change of Character)
- SMC (FVG, Order Blocks, Liquidity Sweeps, Premium/Discount zones)
- Fibonacci (retracement levels, Golden Pocket detection)
- ATR calculation
- Market regime detection (Trending, Ranging, High/Low Volatility, Uncertain)
- Confluence scoring (0-100, weighted: HTF bias, structure, SMC, Fib, liquidity, entry trigger, R:R)
- Confluence engine MTF-configurable (roles: htf, structure, setup, trigger)

### Signal Engine
- SignalEngine with full production path (confluence + strategy candidates)
- Fast research path (confluence-only, ATR geometry, no strategy candidates)
- Tradability gate (STRONG / VERY_STRONG thresholds)
- Strategy versioning (deterministic hash from config, or CANDIDATE_X_V1)
- R:R geometry with spread/slippage validation
- ATR-based fallback SL/TP

### AI Validation
- Heuristic validator (mock mode)
- OpenAI/Gemini support (optional)
- APPROVE/REJECT/CAUTION statuses
- AI never overrides hard safety gates

### Scheduler
- Singleton loop (single-worker enforced)
- Candle-boundary detection (closed-candle only)
- Restart safety (skip first candle after restart, dedup via _last_processed_ts)
- Market-hours awareness (weekend skip)
- Observation mode: signals only, no paper trades
- Signal outcome tracking (MAE/MFE/TP/SL per signal)
- Candidate A/B/C evaluation on every closed candle
- Max daily signal cap
- Telegram alerts (production + candidate, throttled)

### Admission Gate
- Data-quality check
- AI status
- Regime filter
- Duplicate trade detection
- Conflicting position detection
- Daily loss limit
- Consecutive loss limit
- Max drawdown circuit breaker
- MAX_OPEN_POSITIONS
- R:R minimum
- Signal quality check

### Paper Trading
- State machine (ENTRY_HIT → TP1/TP2/TP3/SL_HIT → CLOSED)
- Persisted to DB (restart-safe)
- Spread/slippage/fees handling
- Inverted geometry protection
- SL/TP priority (same-candle SL priority, conservative)

### Observation Mode
- OBSERVATION_MODE=true: signals stored, outcomes tracked, NO paper trades
- BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY=true: blocks while FAILED
- Signal outcome tracking via DB columns (MAE/MFE/final R/TP hits/SL hits/timing)
- Outcome tracker restores open signals from DB (restart-safe)

### Telegram
- Signal alert with full metadata (strategy, direction, entry, SL, TP, R:R, confluence, 4H bias, 1H structure, regime, session, quality, AI)
- Candidate forward-observation alerts (with CANDIDATE_X_V1, FORWARD OBSERVATION, MANUAL ONLY)
- System alerts: degradation, recovery, paper-blocked, strategy-status, signal-rejection
- Notification persistence (SENT/FAILED/SKIPPED per notification)
- Dedup: signal_id for production, cooldown keys for typed alerts, cooldown DB persistence

### Research & Backtesting
- BacktestEngine (event-driven, chronological, SL-priority, spread/slippage/costs)
- Walk-forward (multiple train/val/test windows, chronological)
- Monte Carlo (10,000+ simulations, P(negative return), DD distribution)
- Bootstrap (95% CI for mean/median R, win rate)
- Strategy classification (pooled OOS metrics, conservative: FAILED/PROMISING/ROBUST/WEAK/INCONCLUSIVE)
- R-multiple distribution
- Trade forensics (MAE/MFE, target reachability, SL reachability)
- Regime/session/confluence bucket analysis
- Candidate OOS on chronological test windows
- MTF + candidate research (4 configs, regime/confluence/exit/signal-selection sweeps)
- Signal selection research (strongest-per-day/4H/session, min-gap, confidence≥85, etc.)
- Daily opportunity analysis
- Cached signal collection (reuses IncrementalResampler, fast research path)

### Deployment
- Dockerfile (python:3.12-slim, curl healthcheck, single worker)
- docker-compose.yml (env_file, /data volume, restart unless-stopped, healthcheck)
- CI (.github/workflows/ci.yml: pytest strict, ruff advisory, import smoke)
- Windows start script (start_windows.ps1)
- Linux start script (start_linux.sh)
- NSSM Windows service documentation
- systemd service documentation
- Database backup (WAL-safe SQLite backup API, prune old)
- Database restore (pre-restore snapshot, safety confirmation)
- Daily forward summary
- Weekly forward report (candidate A/B/C side-by-side, OOS reference, degradation)

### Dashboard
- Live price, feed status, data quality
- Current signal (entry, SL, TP1/TP2/TP3, R:R, confidence, direction, quality)
- AI status, admission status, strategy grade
- Paper trading status, observation mode badge
- Signal outcomes (forward validation stats)
- MTF research candidates table (config, regime, conf, TP, trades, WR, Exp, OOS+)
- Account statement, active trades, paper trades, trade history
- Research summary, REST refresh status
- Auto-refresh (configurable via DASHBOARD_REFRESH_SECONDS)
- Dark theme, Bootstrap 5, vanilla JS

### Tests
- 274 tests passing
- Unit: signal engine, confluence, SMC, Fibonacci, market structure, resampling, ATR, admission, limits, state machine, paper trading, Telegram, format, scheduler, observation, forensics, data quality, history refresh, ingestion, live providers, backtesting, hardening, variants, classification, resolved, resampler equivalence, MTF, candidate observation, strategy version, outcome tracker
- Integration: E2E paper trade, pipeline, API, data quality scenarios
- Look-ahead protection tests
- Determinism tests (collector, backtest, resampler, replay)
- Timezone correctness tests

## 2. What Is Incomplete / Needs Work

### Research Ranking System (Phase N)
- No centralized `rank_candidates` function
- Weekly report lists candidates but doesn't apply a weighted score
- Ranking needed for the promotion state machine and weekly recommendation

### Promotion State Machine (Phase O)
- Not implemented. Current system has only PRODUCTION (FAILED) and FORWARD_OBSERVATION.
- Missing: RESEARCH → OOS_VALIDATED → FORWARD_OBSERVATION → FORWARD_VALIDATED → PAPER_VALIDATION → HUMAN_REVIEW → PROMOTED
- Need a status field in the candidate framework

### scripts/daily_forward_report.py (Phase L)
- `daily_research_summary.py` exists (generates data/research/forward_summary_*.md)
- But Phase L asks for `scripts/daily_forward_report.py` that generates `reports/daily/` directory
- Need to create the enhanced version

### reports/mtf_comparison.json + .md (Phase F)
- MTF research results exist in `data/research/mtf_report.json`
- Need to generate `reports/mtf_comparison.json` and `reports/mtf_comparison.md`
- These are the "formal" research publication docs

### 24/7 Restart Recovery Test (Phase V)
- Restart recovery is designed (Docker restart, db persistence, scheduler skip)
- But no automated test simulates a restart and verifies no duplicate signals/DB corruption

### Edge Cases
- WebSocket disconnect during analysis (race condition)
- REST timeout during analysis
- Very large backtest memory
- Multiple signals on same candle from different candidates (handled, but untested at scale)

## 3. Bugs Found (Previously Fixed)

All bugs from prior sessions are fixed:
1. Pipeline no longer opens paper trades unconditionally (scheduler is sole authority)
2. Timezone-aware vs naive datetime comparison in outcome tracker
3. Classification used per-window averages (now pooled OOS)
4. CORS wildcard + credentials (now configurable, safe default)
5. Foreign keys not enforced (PRAGMA foreign_keys=ON added)
6. Dict mutation during iteration in outcome tracker (fixed)
7. `_has_conflicting_position` deleted by bad edit (restored)

## 4. Duplicate Functionality Found

- `daily_research_summary.py` and the requested `daily_forward_report.py` overlap significantly
- `weekly_forward_report.py` already exists — the Phase L/M descriptions overlap
- The `run_mtf_research.py` already generates mtf_report.json — reports/mtf_comparison.json would be a derived copy

## 5. Risky Functionality

- `BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY=true` — if this setting is flipped to false, paper trading resumes on a FAILED strategy. The setting is documented.
- `REAL_MONEY_EXECUTION` setting exists as a safety declaration but is never checked in code (there's no broker execution path). It's a "paper shield" — if someone adds broker code, they'd need to also check this flag.
- `CANDIDATE_TELEGRAM_ALERTS_ENABLED` — could cause notification spam if multiple candidates fire frequently. Default is false.

## 6. Missing Tests

- **Ranking system**: no tests for rank_candidates
- **Promotion state machine**: no tests for state transitions
- **Daily forward report**: no tests for report generation
- **Restart recovery**: no automated restart simulation test
- **WebSocket disconnect during analysis**: no race-condition test
- **Multiple candidate signals same candle**: dedup at scale

## 7. Remaining Tasks (Priority-Ordered)

### P0 — Remaining Implementation
1. Add `rank_candidates` function to research module
2. Add promotion state machine to candidates
3. Create `scripts/daily_forward_report.py` (reports/daily/)
4. Generate `reports/mtf_comparison.json` + `reports/mtf_comparison.md`

### P1 — Tests
5. Tests for ranking
6. Tests for promotion state machine
7. Tests for daily report

### P2 — Validation
8. Run full test suite (274+ → target)
9. Run runtime validation
10. Simulate restart recovery
11. Final verification

### P3 — Documentation
12. Update README with ranking + promotion state machine
13. Update .env.example
14. Final acceptance report

## 8. Research Limitations

- Only 8.5 months of data available (Binance XAUUSDT history limit)
- 5 OOS windows for production walk-forward; 13 windows for MTF research
- Candidates A/B/C have OOS bootstrap CI entirely positive but modest sample sizes (164–327)
- Daily opportunity analysis shows 3.8 avg best-day points — 30–50 pts/day not supported
- All research uses the fast path (confluence-only, ATR geometry) — differs from production engine
- Forward observation has just started (0 closed signals accumulated)

## 9. Cost-Adjusted Findings (critical, added 2026-08-25)

Execution-cost analysis at realistic XAUUSDT round-trip costs (0.5 pt = spread
+ slippage + commission) fundamentally changes the candidate ranking:

| Candidate | Raw OOS Exp | Cost-adjusted (0.5pt RT) | Fragility |
|---|---|---|---|
| **A (15M trigger, TP1.75R)** | +0.511R | **+0.278R** | ROBUST (positive even at 3x cost) |
| B (5M trigger, TP1.75R) | +0.627R | **−0.217R** | FRAGILE (negative at 1x cost) |
| C (5M trigger, TP2.0R) | +0.453R | **−0.057R** | FRAGILE (negative at 1x cost) |

**Conclusions:**
1. **The 15M trigger (Candidate A) is the only executionally viable candidate.** Its
   edge survives realistic costs; the 5M candidates' edges are an artifact of
   assuming zero execution cost on a timeframe where the per-trade risk (1.5×ATR
   ≈ 5-6 pts) is too small relative to costs.
2. **5M is NOT better than 15M for this system.** It produces a similar signal
   rate but each 5M signal has a thinner edge that costs destroy.
3. **Cost-aware ranking** (reports/mtf_comparison.json) now ranks Candidate A #1
   and de-ranks the 5M candidates, using cost-adjusted expectancy instead of raw.
4. New reports: `reports/cost_robustness.md`, `reports/5m_vs_15m.md`,
   `reports/signal_quality.md`, `reports/monthly/`.
5. Candidate A's edge is strongest in ASIA/LONDON sessions, SHORT direction,
   confluence 70-79 and 90-99; weakest in the LONDON_NY overlap (reports/signal_quality.md).

This finding must gate promotion: a candidate is only promotion-eligible if its
cost-adjusted (1x) expectancy is positive and it survives 1.5x cost.