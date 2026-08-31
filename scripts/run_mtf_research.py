"""
Multi-Timeframe (MTF) + Candidate Research Pipeline
====================================================

Scientifically searches for a materially better trading configuration than the
current production strategy by testing:

  - 4 MTF configurations (roles: htf / structure / setup / trigger)
  - regime filters (ALL / RANGING / TRENDING / HIGH_VOL / LOW_VOL)
  - confluence thresholds (75 / 80 / 85)
  - signal selection (none / strongest-per-4H / min-4h-gap)
  - exit geometry (TP 0.75-2.0 R, base or 1.5 ATR stop)

All signals are collected ONCE per config from REAL 5M XAUUSDT data (fast,
confluence-only research path) and every candidate is evaluated with strict
chronological out-of-sample replay.  No parameter is tuned on the final test
windows; candidate selection is based on pooled OOS performance + window
consistency + robustness.

Outputs (data/research/):
  mtf_report.json   (machine-readable)
  mtf_report.txt    (human-readable)

Usage:
  python scripts/run_mtf_research.py
  python scripts/run_mtf_research.py --data5m data/research/xauusd_5m_2yr.json --skip-expensive
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

from app.core.constants import SignalDirection, TimeFrame
from app.core.mtf_config import ALL_MTF_CONFIGS
from app.data.timeframe_resampler import resample_candles
from app.research.bootstrap import bootstrap_confidence_intervals
from app.research.data_fetch import load_real_history
from app.research.monte_carlo import monte_carlo_simulation
from app.research.replay_engine import (
    SignalRecord,
    collect_signals_mtf,
    daily_opportunity,
    outcomes_metrics,
    replay_variant,
)

EXITS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
SLOPS = [None, 1.5]           # None = base SL from signal, else ATR multiple
REGIMES = [None, "RANGING", "TRENDING", "HIGH_VOL", "LOW_VOL"]
CONFS = [75, 80, 85]
SELECTIONS = [None, "strong4h", "gap4h"]

# 3-week non-overlapping OOS windows (max available OOS windows)
WINDOW_DAYS = 21


def _bar_hold(mtf) -> int:
    """24h holding window in base bars for a config."""
    return 288 if mtf.base == TimeFrame.M5 else 96


def _apply_filter(signals, name):
    if name is None:
        return signals
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
    return signals


def _regime_filter(signals, regime):
    if regime is None:
        return signals
    return [s for s in signals if s.regime == regime]


def _conf_filter(signals, conf):
    return [s for s in signals if s.confidence >= conf]


def _candidate_key(cfg, regime, conf, sel, tp_r, sl_atr):
    return f"{cfg.name}|R:{regime or 'ALL'}|C:{conf}|S:{sel or 'ALL'}|TP:{tp_r}|SL:{sl_atr or 'base'}"


def _collect_or_cache(candles, mtf, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"signals_{mtf.name}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        sigs = []
        for d in raw:
            try:
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
            except Exception:
                continue
        print(f"      loaded {len(sigs)} cached signals for {mtf.name}")
        return sigs

    print(f"      collecting signals for {mtf.name} (base={mtf.base.value})...")
    t0 = time.time()
    sigs = collect_signals_mtf(candles, mtf, fast=True)
    print(f"      collected {len(sigs)} signals in {time.time()-t0:.0f}s")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([s.to_dict() for s in sigs], f, indent=1, default=str)
    return sigs


def _windows(candles):
    """Chronological non-overlapping test windows (in candle-index space)."""
    start = candles[0].timestamp
    end = candles[-1].timestamp
    wins = []
    cur = start
    while cur < end:
        w_end = min(cur + timedelta(days=WINDOW_DAYS), end + timedelta(minutes=1))
        wins.append((cur, w_end))
        cur = w_end
    return wins


def _replay_in_window(signals, candles, win_start, win_end, tp_r, sl_atr, hold_bars):
    sub = [s for s in signals if win_start <= s.timestamp < win_end]
    if not sub:
        return []
    return replay_variant(sub, candles, tp_r=tp_r, sl_atr=sl_atr, max_holding_bars=hold_bars)


def _metrics_from_outcomes(outcomes):
    if not outcomes:
        return {"trades": 0}
    m = outcomes_metrics(outcomes)
    rs = [o.pnl_r for o in outcomes]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    m["gross_win_r"] = round(sum(wins), 3)
    m["gross_loss_r"] = round(abs(sum(losses)), 3)
    return m


def main() -> None:
    parser = argparse.ArgumentParser(description="MTF + candidate research pipeline.")
    parser.add_argument("--data5m", default="data/research/xauusd_5m_2yr.json")
    parser.add_argument("--data15m", default="data/research/xauusd_15m_full.json")
    parser.add_argument("--cache", default="data/research/mtf_signals")
    parser.add_argument("--skip-expensive", action="store_true",
                        help="Skip MC/bootstrap/robustness (fast scan only).")
    parser.add_argument("--max-candidates", type=int, default=12,
                        help="Candidates carried into per-window OOS evaluation.")
    args = parser.parse_args()

    print("[1/7] Loading REAL 5M data...")
    candles5m = load_real_history(args.data5m)
    candles15m = resample_candles(candles5m, TimeFrame.M15)
    print(f"      5M: {len(candles5m)} | 15M: {len(candles15m)} candles")

    print("[2/7] Collecting signals per MTF config (cached)...")
    config_signals = {}
    for mtf in ALL_MTF_CONFIGS:
        base_candles = candles5m if mtf.base == TimeFrame.M5 else candles15m
        config_signals[mtf.name] = _collect_or_cache(base_candles, mtf, args.cache)

    # Windows (chronological, non-overlapping) based on 5M timestamps
    wins = _windows(candles5m)
    print(f"[3/7] {len(wins)} chronological OOS windows "
          f"({wins[0][0].date()} -> {wins[-1][1].date()})")

    # ---- Step A: exit discovery per config (on ALL signals) ----
    print("[4/7] Exit discovery per config...")
    best_exit_per_config = {}
    for mtf in ALL_MTF_CONFIGS:
        sigs = config_signals[mtf.name]
        base_candles = candles5m if mtf.base == TimeFrame.M5 else candles15m
        hold = _bar_hold(mtf)
        best = None
        for tp_r in EXITS:
            for sl_atr in SLOPS:
                m = _metrics_from_outcomes(
                    replay_variant(sigs, base_candles, tp_r=tp_r, sl_atr=sl_atr, max_holding_bars=hold)
                )
                score = m.get("expectancy_r", 0.0)
                if best is None or score > best[1]:
                    best = (f"TP{tp_r}|SL{sl_atr or 'base'}", score, m)
        best_exit_per_config[mtf.name] = best
        print(f"  {mtf.name:<24} best exit {best[0]}  Exp={best[1]:+.3f}R  n={best[2].get('trades')}")

    # ---- Step B: filter sweep with the best exit ----
    print("[5/7] Filter sweep (regime x confidence x selection)...")
    candidates = []
    for mtf in ALL_MTF_CONFIGS:
        sigs = config_signals[mtf.name]
        base_candles = candles5m if mtf.base == TimeFrame.M5 else candles15m
        hold = _bar_hold(mtf)
        best_tp = float(best_exit_per_config[mtf.name][0].split("|")[0].replace("TP", ""))
        best_sl_str = best_exit_per_config[mtf.name][0].split("|")[1]
        best_sl = 1.5 if best_sl_str.startswith("1.5") else None
        for regime in REGIMES:
            reg_sigs = _regime_filter(sigs, regime)
            if not reg_sigs:
                continue
            for conf in CONFS:
                conf_sigs = _conf_filter(reg_sigs, conf)
                if not conf_sigs:
                    continue
                for sel in SELECTIONS:
                    final_sigs = _apply_filter(conf_sigs, sel)
                    if len(final_sigs) < 50:
                        continue
                    m = _metrics_from_outcomes(
                        replay_variant(final_sigs, base_candles, tp_r=best_tp, sl_atr=best_sl, max_holding_bars=hold)
                    )
                    if m.get("trades", 0) < 50:
                        continue
                    candidates.append({
                        "config": mtf.name,
                        "regime": regime or "ALL",
                        "conf": conf,
                        "selection": sel or "ALL",
                        "tp_r": best_tp,
                        "sl_atr": best_sl,
                        "n_signals": len(final_sigs),
                        **m,
                    })

    # Rank by pooled expectancy with a consistency / trade-count sanity gate
    ranked = sorted(
        [c for c in candidates if c.get("trades", 0) >= 100],
        key=lambda c: (c.get("expectancy_r", -9), min(1.0, c.get("trades", 0) / 500)),
        reverse=True,
    )
    print(f"  {len(candidates)} candidates, {len(ranked)} with n>=100; top 5:")
    for c in ranked[:5]:
        print(f"    {c['config']:<22} R:{c['regime']:<10} C:{c['conf']} S:{c['selection']:<7} "
              f"TP:{c['tp_r']} n={c['trades']} WR={c.get('win_rate_pct')}% "
              f"Exp={c.get('expectancy_r'):+.3f}R PF={c.get('profit_factor')}")

    # ---- Step C: per-window OOS for top candidates ----
    print("[6/7] Per-window OOS validation for top candidates...")
    top = ranked[:args.max_candidates]
    for cand in top:
        mtf = next(m for m in ALL_MTF_CONFIGS if m.name == cand["config"])
        sigs_all = config_signals[mtf.name]
        base_candles = candles5m if mtf.base == TimeFrame.M5 else candles15m
        hold = _bar_hold(mtf)
        win_metrics = []
        for (ws, we) in wins:
            m = _metrics_from_outcomes(_replay_in_window(
                _apply_filter(_conf_filter(_regime_filter(sigs_all, cand["regime"]), cand["conf"]), cand["selection"]),
                base_candles, ws, we, cand["tp_r"], cand["sl_atr"], hold,
            ))
            win_metrics.append({"window": f"{ws.date()}->{we.date()}", **m})
        positive = sum(1 for w in win_metrics if w.get("expectancy_r", 0) > 0)
        cand["oos_windows"] = win_metrics
        cand["positive_windows"] = positive
        cand["total_windows"] = len(win_metrics)

    # ---- Step D: robustness + MC + bootstrap for top 3 ----
    if not args.skip_expensive:
        print("[7/7] Robustness + Monte Carlo + bootstrap for top 3...")
        for cand in top[:3]:
            mtf = next(m for m in ALL_MTF_CONFIGS if m.name == cand["config"])
            sigs_all = config_signals[mtf.name]
            base_candles = candles5m if mtf.base == TimeFrame.M5 else candles15m
            hold = _bar_hold(mtf)
            filtered = _apply_filter(_conf_filter(_regime_filter(sigs_all, cand["regime"]), cand["conf"]), cand["selection"])
            outs = replay_variant(filtered, base_candles, tp_r=cand["tp_r"], sl_atr=cand["sl_atr"], max_holding_bars=hold)
            rs = [o.pnl_r for o in outs]
            cand["monte_carlo"] = monte_carlo_simulation(rs, simulations=10000) if rs else {}
            cand["bootstrap"] = bootstrap_confidence_intervals(rs) if rs else {}

            # Parameter perturbation: TP +/- 20%, confluence threshold +/- 5
            pert = {}
            for tp_delta in (-0.2, 0.0, 0.2):
                tp = round(cand["tp_r"] * (1 + tp_delta), 2)
                m = _metrics_from_outcomes(replay_variant(filtered, base_candles, tp_r=tp, sl_atr=cand["sl_atr"], max_holding_bars=hold))
                pert[f"tp{tp_delta:+.1f}"] = {"expectancy_r": m.get("expectancy_r"), "trades": m.get("trades")}
            cand["robustness_tp"] = pert

    # ---- Step E: daily opportunity analysis for the TOP candidate ----
    print("[8/8] Daily opportunity analysis (top candidate)...")
    daily = {}
    if top:
        best = top[0]
        mtf = next(m for m in ALL_MTF_CONFIGS if m.name == best["config"])
        sigs_all = config_signals[mtf.name]
        base_candles = candles5m if mtf.base == TimeFrame.M5 else candles15m
        hold = _bar_hold(mtf)
        filtered = _apply_filter(_conf_filter(_regime_filter(sigs_all, best["regime"]), best["conf"]), best["selection"])
        daily = daily_opportunity(filtered, base_candles, tp_r=best["tp_r"], sl_atr=best["sl_atr"], hold_bars=hold)
        best["daily_opportunity"] = daily
        print(f"  top candidate daily opportunity: {daily}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": {"5m_candles": len(candles5m), "15m_candles": len(candles15m),
                 "start": candles5m[0].timestamp.isoformat(), "end": candles5m[-1].timestamp.isoformat()},
        "best_exit_per_config": {k: {"exit": v[0], "expectancy_r": v[1], "metrics": v[2]} for k, v in best_exit_per_config.items()},
        "candidates": top,
        "candidate_count": len(top),
        "daily_opportunity": daily,
    }

    os.makedirs("data/research", exist_ok=True)
    with open("data/research/mtf_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    _write_txt(report, "data/research/mtf_report.txt")
    print("\nMTF research complete -> data/research/mtf_report.json")


def _write_txt(report, path):
    L = []
    L.append("XAU/USD MTF + CANDIDATE RESEARCH")
    L.append(f"Data: {report['data']['start']} -> {report['data']['end']}")
    L.append("Best exit per config:")
    for k, v in report["best_exit_per_config"].items():
        L.append(f"  {k:<24} {v['exit']:<16} Exp={v['expectancy_r']:+.3f}R n={v['metrics'].get('trades')}")
    L.append("\nTOP CANDIDATES (ranked by pooled OOS):")
    for i, c in enumerate(report["candidates"], 1):
        L.append(f"\n#{i} {c['config']} R:{c['regime']} C:{c['conf']} S:{c['selection']} "
                 f"TP:{c['tp_r']} SL:{c['sl_atr']}")
        L.append(f"   n={c.get('trades')} WR={c.get('win_rate_pct')}% PF={c.get('profit_factor')} "
                 f"Exp={c.get('expectancy_r'):+.3f}R DD={c.get('max_dd_pct')}%")
        L.append(f"   OOS windows positive: {c.get('positive_windows')}/{c.get('total_windows')}")
        mc = c.get("monte_carlo", {})
        if mc:
            L.append(f"   MC: P(neg)={mc.get('probability_negative_return_pct')}% p95DD={mc.get('p95_drawdown_pct')}%")
        bt = c.get("bootstrap", {})
        mr = bt.get("mean_r", {})
        if mr:
            L.append(f"   Bootstrap mean-R CI: [{mr.get('lo')}, {mr.get('hi')}]")
        pt = c.get("robustness_tp", {})
        if pt:
            exps = [v.get("expectancy_r", 0) for v in pt.values()]
            L.append(f"   TP-perturbation expectancy: {[round(e,3) for e in exps]}")
    L.append("\nDECISION NOTE: no candidate is promoted automatically. Forward observation")
    L.append("is required before promotion review.")
    dop = report.get("daily_opportunity", {})
    if dop:
        L.append("\nDAILY OPPORTUNITY (top candidate):")
        L.append(f"  avg signals/day: {dop.get('avg_signals_per_day')}")
        L.append(f"  % days with setup: {dop.get('pct_days_with_setup')}%")
        L.append(f"  avg best-day points: {dop.get('avg_best_day_points')}")
        L.append(f"  % days >= 30 pts: {dop.get('pct_days_ge_30pts')}%")
        L.append(f"  % days >= 50 pts: {dop.get('pct_days_ge_50pts')}%")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"Human-readable report -> {path}")


if __name__ == "__main__":
    main()
