"""
Trade forensics: MAE/MFE and target-reachability analysis.

MAE  = Maximum Adverse Excursion (how far price moved AGAINST entry before exit)
MFE  = Maximum Favorable Excursion (how far price moved WITH the entry)

Normalised to R, these reveal whether:
  - the take-profit target is actually reachable, or
  - the stop-loss is being hit on noise.
"""


from app.backtesting.models import SimulatedTrade
from app.core.constants import SignalDirection
from app.data.models import Candle


def _mae_mfe(trade: SimulatedTrade, candles: list[Candle]) -> dict:
    risk = max(0.01, abs(trade.entry_price - trade.stop_loss))
    mae = 0.0
    mfe = 0.0
    for c in candles:
        if c.timestamp <= trade.entry_time:
            continue
        if trade.exit_time and c.timestamp > trade.exit_time:
            break
        if trade.direction == SignalDirection.LONG:
            mae = max(mae, trade.entry_price - c.low)
            mfe = max(mfe, c.high - trade.entry_price)
        else:
            mae = max(mae, c.high - trade.entry_price)
            mfe = max(mfe, trade.entry_price - c.low)
    return {
        "mae_r": round(mae / risk, 2),
        "mfe_r": round(mfe / risk, 2),
        "pnl_r": round(trade.pnl_r or 0.0, 2),
        "exit": trade.exit_reason,
    }


def trade_forensics(trades: list[SimulatedTrade], candles: list[Candle]) -> dict:
    """Compute MAE/MFE, target reachability and SL-on-noise statistics."""
    ordered = sorted(candles, key=lambda c: c.timestamp)
    rows = [_mae_mfe(t, ordered) for t in trades if t.exit_time]
    n = len(rows)
    if n == 0:
        return {"trades": 0}

    result = {"trades": n, "by_trade": rows[:200]}
    for threshold in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
        reached = sum(1 for r in rows if r["mfe_r"] >= threshold)
        result[f"mfe_ge_{threshold}r_count"] = reached
        result[f"mfe_ge_{threshold}r_pct"] = round(reached / n * 100.0, 2)

    sl_touched = sum(1 for r in rows if r["mae_r"] >= 1.0)
    result["sl_touched_count"] = sl_touched
    result["sl_touched_pct"] = round(sl_touched / n * 100.0, 2)

    both = sum(1 for r in rows if r["mae_r"] >= 1.0 and r["mfe_r"] >= 1.0)
    result["sl_and_1r_both_touched_pct"] = round(both / n * 100.0, 2)

    # Highest MFE reach rate gives the "natural" target distance.
    result["median_mfe_r"] = round(sorted(r["mfe_r"] for r in rows)[n // 2], 2)
    result["median_mae_r"] = round(sorted(r["mae_r"] for r in rows)[n // 2], 2)

    # Diagnosis heuristics
    tp_reachable = result.get("mfe_ge_2_0r_pct", 0) >= 20.0
    sl_on_noise = sl_touched / n >= 0.7
    diagnosis = []
    if not tp_reachable:
        diagnosis.append("TP target rarely reached (target may be beyond market excursion).")
    if sl_on_noise:
        diagnosis.append("SL touched in most trades (stop may be too tight relative to volatility).")
    if not diagnosis:
        diagnosis.append("No obvious reachability pathology.")
    result["diagnosis"] = diagnosis
    return result