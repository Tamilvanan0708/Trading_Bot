"""
Deterministic strategy redesign research.

Collects every tradable signal once (REAL Binance XAUUSDT data), then:
  1. Exit-design sweep   (TP = 0.75R .. 2.5R)
  2. SL-design sweep     (ATR-based SL)
  3. HIGH_VOL hypothesis (regime filter)
  4. Feature attribution (BOS / FVG / sweep / direction / session)
  5. Candidate construction + final OOS validation

No synthetic data.  No test-set parameter tuning — candidates are selected on
the research window and evaluated only on the locked OOS window.

Usage:
  python scripts/run_strategy_redesign.py
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from app.research.data_fetch import load_real_history
from app.research.replay_engine import (
    collect_signals,
    outcomes_metrics,
    replay_variant,
)

RESEARCH_START = datetime(2026, 5, 21, tzinfo=timezone.utc)
RESEARCH_END = datetime(2026, 7, 20, tzinfo=timezone.utc)
OOS_START = datetime(2026, 7, 20, tzinfo=timezone.utc)
OOS_END = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _within(sig, start, end):
    return start <= sig.timestamp < end


def _sweep_table(signals, candles, label, tp_rs, sl_atr=None):
    """Run an exit/SL sweep and return a results table."""
    rows = []
    for tp_r in tp_rs:
        outcomes = replay_variant(signals, candles, sl_atr=sl_atr, tp_r=tp_r)
        m = outcomes_metrics(outcomes)
        rows.append({"label": label, "sl_atr": sl_atr, "tp_r": tp_r, **m})
    return rows


def _load_signals(candles, cache_path, max_signals=0):
    """Load signals from cache, or collect once and save."""
    import json as _json
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            raw = _json.load(f)
        from app.core.constants import SignalDirection
        from app.research.replay_engine import SignalRecord
        sigs = []
        for d in raw:
            sigs.append(SignalRecord(
                timestamp=datetime.fromisoformat(d["timestamp"]),
                direction=SignalDirection(d["direction"]),
                entry=d["entry"], base_sl=d["base_sl"], base_risk=d["base_risk"],
                atr=d["atr"], confidence=d["confidence"], market_bias=d["market_bias"],
                regime=d["regime"], session=d["session"], reasons=d.get("reasons", []),
                has_bos=d.get("has_bos", False), has_choch=d.get("has_choch", False),
                has_fvg=d.get("has_fvg", False), has_sweep=d.get("has_sweep", False),
                has_fib=d.get("has_fib", False),
            ))
        print(f"      loaded {len(sigs)} signals from cache")
        return sigs
    print("      collecting signals (first run)...")
    sigs = collect_signals(candles, max_signals=max_signals or None)
    with open(cache_path, "w", encoding="utf-8") as f:
        _json.dump([s.to_dict() for s in sigs], f, indent=1, default=str)
    print(f"      collected {len(sigs)} signals, cached to {cache_path}")
    return sigs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/research/xauusd_15m_real.json")
    parser.add_argument("--max-signals", type=int, default=0, help="Cap signals for fast dev runs.")
    args = parser.parse_args()

    print("[1/6] Loading REAL data...")
    candles = load_real_history(args.data)
    print(f"      {len(candles)} candles  {candles[0].timestamp.date()} -> {candles[-1].timestamp.date()}")

    # Slice to the research window + warmup so signal collection is tractable.
    warmup_days = 10
    warmup_cutoff = RESEARCH_START - timedelta(days=warmup_days)
    candles = [c for c in candles if c.timestamp >= warmup_cutoff]
    print(f"      sliced to {len(candles)} candles ({candles[0].timestamp.date()} -> {candles[-1].timestamp.date()})")

    print("[2/6] Collecting deterministic signals (one pass)...")
    t0 = time.time()
    cache = "data/research/signals_cache.json"
    all_signals = _load_signals(candles, cache, args.max_signals)
    print(f"      signal load/collect took {time.time()-t0:.0f}s")

    research_signals = [s for s in all_signals if _within(s, RESEARCH_START, RESEARCH_END)]
    oos_signals = [s for s in all_signals if _within(s, OOS_START, OOS_END)]
    print(f"      research window: {len(research_signals)} signals, OOS: {len(oos_signals)} signals")
    print(f"      research signal dates: {research_signals[0].timestamp.date() if research_signals else '-'} -> {research_signals[-1].timestamp.date() if research_signals else '-'}")
    print(f"      OOS signal dates: {oos_signals[0].timestamp.date() if oos_signals else '-'} -> {oos_signals[-1].timestamp.date() if oos_signals else '-'}")

    report = {"data_label": "REAL DATA (Binance XAUUSDT)", "candles": len(candles)}

    # ---------------------------------------------------------------
    print("\n[3/6] EXIT-DESIGN SWEEP (research window, base SL)")
    exit_sweep = _sweep_table(research_signals, candles, "base_sl", [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5])
    for r in exit_sweep:
        print(f"      TP={r['tp_r']:>4}R  trades={r['trades']:>4}  WR={r['win_rate_pct']:>5}%  "
              f"PF={r['profit_factor']:>5}  Exp={r['expectancy_r']:>6}  MFE={r['avg_mfe_r']:>5}  "
              f"DD={r['max_dd_pct']:>5}%")
    report["exit_sweep"] = exit_sweep

    # ---------------------------------------------------------------
    print("\n[4/6] SL-DESIGN SWEEP (ATR-based, TP=1.25R/1.5R/2.0R)")
    sl_sweep = []
    for sl_atr in [0.75, 1.0, 1.25, 1.5]:
        for tp_r in [1.25, 1.5, 2.0]:
            outcomes = replay_variant(research_signals, candles, sl_atr=sl_atr, tp_r=tp_r)
            m = outcomes_metrics(outcomes)
            sl_sweep.append({"sl_atr": sl_atr, "tp_r": tp_r, **m})
            print(f"      SL={sl_atr}ATR  TP={tp_r}R  trades={m['trades']:>4}  WR={m['win_rate_pct']:>5}%  "
                  f"PF={m['profit_factor']:>5}  Exp={m['expectancy_r']:>6}  MFE={m['avg_mfe_r']:>5}")
    report["sl_sweep"] = sl_sweep

    # ---------------------------------------------------------------
    print("\n[5/6] HIGH_VOLATILITY HYPOTHESIS")
    hv = [s for s in research_signals if s.regime == "HIGH_VOLATILITY"]
    non_hv = [s for s in research_signals if s.regime != "HIGH_VOLATILITY"]
    print(f"      HIGH_VOL signals: {len(hv)}  non-HIGH_VOL: {len(non_hv)}")
    for tp_r in [1.25, 1.5, 2.0]:
        m_hv = outcomes_metrics(replay_variant(hv, candles, tp_r=tp_r))
        m_nv = outcomes_metrics(replay_variant(non_hv, candles, tp_r=tp_r))
        print(f"      TP={tp_r}R  HIGH_VOL: n={m_hv['trades']} WR={m_hv['win_rate_pct']}% Exp={m_hv['expectancy_r']} "
              f"| NON_HV: n={m_nv['trades']} WR={m_nv['win_rate_pct']}% Exp={m_nv['expectancy_r']}")
    report["high_vol"] = {
        "high_vol_signals": len(hv),
        "non_high_vol_signals": len(non_hv),
    }

    # ---------------------------------------------------------------
    print("\n[5b/6] FEATURE ATTRIBUTION (base SL, TP=1.25R)")
    attribution = {}
    def _feat_metrics(name, subset):
        m = outcomes_metrics(replay_variant(subset, candles, tp_r=1.25))
        attribution[name] = m
        print(f"      {name:<22} n={m['trades']:>4} WR={m['win_rate_pct']:>5}% PF={m['profit_factor']:>5} "
              f"Exp={m['expectancy_r']:>6} MFE={m['avg_mfe_r']:>5}")
    _feat_metrics("ALL", research_signals)
    _feat_metrics("LONG", [s for s in research_signals if s.direction.value == "LONG"])
    _feat_metrics("SHORT", [s for s in research_signals if s.direction.value == "SHORT"])
    _feat_metrics("has_BOS", [s for s in research_signals if s.has_bos])
    _feat_metrics("no_BOS", [s for s in research_signals if not s.has_bos])
    _feat_metrics("has_FVG", [s for s in research_signals if s.has_fvg])
    _feat_metrics("no_FVG", [s for s in research_signals if not s.has_fvg])
    _feat_metrics("has_SWEEP", [s for s in research_signals if s.has_sweep])
    _feat_metrics("no_SWEEP", [s for s in research_signals if not s.has_sweep])
    _feat_metrics("TRENDING", [s for s in research_signals if s.regime == "TRENDING"])
    _feat_metrics("RANGING", [s for s in research_signals if s.regime == "RANGING"])
    _feat_metrics("LOW_VOL", [s for s in research_signals if s.regime == "LOW_VOLATILITY"])
    _feat_metrics("NEW_YORK", [s for s in research_signals if s.session == "NEW_YORK"])
    _feat_metrics("ASIA", [s for s in research_signals if s.session == "ASIA"])
    _feat_metrics("LONDON", [s for s in research_signals if s.session == "LONDON"])
    report["feature_attribution"] = attribution

    # ---------------------------------------------------------------
    print("\n[6/6] CANDIDATE OOS VALIDATION (locked parameters)")
    candidates = {
        "A: base SL + TP=1.25R": {"sl_atr": None, "tp_r": 1.25, "filter": None},
        "B: SL=1.0ATR + TP=1.25R": {"sl_atr": 1.0, "tp_r": 1.25, "filter": None},
        "C: HIGH_VOL only + base SL + TP=1.5R": {"sl_atr": None, "tp_r": 1.5, "filter": "HIGH_VOLATILITY"},
    }
    oos_results = {}
    for name, cfg in candidates.items():
        seg = oos_signals
        if cfg["filter"]:
            seg = [s for s in seg if s.regime == cfg["filter"]]
        outcomes = replay_variant(seg, candles, sl_atr=cfg["sl_atr"], tp_r=cfg["tp_r"])
        m = outcomes_metrics(outcomes)
        oos_results[name] = m
        print(f"      {name}: n={m['trades']} WR={m['win_rate_pct']}% PF={m['profit_factor']} "
              f"Exp={m['expectancy_r']} MFE={m['avg_mfe_r']} DD={m['max_dd_pct']}%")
    report["oos_candidates"] = oos_results

    os.makedirs("data/research", exist_ok=True)
    path = "data/research/redesign_report.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nRedesign research written to {path}")


if __name__ == "__main__":
    main()