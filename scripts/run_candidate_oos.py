"""
Candidate OOS validation on walk-forward TEST windows (strict chronological).

For each walk-forward TEST window (strictly after its TRAIN+VALIDATION data),
replay candidate exit/filter configurations over the signals inside that
window.  All metrics are OOS relative to the window's train data.

This answers: "if we had deployed exit/filter candidate X during window W,
what would its OOS performance have been?"  It does NOT change production.

Usage:
  python scripts/run_candidate_oos.py
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.research.data_fetch import load_real_history
from app.research.replay_engine import (
    SignalRecord,
    collect_signals,
    outcomes_metrics,
    replay_variant,
)

# Walk-forward windows matching the diagnosis Phase-5 run (train=2m,val=1m,test=1m,step=1m)
WINDOWS = [
    ("2025-12-11", "2026-03-11", "2026-03-11", "2026-04-10"),
    ("2026-01-10", "2026-04-10", "2026-04-10", "2026-05-10"),
    ("2026-02-09", "2026-05-10", "2026-05-10", "2026-06-09"),
    ("2026-03-11", "2026-06-09", "2026-06-09", "2026-07-09"),
    ("2026-04-10", "2026-07-09", "2026-07-09", "2026-08-08"),
]

CANDIDATES = {
    "A: production-TP2 (2.41R)": {"tp_r": None, "sl_atr": None, "filter": None},
    "B: TP=1.5R": {"tp_r": 1.5, "sl_atr": None, "filter": None},
    "C: TP=1.5R + conf>=85": {"tp_r": 1.5, "sl_atr": None, "filter": "conf85"},
    "D: TP=1.5R + strongest-4h": {"tp_r": 1.5, "sl_atr": None, "filter": "strong4h"},
    "E: TP=1.5R + min-4h-gap": {"tp_r": 1.5, "sl_atr": None, "filter": "gap4h"},
    "F: TP=1.5R + RANGING regime": {"tp_r": 1.5, "sl_atr": None, "filter": "RANGING"},
    "G: TP=1.5R + LOW-VOL ATR": {"tp_r": 1.5, "sl_atr": None, "filter": "lowvol"},
    "H: TP=1.5R + has_FVG": {"tp_r": 1.5, "sl_atr": None, "filter": "fvg"},
    # Phase E signal-selection experiments
    "I: first-after-bos_break": {"tp_r": 1.5, "sl_atr": None, "filter": "first_bos"},
    "J: one-dir-per-session": {"tp_r": 1.5, "sl_atr": None, "filter": "onedir_sess"},
    "K: cooldown-after-loss": {"tp_r": 1.5, "sl_atr": None, "filter": "cooldown"},
    "L: TP=1.25R": {"tp_r": 1.25, "sl_atr": None, "filter": None},
    # Phase F combined candidates
    "M: RANGING+strongest-4h": {"tp_r": 1.5, "sl_atr": None, "filter": "rang_strong4h"},
    "N: RANGING+min-4h-gap": {"tp_r": 1.5, "sl_atr": None, "filter": "rang_gap4h"},
    "O: TP=1.5R+low-vol+conf85": {"tp_r": 1.5, "sl_atr": None, "filter": "lowvol_conf85"},
}


def _within(sig, start, end):
    return start <= sig.timestamp < end


def _apply_filter(signals, name):
    if name is None:
        return signals
    if name == "conf85":
        return [s for s in signals if s.confidence >= 85]
    if name == "strong4h":
        by_block = defaultdict(list)
        for s in signals:
            block_hour = (s.timestamp.hour // 4) * 4
            by_block[(s.timestamp.date(), block_hour)].append(s)
        return [max(v, key=lambda s: s.confidence) for v in by_block.values()]
    if name == "gap4h":
        out = []
        last = None
        gap = timedelta(hours=4)
        for s in sorted(signals, key=lambda x: x.timestamp):
            if last is None or (s.timestamp - last.timestamp) >= gap:
                out.append(s)
                last = s
        return out
    if name == "RANGING":
        return [s for s in signals if s.regime == "RANGING"]
    if name == "lowvol":
        if not signals:
            return signals
        atrs = sorted(s.atr for s in signals)
        median_atr = atrs[len(atrs) // 2]
        return [s for s in signals if s.atr <= median_atr]
    if name == "fvg":
        return [s for s in signals if s.has_fvg]
    if name == "first_bos":
        # First signal after a BOS structure break, then wait for the next break.
        out = []
        triggered = False
        for s in sorted(signals, key=lambda x: x.timestamp):
            if s.has_bos and not triggered:
                out.append(s)
                triggered = True
            elif not s.has_bos:
                triggered = False
        return out
    if name == "onedir_sess":
        # One signal per direction per session.
        out = []
        seen = set()
        for s in sorted(signals, key=lambda x: x.timestamp):
            key = (s.timestamp.date(), s.session, s.direction.value)
            if key not in seen:
                seen.add(key)
                out.append(s)
        return out
    if name == "cooldown":
        # Skip a new signal in the same direction for 4h after a stop-out
        # (research proxy: no outcome data here, so use gap heuristic).
        out = []
        last_dir_time = {}
        gap = timedelta(hours=4)
        for s in sorted(signals, key=lambda x: x.timestamp):
            d = s.direction.value
            last = last_dir_time.get(d)
            if last is None or (s.timestamp - last) >= gap:
                out.append(s)
            last_dir_time[d] = s.timestamp
        return out
    if name == "rang_strong4h":
        base = _apply_filter(signals, "RANGING")
        return _apply_filter(base, "strong4h")
    if name == "rang_gap4h":
        base = _apply_filter(signals, "RANGING")
        return _apply_filter(base, "gap4h")
    if name == "lowvol_conf85":
        base = _apply_filter(signals, "lowvol")
        return _apply_filter(base, "conf85")
    return signals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/research/xauusd_15m_full.json")
    parser.add_argument("--cache", default="data/research/signals_cache_full.json")
    args = parser.parse_args()

    candles = load_real_history(args.data)
    print(f"Loaded {len(candles)} candles")

    # Load pre-collected signals (fast).
    if os.path.exists(args.cache):
        with open(args.cache, encoding="utf-8") as f:
            raw = json.load(f)
        from app.core.constants import SignalDirection as SD
        signals = []
        for d in raw:
            try:
                signals.append(SignalRecord(
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
    else:
        signals = collect_signals(candles)
    print(f"Loaded {len(signals)} signals from cache")

    report = {"windows": [], "aggregate": {}}

    for tr_s, tr_e, te_s, te_e in WINDOWS:
        test_start = datetime.fromisoformat(te_s).replace(tzinfo=timezone.utc)
        test_end = datetime.fromisoformat(te_e).replace(tzinfo=timezone.utc)
        test_candles = [c for c in candles if test_start <= c.timestamp < test_end]
        test_signals = [s for s in signals if _within(s, test_start, test_end)]
        print(f"\n=== TEST WINDOW {te_s} -> {te_e}  ({len(test_candles)} candles, {len(test_signals)} signals) ===")

        win = {"window": f"{te_s} -> {te_e}", "signals": len(test_signals), "results": {}}
        for name, cfg in CANDIDATES.items():
            filtered = _apply_filter(test_signals, cfg["filter"])
            m = outcomes_metrics(replay_variant(filtered, test_candles, tp_r=cfg["tp_r"] or 1.5))
            win["results"][name] = m
            print(f"  {name:<28} n={m['trades']:>4} WR={m['win_rate_pct']:>5}% PF={m['profit_factor']:>5} "
                  f"Exp={m['expectancy_r']:>6} DD={m['max_dd_pct']:>5}%")
        report["windows"].append(win)

    # Pooled OOS across all 5 test windows.
    print("\n=== POOLED OOS ACROSS ALL 5 TEST WINDOWS ===")
    all_start = datetime.fromisoformat(WINDOWS[0][2]).replace(tzinfo=timezone.utc)
    all_end = datetime.fromisoformat(WINDOWS[-1][3]).replace(tzinfo=timezone.utc)
    pooled_candles = [c for c in candles if all_start <= c.timestamp < all_end]
    pooled_signals = [s for s in signals if all_start <= s.timestamp < all_end]

    agg = {}
    for name, cfg in CANDIDATES.items():
        filtered = _apply_filter(pooled_signals, cfg["filter"])
        m = outcomes_metrics(replay_variant(filtered, pooled_candles, tp_r=cfg["tp_r"] or 1.5))
        agg[name] = m
        print(f"  {name:<28} n={m['trades']:>4} WR={m['win_rate_pct']:>5}% PF={m['profit_factor']:>5} "
              f"Exp={m['expectancy_r']:>6} DD={m['max_dd_pct']:>5}%")
    report["aggregate"] = agg

    os.makedirs("data/research", exist_ok=True)
    path = "data/research/candidate_oos_windows.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nCandidate OOS report written to {path}")


if __name__ == "__main__":
    main()
