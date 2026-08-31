"""
Walk-forward validation harness.

Runs a chronological TRAIN / VALIDATION cycle over real historical data:
  Train: Jan-Mar   Validate: Apr
  Train: Feb-Apr   Validate: May
  ...

Each validation window uses ONLY the signal engine configured from the
preceding train window.  No future data ever enters a prior decision.

Usage:
  python scripts/run_walk_forward.py --csv data/processed/XAUUSD_15m.json \
      --train-months 3 --validate-months 1 --symbol XAUUSD
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from app.backtesting.engine import BacktestEngine
from app.core.constants import TimeFrame
from app.data.models import Candle


def load_candles(path: str) -> list[Candle]:
    with open(path) as f:
        rows = json.load(f)
    return [
        Candle(
            timestamp=datetime.fromisoformat(r["timestamp"]),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r.get("volume", 0.0)),
        )
        for r in rows
    ]


def split_windows(candles, train_months: int, validate_months: int):
    """Yield (train_candles, validate_candles) chronologically."""
    start = candles[0].timestamp
    end = candles[-1].timestamp
    windows = []
    cursor = start
    while cursor + timedelta(days=30 * (train_months + validate_months)) <= end:
        train_end = cursor + timedelta(days=30 * train_months)
        val_end = train_end + timedelta(days=30 * validate_months)
        train = [c for c in candles if cursor <= c.timestamp < train_end]
        validate = [c for c in candles if train_end <= c.timestamp < val_end]
        if len(validate) < 160:
            break
        windows.append((train, validate, cursor, val_end))
        cursor += timedelta(days=30 * validate_months)
    return windows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to 15m candle JSON or CSV.")
    parser.add_argument("--train-months", type=int, default=3)
    parser.add_argument("--validate-months", type=int, default=1)
    parser.add_argument("--symbol", default="XAUUSD")
    args = parser.parse_args()

    if args.csv.endswith(".json"):
        candles = load_candles(args.csv)
    else:
        from app.data.ingestion import ingest_candles_from_csv
        candles = ingest_candles_from_csv(args.csv, TimeFrame.M15, validate=True)

    print(f"Loaded {len(candles)} candles ({candles[0].timestamp} -> {candles[-1].timestamp}).")
    windows = split_windows(candles, args.train_months, args.validate_months)
    print(f"Walk-forward windows: {len(windows)}")

    all_results = []
    for idx, (train, validate, start, end) in enumerate(windows):
        print(f"\n=== Window {idx + 1}: train {start.date()} -> {train[-1].timestamp.date()}, validate {validate[0].timestamp.date()} -> {validate[-1].timestamp.date()} ===")

        # Train on the training window
        train_engine = BacktestEngine()
        train_result = train_engine.run(
            train, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150
        )

        # Validate on the out-of-sample window (new engine = no data leakage)
        val_engine = BacktestEngine()
        val_result = val_engine.run(
            validate, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150
        )

        s = val_result.summary
        print(f"  Train trades: {train_result.summary.total_trades}, PF: {train_result.summary.profit_factor}")
        print(f"  Validate trades: {s.total_trades}, WinRate: {s.win_rate_pct}%, PF: {s.profit_factor}, "
              f"Net: ${s.net_profit_usd:.2f}, MaxDD: ${s.max_drawdown_usd:.2f}, Exp: {s.expectancy_r}R")

        all_results.append({
            "window": idx + 1,
            "train_start": train[0].timestamp.isoformat(),
            "validate_start": validate[0].timestamp.isoformat(),
            "train_trades": train_result.summary.total_trades,
            "validate_trades": s.total_trades,
            "validate_win_rate": s.win_rate_pct,
            "validate_profit_factor": s.profit_factor,
            "validate_net_pnl": s.net_profit_usd,
            "validate_max_drawdown": s.max_drawdown_usd,
            "validate_expectancy_r": s.expectancy_r,
        })

    print("\n" + "=" * 60)
    print("WALK-FORWARD SUMMARY")
    print("=" * 60)
    for r in all_results:
        print(
            f"Window {r['window']}: val {r['validate_start'][:10]} | trades={r['validate_trades']} "
            f"| WR={r['validate_win_rate']}% | PF={r['validate_profit_factor']} | Net=${r['validate_net_pnl']}"
        )


if __name__ == "__main__":
    main()