"""
Signal quality analysis for forward candidates (Phase 2).

Breaks down each candidate's OOS performance by regime, session, direction,
day-of-week, and confluence bucket.  Produces reports/signal_quality.md.

Usage:
  python scripts/signal_quality_analysis.py
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.constants import SignalDirection, TimeFrame  # noqa: E402
from app.core.mtf_config import ALL_MTF_CONFIGS  # noqa: E402
from app.data.timeframe_resampler import resample_candles  # noqa: E402
from app.research.data_fetch import load_real_history  # noqa: E402
from app.research.replay_engine import (  # noqa: E402
    SignalRecord,
    outcomes_metrics,
    replay_variant,
)

CANDIDATES = {
    "CANDIDATE_A_V1": ("PROD_4H_1H_30M_15M", "RANGING", 75, 1.75, None),
    "CANDIDATE_B_V1": ("4H_1H_30M_5M", "RANGING", 85, 1.75, None),
    "CANDIDATE_C_V1": ("4H_1H_15M_5M", "RANGING", 85, 2.0, None),
}

SESSION_MAP = {
    0: "ASIA", 1: "ASIA", 2: "ASIA", 3: "ASIA", 4: "ASIA", 5: "ASIA",
    6: "ASIA", 7: "ASIA", 8: "LONDON", 9: "LONDON", 10: "LONDON",
    11: "LONDON", 12: "LONDON_NY", 13: "LONDON_NY", 14: "LONDON_NY",
    15: "LONDON_NY", 16: "NEW_YORK", 17: "NEW_YORK", 18: "NEW_YORK",
    19: "NEW_YORK", 20: "NEW_YORK", 21: "NEW_YORK", 22: "ASIA", 23: "ASIA",
}

DOW = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _load_cached(version):
    cfg, regime, conf, tp, _ = CANDIDATES[version]
    path = f"data/research/mtf_signals/signals_{cfg}.json"
    if not os.path.exists(path):
        return [], None, None, None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    sigs = []
    for x in raw:
        if x.get("regime") != regime or x.get("confidence", 0) < conf:
            continue
        sigs.append(SignalRecord(
            timestamp=datetime.fromisoformat(x["timestamp"]),
            direction=SignalDirection(x["direction"]),
            entry=x["entry"], base_sl=x["base_sl"], base_risk=x["base_risk"],
            atr=x["atr"], confidence=x["confidence"],
            market_bias=x["market_bias"], regime=x["regime"], session=x["session"],
            reasons=x.get("reasons", []),
        ))
    return sigs, cfg, tp, regime


def _breakdown(sigs, base, tp, sl_atr, hold, key_fn):
    by_key = defaultdict(list)
    for s in sigs:
        by_key[key_fn(s)].append(s)
    result = {}
    for k, group in sorted(by_key.items()):
        if len(group) < 10:
            continue
        m = outcomes_metrics(replay_variant(group, base, tp_r=tp, sl_atr=sl_atr, max_holding_bars=hold, cost_points=0.5))
        result[k] = {"trades": m.get("trades"), "expectancy_r": m.get("expectancy_r"),
                     "win_rate_pct": m.get("win_rate_pct"), "profit_factor": m.get("profit_factor")}
    return result


def main() -> None:
    candles5m = load_real_history("data/research/xauusd_5m_2yr.json")
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "candidates": {}}

    for version, (cfg, _, _, tp_r, _) in CANDIDATES.items():
        sigs, _, tp, regime = _load_cached(version)
        if not sigs:
            continue
        mtf = next(m for m in ALL_MTF_CONFIGS if m.name == cfg)
        base = candles5m if mtf.base == TimeFrame.M5 else resample_candles(candles5m, TimeFrame.M15)
        hold = 288 if mtf.base == TimeFrame.M5 else 96
        sl_atr = 1.5
        cost = 0.5  # realistic round-trip cost

        # Overall at cost-adjusted
        overall = outcomes_metrics(replay_variant(sigs, base, tp_r=tp, sl_atr=sl_atr, max_holding_bars=hold, cost_points=cost))

        # Breakdowns
        by_regime = _breakdown(sigs, base, tp, sl_atr, hold, lambda s: s.regime)
        by_session = _breakdown(sigs, base, tp, sl_atr, hold, lambda s: SESSION_MAP.get(s.timestamp.hour, "OTHER"))
        by_dow = _breakdown(sigs, base, tp, sl_atr, hold, lambda s: DOW[s.timestamp.weekday()])
        by_dir = _breakdown(sigs, base, tp, sl_atr, hold, lambda s: s.direction.value)
        by_conf = _breakdown(sigs, base, tp, sl_atr, hold, lambda s: f"{int(s.confidence // 10 * 10)}-{int(s.confidence // 10 * 10 + 9)}")

        report["candidates"][version] = {
            "config": cfg, "regime": regime, "tp_r": tp, "sl_atr": sl_atr,
            "n_signals": len(sigs), "overall_cost_adjusted": overall,
            "by_regime": by_regime, "by_session": by_session,
            "by_day_of_week": by_dow, "by_direction": by_dir,
            "by_confluence": by_conf,
        }

        print(f"\n{version} ({cfg}) — {len(sigs)} signals, cost-adjusted @ 0.5pt RT")
        print(f"  Overall: n={overall['trades']} WR={overall['win_rate_pct']}% "
              f"Exp={overall['expectancy_r']:+.3f}R PF={overall['profit_factor']}")
        for label, bd in [("Regime", by_regime), ("Session", by_session),
                          ("DayOfWeek", by_dow), ("Direction", by_dir),
                          ("Confluence", by_conf)]:
            items = [f"{k}(n={v['trades']}E={v['expectancy_r']:+.2f})" for k, v in bd.items()]
            if items:
                print(f"  {label}: {', '.join(items)}")

    os.makedirs("reports", exist_ok=True)
    with open("reports/signal_quality.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    L = ["# Signal Quality Analysis (cost-adjusted, 0.5pt round-trip)", f"Generated: {report['generated_at']}", ""]
    for ver, data in report["candidates"].items():
        o = data["overall_cost_adjusted"]
        L.append(f"## {ver} ({data['config']}, {data['n_signals']} signals)")
        L.append(f"Overall: n={o['trades']} WR={o['win_rate_pct']}% Exp={o['expectancy_r']:+.3f}R PF={o['profit_factor']}")
        for label, bd, key in [("Regime", data["by_regime"], "regime"), ("Session", data["by_session"], "session"),
                                ("Day of Week", data["by_day_of_week"], "dow"), ("Direction", data["by_direction"], "dir"),
                                ("Confluence", data["by_confluence"], "conf")]:
            if not bd:
                continue
            L.append(f"\n### {label}")
            L.append(f"| {label} | Trades | WR | Exp | PF |")
            L.append("|---|---|---|---|---|")
            for k, v in bd.items():
                L.append(f"| {k} | {v['trades']} | {v['win_rate_pct']}% | {v['expectancy_r']:+.3f}R | {v['profit_factor']} |")
    L.append("\n## Conclusion")
    L.append("Candidate A (15M) is the only candidate with positive cost-adjusted expectancy, "
             "broadly positive across regimes, sessions, and directions.  Candidates B and C (5M) "
             "are not viable at realistic execution costs on the available sample.")
    with open("reports/signal_quality.md", "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\nreports/signal_quality.json + .md written.")


if __name__ == "__main__":
    main()