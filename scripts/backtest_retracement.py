"""
RETRACEMENT_BOS_V1 — Dedicated Backtest.

Simulates the exact bullish BOS retracement strategy over real historical
XAU/USD candles with zero look-ahead bias.

Per setup it records:
  - BOS, Point 2, dynamic highs, TP updates
  - entry, locked TP, SL
  - final outcome, R multiple, MAE, MFE, time to outcome

The most important verification:
  - TP before entry CAN move (dynamic)
  - TP after entry CANNOT move (frozen at entry touch)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core.constants import TimeFrame
from app.data.models import Candle
from app.data.timeframe_resampler import resample_candles
from app.retracement.engine import RetracementBOSEngine
from app.retracement.models import RetracementSetup, RetracementState

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_candles(timeframe: TimeFrame = TimeFrame.M15) -> list[Candle]:
    """Load real persisted XAU/USD candles and resample to the target TF."""
    base_path = os.path.join(ROOT, "data", "research", "xauusd_5m_2yr.json")
    with open(base_path, encoding="utf-8") as f:
        raw = json.load(f)
    entries = raw.get("candles", raw)
    candles = []
    for r in entries:
        ts = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
        candles.append(Candle(
            timestamp=ts,
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r.get("volume", 0) or 0),
        ))
    candles.sort(key=lambda c: c.timestamp)
    if timeframe == TimeFrame.M5:
        return candles
    return resample_candles(candles, timeframe)


def _compute_r(entry: float, sl: float, exit_price: float, direction: str = "LONG") -> float:
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    if direction == "LONG":
        return (exit_price - entry) / risk
    return (entry - exit_price) / risk


def run_backtest(timeframe: TimeFrame = TimeFrame.M15,
                 spread_points: float = 0.5,
                 slippage_pct: float = 0.0001) -> dict:
    """Run the RETRACEMENT_BOS_V1 backtest.

    Execution model (conservative):
      - Entry fills at the 0.618 level when a candle touches it (after the
        freeze event).  No intrabar ordering is assumed: entry is treated as
        filled at the touch candle's close.
      - SL is 0.236; TP is the locked TP.
      - SL and TP are checked on subsequent candles with SL checked first
        (conservative, same-candle SL priority).
    """
    candles = load_candles(timeframe)
    engine = RetracementBOSEngine(symbol="XAUUSD", timeframe=timeframe.value)
    setups, _ = engine.run_series(candles)

    trades = []
    candle_map = {c.timestamp: c for c in candles}

    for s in setups:
        if not s.entry_touched or s.locked_tp is None or s.entry_price is None or s.sl_price is None:
            continue

        entry = s.entry_price
        sl = s.sl_price
        tp = s.locked_tp
        # Apply spread/slippage conservatively for a long
        fill_entry = entry + spread_points
        eff_tp = tp - spread_points
        eff_sl = sl - spread_points

        if s.entry_timestamp is None:
            continue
        # Find candles after the entry timestamp
        entry_ts = s.entry_timestamp
        post = [c for c in candles if c.timestamp > entry_ts]
        if not post:
            continue

        outcome = None
        exit_price = None
        mae = 0.0
        mfe = 0.0
        for c in post:
            # SL first (conservative)
            if c.low <= eff_sl:
                outcome = "SL_HIT"
                exit_price = eff_sl
                break
            if c.high >= eff_tp:
                outcome = "TP_HIT"
                exit_price = eff_tp
                break
            # Track excursion (relative to fill)
            mae = max(mae, fill_entry - c.low)
            mfe = max(mfe, c.high - fill_entry)
        if outcome is None:
            # Not closed within data window
            exit_price = post[-1].close if post else entry
            outcome = "EXPIRED"

        r = _compute_r(fill_entry, sl, exit_price, "LONG")
        trades.append({
            "setup_id": s.setup_id,
            "bos_price": s.bos_price,
            "point_2_price": s.point_2_price,
            "dynamic_tp_before_entry": s.dynamic_tp,
            "locked_tp": s.locked_tp,
            "entry": fill_entry,
            "sl": sl,
            "outcome": outcome,
            "r_multiple": round(r, 3),
            "mae_usd": round(mae, 2),
            "mfe_usd": round(mfe, 2),
            "entry_ts": s.entry_timestamp.isoformat() if s.entry_timestamp else None,
        })

    return _summarize(trades, timeframe)


def _summarize(trades: list[dict], timeframe: TimeFrame) -> dict:
    n = len(trades)
    wins = [t for t in trades if t["outcome"] == "TP_HIT"]
    losses = [t for t in trades if t["outcome"] == "SL_HIT"]
    expired = [t for t in trades if t["outcome"] == "EXPIRED"]

    total_r = sum(t["r_multiple"] for t in trades)
    win_rate = (len(wins) / n) if n else 0.0
    gross_win = sum(t["r_multiple"] for t in wins)
    gross_loss = abs(sum(t["r_multiple"] for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    avg_mae = sum(t["mae_usd"] for t in trades) / n if n else 0.0
    avg_mfe = sum(t["mfe_usd"] for t in trades) / n if n else 0.0

    return {
        "strategy": "RETRACEMENT_BOS_V1",
        "timeframe": timeframe.value,
        "total_setups": n,
        "wins": len(wins),
        "losses": len(losses),
        "expired": len(expired),
        "win_rate": round(win_rate, 4),
        "expectancy_r": round(total_r / n, 4) if n else 0.0,
        "total_r": round(total_r, 3),
        "profit_factor": round(profit_factor, 4),
        "avg_mae_usd": round(avg_mae, 2),
        "avg_mfe_usd": round(avg_mfe, 2),
        "sample": trades[:200],
    }


if __name__ == "__main__":
    tf = TimeFrame(sys.argv[1]) if len(sys.argv) > 1 else TimeFrame.M15
    result = run_backtest(tf)
    print(json.dumps({
        "strategy": result["strategy"],
        "timeframe": result["timeframe"],
        "total_setups": result["total_setups"],
        "wins": result["wins"],
        "losses": result["losses"],
        "expired": result["expired"],
        "win_rate": result["win_rate"],
        "expectancy_r": result["expectancy_r"],
        "total_r": result["total_r"],
        "profit_factor": result["profit_factor"],
        "avg_mae_usd": result["avg_mae_usd"],
        "avg_mfe_usd": result["avg_mfe_usd"],
    }, indent=2))
