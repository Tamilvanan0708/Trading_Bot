"""
Portfolio-honest OOS validation of strategy candidates.

Runs each candidate's VariantSignalEngine through the REAL capital-constrained
BacktestEngine on the locked OOS window (no parameter tuning after this point).
"""

import argparse
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from app.backtesting.engine import BacktestEngine
from app.config.settings import Settings
from app.research.data_fetch import load_real_history
from app.research.metrics import compute_extended_metrics
from app.research.variant_engine import VariantSignalEngine
from app.signals.engine import SignalEngine

OOS_START = __import__("datetime").datetime(2026, 7, 18, tzinfo=__import__("datetime").timezone.utc)
OOS_END = __import__("datetime").datetime(2026, 8, 21, tzinfo=__import__("datetime").timezone.utc)

CANDIDATES = {
    "A_base_sl_tp1.25": {"tp_r": 1.25, "sl_atr": None, "regime": None},
    "B_sl1atr_tp1.25": {"tp_r": 1.25, "sl_atr": 1.0, "regime": None},
    "C_highvol_tp1.5": {"tp_r": 1.5, "sl_atr": None, "regime": "HIGH_VOLATILITY"},
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/research/xauusd_15m_real.json")
    args = parser.parse_args()

    candles = load_real_history(args.data)
    oos = [c for c in candles if OOS_START <= c.timestamp < OOS_END]
    print(f"OOS window candles: {len(oos)}  ({oos[0].timestamp.date()} -> {oos[-1].timestamp.date()})")

    base_settings = Settings(BACKTEST_SPREAD_POINTS=0.5, BACKTEST_SLIPPAGE_PCT=0.0001)
    report = {}
    for name, cfg in CANDIDATES.items():
        print(f"\n=== Candidate {name} (OOS) ===")
        t0 = time.time()
        base_engine = SignalEngine(base_settings)
        variant = VariantSignalEngine(
            base_engine,
            tp_r=cfg["tp_r"],
            sl_atr=cfg["sl_atr"],
            only_regime=cfg["regime"],
        )
        engine = BacktestEngine(base_settings)
        engine.signal_engine = variant
        result = engine.run(oos, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
        print(f"  runtime {time.time()-t0:.0f}s  trades={len(result.trades)}")
        print("  exits:", dict(Counter(t.exit_reason for t in result.trades)))
        m = compute_extended_metrics(result.trades)
        print(f"  WR={m['win_rate_pct']}%  PF={m['profit_factor']}  Exp={m['expectancy_r']}R  "
              f"Net=${m['net_profit_usd']}  MaxDD={m['max_drawdown_pct']}%  "
              f"Sharpe={m['sharpe_like']}  LongWR={m['long_win_rate_pct']}%  ShortWR={m['short_win_rate_pct']}%")
        report[name] = {
            "trades": m["total_trades"],
            "win_rate_pct": m["win_rate_pct"],
            "profit_factor": m["profit_factor"],
            "expectancy_r": m["expectancy_r"],
            "net_profit_usd": m["net_profit_usd"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "sharpe_like": m["sharpe_like"],
            "long_win_rate_pct": m["long_win_rate_pct"],
            "short_win_rate_pct": m["short_win_rate_pct"],
            "avg_r": m["avg_r"],
            "median_r": m["median_r"],
        }

    os.makedirs("data/research", exist_ok=True)
    with open("data/research/candidate_oos.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nCandidate OOS results written to data/research/candidate_oos.json")


if __name__ == "__main__":
    main()