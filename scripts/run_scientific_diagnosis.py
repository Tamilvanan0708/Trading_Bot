"""
COMPREHENSIVE STRATEGY DIAGNOSIS & SIGNAL-SELECTION RESEARCH
=============================================================

Phases 3-6 of the research program, executed on REAL Binance XAUUSDT data:

  PHASE 3 — Scientific failure diagnosis (entry / target / stop / session /
             regime / long-vs-short / feature / confluence-bucket analysis)
  PHASE 4 — Signal-selection research (ALL vs FILTERED portfolios)
  PHASE 5 — Multi-window walk-forward validation
  PHASE 6 — Robustness testing (perturbation sweeps)

No synthetic data.  No production-strategy changes.  All conclusions are
evidence-based and written to data/research/diagnosis_report.json.

Usage:
  python scripts/run_scientific_diagnosis.py --data data/research/xauusd_15m_full.json
  python scripts/run_scientific_diagnosis.py --max-signals 0 --skip-expensive
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.research.data_fetch import load_real_history
from app.research.metrics import compute_extended_metrics
from app.research.replay_engine import (
    SignalRecord,
    collect_signals,
    outcomes_metrics,
    replay_variant,
)
from app.research.walkforward import run_walk_forward

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_or_collect_signals(candles, cache_path, max_signals=0):
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            raw = json.load(f)
        from app.core.constants import SignalDirection as SD
        sigs = []
        for d in raw:
            try:
                sigs.append(SignalRecord(
                    timestamp=datetime.fromisoformat(d["timestamp"]),
                    direction=SD(d["direction"]),
                    entry=d["entry"], base_sl=d["base_sl"], base_risk=d["base_risk"],
                    atr=d["atr"], confidence=d["confidence"], market_bias=d["market_bias"],
                    regime=d["regime"], session=d["session"], reasons=d.get("reasons", []),
                    has_bos=d.get("has_bos", False), has_choch=d.get("has_choch", False),
                    has_fvg=d.get("has_fvg", False), has_sweep=d.get("has_sweep", False),
                    has_fib=d.get("has_fib", False),
                ))
            except Exception:
                continue
        print(f"      loaded {len(sigs)} signals from cache")
        return sigs
    print("      collecting signals (single deterministic pass)...")
    t0 = time.time()
    sigs = collect_signals(candles, max_signals=max_signals or None)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump([s.to_dict() for s in sigs], f, indent=1, default=str)
    print(f"      collected {len(sigs)} signals in {time.time()-t0:.0f}s")
    return sigs


def _bucket_ranges(values, nbuckets=4):
    """Simple quantile buckets for a value list."""
    if not values:
        return {}
    s = sorted(values)
    n = len(s)
    edges = [s[int(n * i / nbuckets)] for i in range(nbuckets)] + [s[-1]]
    return edges


def _fmt(m):
    return (f"n={m['trades']} WR={m['win_rate_pct']}% PF={m['profit_factor']} "
            f"Exp={m['expectancy_r']}R MFE={m['avg_mfe_r']}R MAE={m['avg_mae_r']}R")


# ---------------------------------------------------------------------------
# Phase 3 — scientific diagnosis
# ---------------------------------------------------------------------------


def phase3_diagnosis(signals, candles, report):
    print("\n=== PHASE 3: SCIENTIFIC FAILURE DIAGNOSIS ===")
    base = replay_variant(signals, candles, tp_r=1.5)
    m = outcomes_metrics(base)
    print(f"[BASELINE TP=1.5R] {_fmt(m)}")
    report["phase3"] = {"baseline_tp1.5": m}

    # 1. Entry quality via MAE/MFE distribution
    mfes = sorted(o.mfe_r for o in base)
    maes = sorted(o.mae_r for o in base)
    n = len(mfes)
    q = lambda vals, p: vals[min(n - 1, int(n * p))]
    report["phase3"]["entry_quality"] = {
        "median_mfe_r": round(q(mfes, 0.5), 3),
        "p75_mfe_r": round(q(mfes, 0.75), 3),
        "median_mae_r": round(q(maes, 0.5), 3),
        "mfe_ge_1r_pct": round(sum(1 for v in mfes if v >= 1.0) / n * 100, 2) if n else 0.0,
        "mfe_ge_2r_pct": round(sum(1 for v in mfes if v >= 2.0) / n * 100, 2) if n else 0.0,
        "mfe_ge_3r_pct": round(sum(1 for v in mfes if v >= 3.0) / n * 100, 2) if n else 0.0,
        "mae_ge_1r_pct": round(sum(1 for v in maes if v >= 1.0) / n * 100, 2) if n else 0.0,
    }

    # 2. Duplicate / clustering — multiple signals in the same market move
    clustered = 0
    window = timedelta(hours=2)
    for i, s in enumerate(signals):
        if i > 0 and (s.timestamp - signals[i - 1].timestamp) < window:
            clustered += 1
    report["phase3"]["clustering"] = {
        "total_signals": len(signals),
        "signals_within_2h_of_previous": clustered,
        "clustering_pct": round(clustered / len(signals) * 100, 2) if signals else 0.0,
        "avg_signals_per_day": round(len(signals) / max(1, (signals[-1].timestamp - signals[0].timestamp).days), 2),
    }
    print(f"[CLUSTERING] {clustered}/{len(signals)} signals within 2h of previous")

    # 3. Long vs Short
    for d in ["LONG", "SHORT"]:
        sub = [s for s in signals if s.direction.value == d]
        m2 = outcomes_metrics(replay_variant(sub, candles, tp_r=1.5))
        report["phase3"][f"direction_{d.lower()}"] = m2
        print(f"[{d}] {_fmt(m2)}")

    # 4. Regime breakdown
    for reg in sorted({s.regime for s in signals}):
        sub = [s for s in signals if s.regime == reg]
        m3 = outcomes_metrics(replay_variant(sub, candles, tp_r=1.5))
        report["phase3"][f"regime_{reg}"] = m3
        print(f"[REGIME {reg}] {_fmt(m3)}")

    # 5. Session breakdown
    for sess in sorted({s.session for s in signals}):
        sub = [s for s in signals if s.session == sess]
        m4 = outcomes_metrics(replay_variant(sub, candles, tp_r=1.5))
        report["phase3"][f"session_{sess}"] = m4
        print(f"[SESSION {sess}] {_fmt(m4)}")

    # 6. Feature attribution
    features = {
        "has_bos": [s for s in signals if s.has_bos],
        "no_bos": [s for s in signals if not s.has_bos],
        "has_fvg": [s for s in signals if s.has_fvg],
        "no_fvg": [s for s in signals if not s.has_fvg],
        "has_sweep": [s for s in signals if s.has_sweep],
        "no_sweep": [s for s in signals if not s.has_sweep],
        "has_fib": [s for s in signals if s.has_fib],
        "no_fib": [s for s in signals if not s.has_fib],
    }
    for name, sub in features.items():
        m5 = outcomes_metrics(replay_variant(sub, candles, tp_r=1.5))
        report["phase3"][f"feat_{name}"] = m5
        print(f"[FEAT {name}] {_fmt(m5)}")

    # 7. Confluence buckets
    buckets = [(60, 74), (75, 79), (80, 89), (90, 100)]
    for lo, hi in buckets:
        sub = [s for s in signals if lo <= s.confidence <= hi]
        m6 = outcomes_metrics(replay_variant(sub, candles, tp_r=1.5))
        report["phase3"][f"conf_{lo}-{hi}"] = m6
        print(f"[CONF {lo}-{hi}] {_fmt(m6)}")

    # 8. ATR quartiles (volatility conditions)
    atr_vals = [s.atr for s in signals]
    edges = _bucket_ranges(atr_vals, 4)
    labels = ["q1_low_vol", "q2", "q3", "q4_high_vol"]
    for i, lab in enumerate(labels):
        lo_b = edges[i]
        hi_b = edges[i + 1]
        sub = [s for s in signals if lo_b <= s.atr < hi_b or (i == 3 and s.atr >= hi_b)]
        m7 = outcomes_metrics(replay_variant(sub, candles, tp_r=1.5))
        report["phase3"][f"atr_{lab}"] = {**m7, "atr_range": [round(lo_b, 2), round(hi_b, 2)]}
        print(f"[ATR {lab} {round(lo_b,2)}-{round(hi_b,2)}] {_fmt(m7)}")

    return report


# ---------------------------------------------------------------------------
# Phase 4 — signal selection research
# ---------------------------------------------------------------------------


def phase4_selection(signals, candles, report):
    print("\n=== PHASE 4: SIGNAL SELECTION (ALL vs FILTERED) ===")
    all_out = replay_variant(signals, candles, tp_r=1.25)
    m_all = outcomes_metrics(all_out)
    print(f"[ALL SIGNALS TP=1.25R] {_fmt(m_all)}")
    report["phase4"] = {"all_signals_tp1.25": m_all}

    def add_filter(name, sub):
        if not sub:
            report["phase4"][name] = {"trades": 0}
            print(f"[FILTER {name}] no signals")
            return
        mo = outcomes_metrics(replay_variant(sub, candles, tp_r=1.25))
        report["phase4"][name] = mo
        print(f"[FILTER {name}] {_fmt(mo)}")

    # Strongest signal per calendar day (highest confluence)
    by_day = defaultdict(list)
    for s in signals:
        by_day[s.timestamp.date()].append(s)
    strongest_per_day = [max(v, key=lambda s: s.confidence) for v in by_day.values()]
    add_filter("strongest_per_day", strongest_per_day)

    # Strongest per 4H block (UTC)
    by_block = defaultdict(list)
    for s in signals:
        block_hour = (s.timestamp.hour // 4) * 4
        by_block[(s.timestamp.date(), block_hour)].append(s)
    strongest_per_4h = [max(v, key=lambda s: s.confidence) for v in by_block.values()]
    add_filter("strongest_per_4h_block", strongest_per_4h)

    # Strongest per session-day
    by_sess_day = defaultdict(list)
    for s in signals:
        by_sess_day[(s.timestamp.date(), s.session)].append(s)
    strongest_per_session = [max(v, key=lambda s: s.confidence) for v in by_sess_day.values()]
    add_filter("strongest_per_session", strongest_per_session)

    # Minimum 4h (16 candles) between signals
    min_gap = []
    last = None
    gap = timedelta(hours=4)
    for s in sorted(signals, key=lambda x: x.timestamp):
        if last is None or (s.timestamp - last.timestamp) >= gap:
            min_gap.append(s)
            last = s
    add_filter("min_4h_between_signals", min_gap)

    # Highest confluence only (>= 85)
    add_filter("confidence_ge_85", [s for s in signals if s.confidence >= 85])
    add_filter("confidence_ge_80", [s for s in signals if s.confidence >= 80])

    # First signal of the day only
    first_per_day = []
    seen_days = set()
    for s in sorted(signals, key=lambda x: x.timestamp):
        d = s.timestamp.date()
        if d not in seen_days:
            seen_days.add(d)
            first_per_day.append(s)
    add_filter("first_signal_of_day", first_per_day)

    # No same-direction clustering: skip signals with same direction within 4h
    filtered_same_dir = []
    last_long = last_short = None
    for s in sorted(signals, key=lambda x: x.timestamp):
        gap4 = timedelta(hours=4)
        if s.direction.value == "LONG":
            if last_long is None or (s.timestamp - last_long.timestamp) >= gap4:
                filtered_same_dir.append(s)
                last_long = s
        else:
            if last_short is None or (s.timestamp - last_short.timestamp) >= gap4:
                filtered_same_dir.append(s)
                last_short = s
    add_filter("no_same_dir_within_4h", filtered_same_dir)

    # SHORT-only + regime trending + no FVG (combination seen positive in earlier sweeps)
    combo = [s for s in signals
             if s.direction.value == "SHORT"
             and s.regime in ("TRENDING", "RANGING")
             and not s.has_fvg
             and not s.has_bos]
    add_filter("short_trend_ranging_no_fvg_no_bos", combo)

    return report


# ---------------------------------------------------------------------------
# Phase 5 — multi-window walk-forward
# ---------------------------------------------------------------------------


def phase5_walkforward(candles, report):
    print("\n=== PHASE 5: MULTI-WINDOW WALK-FORWARD ===")
    for train_months, val_months, test_months, step in [
        (2, 1, 1, 1),
    ]:
        windows = run_walk_forward(
            candles,
            train_months=train_months,
            val_months=val_months,
            test_months=test_months,
            step_months=step,
            warmup_bars=150,
            initial_balance=10000.0,
            risk_percent=1.0,
        )
        print(f"[WF train={train_months}m val={val_months}m test={test_months}m] "
              f"{len(windows)} windows")

        # Aggregate OOS trades
        oos_trades = []
        for wr in windows:
            if wr.test_result and wr.test_result.trades:
                oos_trades.extend(wr.test_result.trades)
        oos = compute_extended_metrics(oos_trades) if oos_trades else {}
        positive = sum(1 for wr in windows if wr.test_metrics.get("expectancy_r", 0) > 0)
        report["phase5"] = {
            "train_months": train_months,
            "val_months": val_months,
            "test_months": test_months,
            "window_count": len(windows),
            "positive_oos_windows": positive,
            "oos_trades": oos.get("total_trades", 0),
            "oos_win_rate_pct": oos.get("win_rate_pct", 0.0),
            "oos_expectancy_r": oos.get("expectancy_r", 0.0),
            "oos_profit_factor": oos.get("profit_factor", 0.0),
            "oos_max_drawdown_pct": oos.get("max_drawdown_pct", 0.0),
            "windows": [wr.to_dict() for wr in windows],
        }
        print(f"[WF] {len(windows)} windows, OOS trades={oos.get('total_trades', 0)}, "
              f"WR={oos.get('win_rate_pct', 0)}%, Exp={oos.get('expectancy_r', 0)}R, "
              f"positive windows={positive}/{len(windows)}")
    return report


# ---------------------------------------------------------------------------
# Phase 6 — robustness testing
# ---------------------------------------------------------------------------


def phase6_robustness(signals, candles, report):
    print("\n=== PHASE 6: ROBUSTNESS TESTING ===")
    robust = {}

    # Target perturbation
    tp_sweep = {}
    for tp in [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
        m = outcomes_metrics(replay_variant(signals, candles, tp_r=tp))
        tp_sweep[tp] = {"trades": m["trades"], "win_rate_pct": m["win_rate_pct"],
                        "expectancy_r": m["expectancy_r"], "profit_factor": m["profit_factor"],
                        "max_dd_pct": m["max_dd_pct"]}
        print(f"[TP={tp}R] Exp={m['expectancy_r']}R WR={m['win_rate_pct']}% PF={m['profit_factor']}")
    robust["tp_sweep"] = tp_sweep

    # SL perturbation
    sl_sweep = {}
    for sl in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
        m = outcomes_metrics(replay_variant(signals, candles, sl_atr=sl, tp_r=1.25))
        sl_sweep[sl] = {"trades": m["trades"], "win_rate_pct": m["win_rate_pct"],
                        "expectancy_r": m["expectancy_r"], "profit_factor": m["profit_factor"],
                        "max_dd_pct": m["max_dd_pct"]}
        print(f"[SL={sl}ATR TP=1.25R] Exp={m['expectancy_r']}R WR={m['win_rate_pct']}% PF={m['profit_factor']}")
    robust["sl_sweep"] = sl_sweep

    # Random signal removal (drop 10% random signals) — reproducibility seed
    import random
    rng = random.Random(42)
    removal_results = {}
    for drop in [0.0, 0.1, 0.25, 0.5]:
        sub = signals if drop == 0 else rng.sample(signals, int(len(signals) * (1 - drop)))
        m = outcomes_metrics(replay_variant(sub, candles, tp_r=1.25))
        removal_results[drop] = {"trades": m["trades"], "expectancy_r": m["expectancy_r"],
                                 "win_rate_pct": m["win_rate_pct"]}
        print(f"[REMOVE {int(drop*100)}%] n={m['trades']} Exp={m['expectancy_r']}R")
    robust["random_removal"] = removal_results

    report["phase6"] = robust
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/research/xauusd_15m_full.json")
    parser.add_argument("--max-signals", type=int, default=0)
    parser.add_argument("--cache", default="data/research/signals_cache_full.json")
    parser.add_argument("--skip-expensive", action="store_true",
                        help="Skip walk-forward and robustness (fast diagnosis only).")
    args = parser.parse_args()

    print(f"[1/5] Loading REAL data from {args.data} ...")
    candles = load_real_history(args.data)
    print(f"      {len(candles)} candles  {candles[0].timestamp.date()} -> {candles[-1].timestamp.date()}")

    print("[2/5] Collecting deterministic signals (one pass)...")
    signals = _load_or_collect_signals(candles, args.cache, args.max_signals)
    print(f"      {len(signals)} signals  "
          f"{signals[0].timestamp.date() if signals else '-'} -> "
          f"{signals[-1].timestamp.date() if signals else '-'}")

    report = {
        "data_label": "REAL DATA (Binance XAUUSDT)",
        "candles": len(candles),
        "data_start": candles[0].timestamp.isoformat(),
        "data_end": candles[-1].timestamp.isoformat(),
        "signals": len(signals),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    report = phase3_diagnosis(signals, candles, report)
    report = phase4_selection(signals, candles, report)

    if not args.skip_expensive:
        report = phase5_walkforward(candles, report)
        report = phase6_robustness(signals, candles, report)

    os.makedirs("data/research", exist_ok=True)
    path = "data/research/diagnosis_report.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nDiagnosis report written to {path}")


if __name__ == "__main__":
    main()
