"""
Real XAU/USD strategy validation runner.

Fetches (or loads) real Binance XAUUSDT 15M data, runs walk-forward
TRAIN/VALIDATION/TEST validation, computes R-multiple, regime, session,
confluence, AI-outcome, Monte Carlo and bootstrap analyses, and writes a
professional out-of-sample report.

Usage:
  python scripts/run_strategy_research.py --days 180
  python scripts/run_strategy_research.py --data data/research/xauusd_15m_real.json
  python scripts/run_strategy_research.py --train-months 2 --val-months 1 --test-months 1 --days 240
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from app.ai.validator import AIValidator
from app.config.settings import get_settings
from app.core.constants import TimeFrame
from app.data.ingestion import validate_candles
from app.research.ai_outcomes import ai_outcome_analysis
from app.research.bootstrap import bootstrap_confidence_intervals
from app.research.classification import classify_strategy
from app.research.confluence import confluence_bucket_analysis
from app.research.data_fetch import fetch_real_history, load_real_history
from app.research.forensics import trade_forensics
from app.research.metrics import compute_extended_metrics
from app.research.monte_carlo import monte_carlo_simulation
from app.research.regime import regime_analysis
from app.research.report import build_validation_report, summarize_report
from app.research.rmultiple import r_multiple_distribution
from app.research.session import session_analysis
from app.research.walkforward import run_walk_forward


def main() -> None:
    parser = argparse.ArgumentParser(description="Real XAU/USD strategy validation.")
    parser.add_argument("--data", type=str, help="Path to existing real-history JSON file.")
    parser.add_argument("--days", type=int, default=180, help="Days of history to fetch if no --data.")
    parser.add_argument("--slice-days", type=int, default=0,
                        help="If >0, use only the most recent N days of data (for tractable runtimes).")
    parser.add_argument("--train-months", type=int, default=3)
    parser.add_argument("--val-months", type=int, default=1)
    parser.add_argument("--test-months", type=int, default=1)
    parser.add_argument("--step-months", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=150)
    parser.add_argument("--risk", type=float, default=1.0)
    parser.add_argument("--ai", action="store_true", help="Record AI validation status per trade.")
    parser.add_argument("--simulations", type=int, default=10000, help="Monte Carlo simulations.")
    args = parser.parse_args()

    # 1. Real data
    if args.data:
        candles = load_real_history(args.data)
    else:
        candles = asyncio.run(fetch_real_history(days=args.days))

    if args.slice_days > 0:
        cutoff = candles[-1].timestamp - timedelta(days=args.slice_days)
        candles = [c for c in candles if c.timestamp >= cutoff]
        print(f"[INFO] Sliced to last {args.slice_days} days: {len(candles)} candles.")
    if len(candles) < 400:
        print(f"[ABORT] Insufficient data ({len(candles)} candles). Need >= 400.")
        sys.exit(2)

    vresult = validate_candles(candles, TimeFrame.M15, strict_gaps=False)
    data_quality = {
        "label": "REAL DATA (Binance XAUUSDT futures)",
        "gaps": vresult.gaps,
        "duplicates": vresult.duplicates,
        "out_of_order": vresult.out_of_order,
        "invalid_ohlc": vresult.invalid_ohlc,
        "valid": vresult.valid,
        "timezone": "UTC",
        "timeframes": ["15m", "30m", "1h", "4h"],
        "spread_points": get_settings().BACKTEST_SPREAD_POINTS,
        "slippage_pct": get_settings().BACKTEST_SLIPPAGE_PCT,
        "transaction_cost_usd": get_settings().BACKTEST_TRANSACTION_COST_USD,
    }
    if not vresult.valid:
        print("[ABORT] Data quality insufficient:", vresult.errors[:3])
        sys.exit(2)

    # 2. Walk-forward
    print("[1/6] Running walk-forward validation...")
    ai_validator = AIValidator() if args.ai else None
    windows = run_walk_forward(
        candles,
        train_months=args.train_months,
        val_months=args.val_months,
        test_months=args.test_months,
        step_months=args.step_months,
        warmup_bars=args.warmup,
        risk_percent=args.risk,
        ai_validator=ai_validator,
    )
    if not windows:
        print("[ABORT] No walk-forward windows fit in the data period.")
        sys.exit(2)

    # 3. OOS trades (all test windows)
    oos_trades = []
    for wr in windows:
        if wr.test_result:
            oos_trades.extend(wr.test_result.trades)
    if not oos_trades:
        print("[INFO] No out-of-sample trades generated. Classification: INCONCLUSIVE.")

    oos_metrics = compute_extended_metrics(oos_trades) if oos_trades else {}
    rs = [t.pnl_r or 0.0 for t in oos_trades] if oos_trades else []

    print("[2/6] R-multiple distribution...")
    rdist = r_multiple_distribution(rs)

    print("[3/6] Regime / session / confluence / AI analysis...")
    regime_bd = regime_analysis(oos_trades, candles) if oos_trades else {}
    session_bd = session_analysis(oos_trades) if oos_trades else {}
    confluence_bd = confluence_bucket_analysis(oos_trades) if oos_trades else {}
    ai_eff = ai_outcome_analysis(oos_trades) if oos_trades else {}

    print("[3b/6] Trade forensics (MAE/MFE / target reachability)...")
    forensics = trade_forensics(oos_trades, candles) if oos_trades else {"trades": 0}

    print("[4/6] Monte Carlo simulation...")
    mc = monte_carlo_simulation(rs, simulations=args.simulations) if rs else {}

    print("[5/6] Bootstrap confidence intervals...")
    boot = bootstrap_confidence_intervals(rs) if rs else {}

    # 6. Classification
    print("[6/6] Classifying strategy...")
    classification = classify_strategy(windows)

    report = build_validation_report(
        symbol="XAUUSD",
        candles_start=candles[0].timestamp,
        candles_end=candles[-1].timestamp,
        candle_count=len(candles),
        data_quality=data_quality,
        window_results=windows,
        monte_carlo=mc,
        bootstrap=boot,
        regime_breakdown=regime_bd,
        session_breakdown=session_bd,
        confluence_breakdown=confluence_bd,
        ai_effectiveness=ai_eff,
        classification=classification,
    )
    report["r_multiple_distribution"] = rdist
    report["oos_metrics"] = oos_metrics
    report["forensics"] = forensics

    os.makedirs("data/research", exist_ok=True)
    path = "data/research/latest_report.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print()
    print(summarize_report(report))
    print()
    print(f"Full report written to {path}")
    print()
    print("R-multiple distribution:")
    print(json.dumps(rdist, indent=2))
    if regime_bd:
        print("\nPerformance by regime:")
        print(json.dumps(regime_bd, indent=2))
    if session_bd:
        print("\nPerformance by session:")
        print(json.dumps(session_bd, indent=2))


if __name__ == "__main__":
    main()