# RETRACEMENT_BOS_V1 — Exact Bullish BOS Retracement Strategy

## Overview

`RETRACEMENT_BOS_V1` is a deterministic bullish break-of-structure (BOS)
retracement strategy. It is **not** generic Fibonacci retracement: it uses an
exact, fixed five-level Fibonacci structure anchored to a specific swing point
(Point 2) with strict TP dynamic/freeze semantics.

Module: `app/retracement/`
Strategy version: `RETRACEMENT_BOS_V1`
Direction: LONG (bullish) only in this module.

## Exact Fibonacci Level Mapping (NEVER swapped)

| Ratio | Label | Meaning |
|-------|-------|---------|
| 1.618 | **BLACK LINE 1** | Extension / reference line. NOT normal TP. |
| 1.000 | **TP** | Target. Dynamic before entry; FROZEN at entry touch. |
| 0.618 | **ENTRY** | Entry level. Price retraces down and touches it. |
| 0.236 | **SL** | Stop loss. NOT 0.000, NOT 0.382/0.5/1.000/1.618. |
| 0.000 | **BLACK LINE 2** | Point 2 anchor. NOT SL. |

These five levels and their semantics are hard-coded in
`app/retracement/models.py` (`RetracementLevel.label`) and are verified by
`test_level_mapping_is_exact`.

## Strategy Pipeline

```
BULLISH BOS
    │  price closes above the relevant previous confirmed swing high
    ▼
POINT 2 (the LOW of the BOS move)  ->  0.000 anchor (BLACK LINE 2)
    ▼
FIB ACTIVE  (0.236 / 0.618 / 1.000 / 1.618 computed from Point 2 + valid high)
    ▼
TP DYNAMIC  (new VALID HIGHS update 1.000 TP and 1.618 extension)
    ▼
ENTRY TOUCHED  (price first retraces and touches 0.618)
    ▼
TP FROZEN  (locked_tp = current dynamic TP; NEVER moves again)
    ▼
COMPLETED (TP_HIT / SL_HIT)  or  INVALIDATED (close < Point 2)
```

### Step 1 — Bullish BOS
A bullish BOS occurs when a candle **closes above** the relevant previous
**confirmed swing high**. The BOS reference comes from the existing market
structure swing detection (`app/indicators/swings.detect_swings`). No setup is
created before BOS confirmation. BOS must happen first.

### Step 2 — Point 2
After BOS, Point 2 = **the lowest low of the BOS move**. The BOS move spans
from the last confirmed swing low before the BOS reference high, up to and
including the BOS candle. `0.000 = Point 2` always.

### Step 3 — Fibonacci levels
For a bullish setup with Point 2 = P2 and current valid high = H:

```
range = H - P2
0.000  = P2
0.236  = P2 + 0.236 * range
0.618  = P2 + 0.618 * range   (ENTRY)
1.000  = H                    (TP)
1.618  = P2 + 1.618 * range   (BLACK LINE 1)
```

### Step 4 — Dynamic TP (before entry)
As price forms new **valid highs** (new confirmed swing highs above the current
high endpoint), the Fibonacci high endpoint updates, so both `1.000 TP` and
`1.618` are recalculated. The TP may move upward before entry.

### Step 5 — Entry touch + TP freeze (CRITICAL)
`ENTRY` is always `0.618`. When price first retraces down and a candle spans
the 0.618 level, the engine:
1. sets `entry_touched = True`
2. reads the **current** dynamic TP and stores it as `locked_tp`
3. sets `tp_locked = True`
4. transitions `ENTRY_TOUCHED -> TP_FROZEN`

From that moment the TP is immutable. New highs after entry are logged as
`POST_ENTRY_HIGH_IGNORED` and never change `locked_tp`. This is enforced by the
engine state machine and covered by `test_tp_freeze_critical_case`.

### Step 6 — SL / TP outcome
- SL = `0.236`. If a candle's low <= SL, outcome = `SL_HIT`.
- TP = `locked_tp`. If a candle's high >= locked TP, outcome = `TP_HIT`.
- Same-candle SL priority (conservative): SL is checked before TP.

## State Machine

```
NO_SETUP -> BOS_DETECTED -> POINT_2_IDENTIFIED -> FIB_ACTIVE -> TP_DYNAMIC
        -> ENTRY_TOUCHED -> TP_FROZEN -> COMPLETED / INVALIDATED
```

Transitions are strictly forward. `TP_FROZEN` never returns to `TP_DYNAMIC`.
`INVALIDATED` / `COMPLETED` setups never become active again.

## Invalidation Conditions

1. **Price closes below Point 2** (`0.000` anchor). Reason:
   `"Price closed below Point 2 (0.000 anchor)."`

An invalidated setup is final; it cannot silently become active again.

## Valid High Definition

A valid high is a **confirmed swing high** (fractal: high[i] > left_bars and
> right_bars neighbours) that is above the current high endpoint and is
confirmed (swing.index + right_bars <= current candle index). Random candle
highs are never used for TP updates.

## No Look-Ahead Bias

- `process_candle` / `run_series` walk candles strictly chronologically.
- Swing confirmation delay (`right_bars = 3`) is respected: a swing is only
  visible `right_bars` candles after its index.
- Point 2 and Fibonacci levels only use candles up to the current candle.
- Future highs/lows/BOS/candles are never used.

## Intrabar Ordering

OHLC candles alone cannot establish whether a high formed before the entry
touch within the same candle. The engine uses the **conservative** convention
consistent with the existing backtester: SL is checked before TP, and entry is
considered filled once the candle touches 0.618. No intrabar ordering is
invented.

## Multi-Timeframe

The engine is timeframe-agnostic and can run on 5m/15m/30m/1h/4h by resampling
the real base 5M dataset. Not every timeframe produces a valid setup — the
engine naturally returns `NO_SETUP` when BOS/Point 2 requirements are unmet.

## Safety

Signal-only. No broker orders, no real-money execution, no automatic trading.
AI/ML is advisory and cannot alter the deterministic levels, move Point 2,
change the entry/SL/TP semantics, or override safety gates.

## API

- `GET /retracement/{symbol}` — current/latest setup (or `NO_SETUP`, no
  fabricated levels)
- `POST /retracement/{symbol}/run` — run engine over real historical data and
  persist setups + event history
- `GET /retracement/{symbol}/history` — persisted setup + event history

## Persistence

Setups persist in `retracement_setups`; event history in `retracement_events`.
A frozen TP survives an application restart (`test_restart_preserves_frozen_tp`).
