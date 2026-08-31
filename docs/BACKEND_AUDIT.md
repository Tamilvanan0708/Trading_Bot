# BACKEND AUDIT — XAU/USD AI Signal Intelligence Terminal

## Audit Scope & Method
Inspected every module under `app/` and `scripts/`. Verified data flow end to
end: live tick → candle close → MTF resample → confluence → signal → AI →
admission → storage → outcome tracking → research. Verified no look-ahead, no
future-candle leakage, no duplicate signal generation, no broker path.

## Verified Working Components
| Component | Status | Notes |
|---|---|---|
| Binance WebSocket feed | ✅ | fstream.binance.com, bookTicker + aggTrade, reconnect with exponential backoff |
| Binance REST history | ✅ | paginated, retries, backfill on reconnect |
| History refresh loop | ✅ | periodic + emergency (cooldown-gated) |
| Data-quality validation | ✅ | gaps/dup/ooo/stale/invalid, blocks trading when degraded |
| Closed-candle processing | ✅ | forming candle never enters analysis |
| IncrementalResampler | ✅ | 5M/15M/30M/1H/4H, O(n), determinism-tested vs pandas |
| MarketStructureDetector | ✅ | swings, HH/HL/LH/LL, trend, BOS/CHoCH |
| SMCEngine | ✅ | FVG, order blocks, liquidity sweeps, premium/discount |
| FibonacciEngine | ✅ | retracements, golden pocket, direction |
| ConfluenceEngine | ✅ | weighted 0-100, MTF-role configurable, R:R component |
| SignalEngine | ✅ | deterministic, MTF-configurable, ATR fallback SL/TP |
| AI validation | ✅ | heuristic (mock) + optional LLM; advisory only |
| Scheduler | ✅ | singleton, restart-skip dedup, weekend-aware |
| AdmissionGate | ✅ | data-quality, AI, regime, duplicates, limits |
| PaperTradingService | ✅ | restart-safe state machine, fees/spread/slippage |
| SignalOutcomeTracker | ✅ | forward MAE/MFE/TP/SL/final R, timezone-safe |
| CandidateObservation | ✅ | A/B/C side-by-side on every closed candle |
| Research engine | ✅ | walk-forward, MC, bootstrap, classification, cost robustness |
| Telegram | ✅ | signal + system alerts, dedup, persistence |
| Terminal SPA + dashboard | ✅ | shared frontend, real APIs only |

## Bugs Found & Fixed (this engagement)
1. Pipeline opened paper trades unconditionally (pre-admission) — removed;
   scheduler is sole authority.
2. Timezone-aware vs naive datetime crash in outcome tracker — normalized.
3. Classification used per-window averages → pooled OOS (conservative).
4. CORS wildcard + credentials — configurable origins, safe default.
5. Foreign keys not enforced — `PRAGMA foreign_keys=ON`.
6. Outcome tracker dict-mutation during iteration — fixed.
7. `_has_conflicting_position` deleted by a bad edit — restored.
8. Cost model: round-trip cost applied as per-side → half per side (correct).
9. `/notifications` 500: wrong model name (`NotificationModel`→`NotificationLogModel`)
   and non-existent columns — fixed.
10. Terminal static asset path shadowed by SPA fallback — `static/` prefix stripped.

## No-Duplicate / No-Overlap Verification
- Scheduler restart-skip prevents re-processing the last closed candle.
- Candidate dedup: `strategy_version + candle + direction`.
- Reconnect backfill merges REST history without duplicating existing candles
  (validated gaps=0 dup=0 ooo=0).
- Outcome tracker restores open signals and advances from the last processed
  candle timestamp (no double-processing).

## Known Weaknesses / Limitations
- Only ~8.5 months of Binance XAUUSDT history exists (symbol launch limit).
- Forward observation sample: 0 closed candidate signals so far.
- The daily 30–50 pt target is not supported by the data (reported honestly).
- Docker build not executed locally (no Docker daemon); configs verified.
