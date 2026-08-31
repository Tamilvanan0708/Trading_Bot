# RETRACEMENT_AUDIT — RETRACEMENT_BOS_V1 No-Look-Ahead & Safety Audit

## No-Look-Ahead Audit (runtime)

`scripts/audit_retracement_nolookahead.py` verifies the engine never uses future
data.

Result: **PASS**

| Check | Result |
|-------|--------|
| Batch vs online Point 2 identity | PASS (99.0 = 99.0) |
| Truncation invariance (future candles can't change past setups) | PASS (Point 2 99.0=99.0, BOS 125.0=125.0) |
| Frozen-TP invariance (post-entry highs ignored) | PASS (locked_tp=200.0 unchanged at 210/215/220) |

### Why the engine is look-ahead free

1. `run_series` walks candles strictly chronologically.
2. Swing confirmation uses the existing fractal detector with `right_bars=3`.
   A swing is only visible when `swing.index + right_bars <= current_index`.
3. Point 2 = min low of the BOS move, computed only over candles from the last
   confirmed swing low to the current candle (never future candles).
4. Dynamic TP updates use only confirmed swing highs; the TP for an earlier
   candle cannot be changed by a later candle.
5. Entry touch and TP freeze are evaluated on the current candle only.

## Intrabar Ordering

OHLC alone cannot determine whether a high occurred before the entry touch
within the same candle. The engine uses the **conservative** convention:
- SL is checked before TP (same-candle SL priority), matching the existing
  `BacktestEngine`.
- Entry is treated as filled once a candle touches 0.618; no ordering is
  invented.

## Safety Audit

| Guarantee | Status |
|-----------|--------|
| No broker orders / real-money execution | PASS (signal-only module) |
| No automatic trade placement | PASS |
| AI/ML cannot alter deterministic levels | PASS (advisory only by design) |
| Observation mode remains non-trading | PASS (unchanged) |
| No fabricated market data | PASS (uses real persisted candles only) |
| No look-ahead bias | PASS (audited above) |
| Point 2 is never moved after identification | PASS |
| TP is frozen at entry touch, never moved after | PASS (unit test + audit) |
| Invalidated setups cannot become active again | PASS (state machine is forward-only) |

## Known Limitations

- The strategy is RESEARCH stage. Positive 15M backtest expectancy is NOT a
  claim of profitability.
- No OOS split, bootstrap CI, Monte Carlo, or transaction-cost sensitivity yet.
- No live/paper forward observation yet.
- Promotion requires: RESEARCH -> BACKTESTED -> OOS_VALIDATED ->
  FORWARD_OBSERVATION -> QUALIFIED -> HUMAN_APPROVAL (no automatic promotion).
