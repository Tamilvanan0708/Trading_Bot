# RETRACEMENT_BACKTEST — RETRACEMENT_BOS_V1

## Method

`scripts/backtest_retracement.py` runs the exact RETRACEMENT_BOS_V1 engine over
real persisted XAU/USD candles (base 5M dataset resampled to the target
timeframe) with **zero look-ahead bias**.

Execution model (conservative):
- Entry fills at the 0.618 level once a candle touches it after the TP-freeze
  event. No intrabar ordering is assumed.
- SL = 0.236; TP = locked TP (frozen at entry touch).
- SL checked before TP on subsequent candles (same-candle SL priority).
- Spread/slippage applied conservatively (widens entry, narrows TP/SL).

For every setup the backtest records: BOS, Point 2, dynamic highs, TP updates,
entry, locked TP, SL, final outcome, R multiple, MAE, MFE, time to outcome.

## 15M Result (real data, Dec 2025 – Aug 2026)

| Metric | Value |
|--------|-------|
| Total setups (touched entry) | 307 |
| Wins (TP_HIT) | 206 |
| Losses (SL_HIT) | 101 |
| Expired | 0 |
| Win rate | 67.1% |
| Expectancy (R) | +0.144 |
| Total R | +44.22 |
| Profit factor | 1.42 |
| Avg MAE (USD) | 9.11 |
| Avg MFE (USD) | 10.34 |

## Dynamic TP / Freeze Verification

The backtest engine enforces (and the critical unit test `test_tp_freeze_critical_case`
proves):

- **Before entry**: new valid highs raise the dynamic TP.
- **At entry touch**: `locked_tp = dynamic_tp` (the TP of the latest high).
- **After entry**: new highs (E/F/G) are ignored — `locked_tp` never changes.

## Caveat / Promotion Status

This strategy is **RESEARCH**. The 15M sample shows a positive expectancy, but
this is NOT a claim of profitability. Formal promotion remains:

```
RESEARCH -> BACKTESTED -> OOS_VALIDATED -> FORWARD_OBSERVATION
        -> QUALIFIED -> HUMAN_APPROVAL
```

No automatic promotion. Additional OOS, transaction-cost sensitivity, bootstrap
confidence intervals, Monte Carlo, regime and session breakdowns are required
before any forward/paper deployment.

## Run

```bash
.venv\Scripts\python.exe scripts\backtest_retracement.py 15m
.venv\Scripts\python.exe scripts\backtest_retracement.py 5m
```
