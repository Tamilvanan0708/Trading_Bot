"""
Build a deterministic training/research dataset for RETRACEMENT_BOS_V1.

Uses ONLY real historical XAU/USD candles already persisted in the repository
(data/research/xauusd_5m_2yr.json or data/research/xauusd_15m_full.json).
No fabricated candles.

Labels are generated from the EXACT deterministic strategy rules:
  - bullish BOS detection
  - Point 2 identification
  - valid high progression
  - Fibonacci level construction
  - entry-touch detection
  - TP freeze event
  - post-entry TP immutability
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core.constants import TimeFrame
from app.data.models import Candle
from app.data.timeframe_resampler import resample_candles
from app.retracement.engine import RetracementBOSEngine

DATA_DIR = os.path.join(ROOT, "data", "research")
OUTPUT_DIR = os.path.join(ROOT, "data", "retracement_dataset")


def _load_5m_candles() -> list[Candle]:
    """Loads REAL persisted historical candles. Files are dicts with a
    'candles' list of OHLCV dicts."""
    path = os.path.join(DATA_DIR, "xauusd_5m_2yr.json")
    raw = _load_candles_file(path)
    if raw:
        return raw
    path = os.path.join(DATA_DIR, "xauusd_15m_full.json")
    return _load_candles_file(path) or []


def _load_candles_file(path: str) -> list[Candle]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("candles", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    candles = []
    for r in entries:
        if not isinstance(r, dict) or "timestamp" not in r:
            continue
        ts = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
        try:
            candles.append(Candle(
                timestamp=ts,
                open=float(r.get("open", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                close=float(r.get("close", 0)),
                volume=float(r.get("volume", 0) or 0),
            ))
        except (ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c.timestamp)
    return candles


def build_dataset(timeframe: TimeFrame = TimeFrame.M15, output_csv: str | None = None) -> list[dict]:
    base = _load_5m_candles()
    if not base:
        print("ERROR: no real historical data found in data/research/")
        sys.exit(1)
    if timeframe != TimeFrame.M5:
        candles = resample_candles(base, timeframe)
    else:
        candles = base

    print(f"Loaded {len(base)} base candles; resampled to {timeframe.value}: {len(candles)} candles.")

    engine = RetracementBOSEngine(symbol="XAUUSD", timeframe=timeframe.value)
    setups, events = engine.run_series(candles)
    rows: list[dict] = []

    # Build one row per event so the dataset captures every state transition
    for ev in events:
        # Find the setup this event belongs to (match by id against the setup list)
        row = {
            "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
            "setup_id": ev.setup_id,
            "event_type": ev.event_type.value,
            "price": ev.price,
            "state_before": ev.state_before.value,
            "state_after": ev.state_after.value,
            "bos_price": None,
            "point_2_price": None,
            "current_high_price": None,
            "fib_0": None,
            "fib_0_236": None,
            "fib_0_618": None,
            "fib_1_000": None,
            "fib_1_618": None,
            "entry_price": None,
            "sl_price": None,
            "dynamic_tp": None,
            "locked_tp": None,
            "tp_locked": None,
            "entry_touched": None,
        }
        # Attach the matching setup's level snapshot for context
        for s in setups:
            if s.setup_id == ev.setup_id:
                row.update({
                    "bos_price": s.bos_price,
                    "point_2_price": s.point_2_price,
                    "current_high_price": s.current_high_price,
                    "fib_0": s.fib_0,
                    "fib_0_236": s.fib_0_236,
                    "fib_0_618": s.fib_0_618,
                    "fib_1_000": s.fib_1_000,
                    "fib_1_618": s.fib_1_618,
                    "entry_price": s.entry_price,
                    "sl_price": s.sl_price,
                    "dynamic_tp": s.dynamic_tp,
                    "locked_tp": s.locked_tp,
                    "tp_locked": s.tp_locked,
                    "entry_touched": s.entry_touched,
                })
                break
        rows.append(row)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = output_csv or os.path.join(OUTPUT_DIR, f"retracement_bos_v1_{timeframe.value}.csv")
    if rows:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} event rows to {out_path}")
    else:
        print("No setups generated on this dataset.")

    # Summary stats
    states = {}
    for r in rows:
        states[r["state_after"]] = states.get(r["state_after"], 0) + 1
    print(f"State distribution (event state_after): {states}")
    return rows


if __name__ == "__main__":
    tf_arg = sys.argv[1] if len(sys.argv) > 1 else "15m"
    tf = TimeFrame(tf_arg)
    build_dataset(tf)
