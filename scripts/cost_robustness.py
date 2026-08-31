"""
Execution-cost robustness analysis for forward candidates (Phase 5).

Models round-trip execution cost (spread + slippage + commission) in price
points and measures how each candidate's pooled OOS expectancy degrades.

Baseline cost: XAUUSDT realistic round-trip ~0.5 points (spread ~0.3 +
slippage ~0.2 + commission).  Swept at 0x / 1x / 1.5x / 2x / 3x.

A candidate whose edge is destroyed by a modest cost increase (1.5-2x) is
fragile and must not be promoted.

Usage:
  python scripts/cost_robustness.py
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.constants import SignalDirection  # noqa: E402
from app.research.data_fetch import load_real_history  # noqa: E402
from app.research.replay_engine import (  # noqa: E402
    SignalRecord,
    outcomes_metrics,
    replay_variant,
)
from app.data.timeframe_resampler import resample_candles  # noqa: E402
from app.core.constants import TimeFrame  # noqa: E402
from app.core.mtf_config import ALL_MTF_CONFIGS  # noqa: E402

BASELINE_COST = 0.5  # round-trip price points (spread + slippage + commission)
COST_MULTIPLES = [0.0, 1.0, 1.5, 2.0, 3.0]

CANDIDATES = {
    "CANDIDATE_A_V1": ("PROD_4H_1H_30M_15M", "RANGING", 75, 1.75, None),
    "CANDIDATE_B_V1": ("4H_1H_30M_5M", "RANGING", 85, 1.75, None),
    "CANDIDATE_C_V1": ("4H_1H_15M_5M", "RANGING", 85, 2.0, None),
}


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


def main() -> None:
    candles5m = load_real_history("data/research/xauusd_5m_2yr.json")
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "baseline_cost_points": BASELINE_COST, "cost_multiples": COST_MULTIPLES,
              "candidates": {}}

    for version, (cfg, _, _, tp_r, _) in CANDIDATES.items():
        sigs, _, tp, regime = _load_cached(version)
        if not sigs:
            report["candidates"][version] = {"error": "no cached signals"}
            print(f"{version}: no cached signals (run MTF research first)")
            continue
        mtf = next(m for m in ALL_MTF_CONFIGS if m.name == cfg)
        base = candles5m if mtf.base == TimeFrame.M5 else resample_candles(candles5m, TimeFrame.M15)
        hold = 288 if mtf.base == TimeFrame.M5 else 96

        rows = {}
        for mult in COST_MULTIPLES:
            cost = round(BASELINE_COST * mult, 3)
            label = f"{mult:g}x"
            m = outcomes_metrics(replay_variant(sigs, base, tp_r=tp_r, sl_atr=1.5,
                                                max_holding_bars=hold, cost_points=cost))
            rows[label] = {
                "cost_points": cost,
                "trades": m.get("trades"),
                "expectancy_r": m.get("expectancy_r"),
                "win_rate_pct": m.get("win_rate_pct"),
                "profit_factor": m.get("profit_factor"),
                "max_dd_pct": m.get("max_dd_pct"),
            }
        report["candidates"][version] = {
            "config": cfg, "regime": regime, "tp_r": tp_r, "sl_atr": 1.5,
            "n_signals": len(sigs), "cost_sweep": rows,
        }
        print(f"\n{version} ({cfg}, RANGING, TP{tp_r}R, {len(sigs)} signals)")
        base_exp = rows["0x"]["expectancy_r"]
        for k, r in rows.items():
            print(f"  cost {r['cost_points']:>4} pts ({k:<4}) Exp={r['expectancy_r']:+.3f}R "
                  f"WR={r['win_rate_pct']}% PF={r['profit_factor']}")
        # Fragility check
        exp_1x = rows["1x"]["expectancy_r"]
        exp_2x = rows["2x"]["expectancy_r"]
        if exp_1x <= 0 or exp_2x <= 0:
            fragility = "FRAGILE" if exp_2x <= 0 else "MODERATE"
        else:
            fragility = "ROBUST"
        report["candidates"][version]["fragility"] = fragility
        print(f"  fragility: {fragility}")

    os.makedirs("reports", exist_ok=True)
    with open("reports/cost_robustness.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    L = ["# Execution-Cost Robustness (XAUUSDT)", f"Baseline round-trip cost: {BASELINE_COST} pts",
         f"Generated: {report['generated_at']}", ""]
    L.append("| Candidate | n | Cost | Exp | WR | PF | DD |")
    L.append("|---|---|---|---|---|---|---|")
    for ver, data in report["candidates"].items():
        if "error" in data:
            continue
        for k, r in data["cost_sweep"].items():
            L.append(f"| {ver} | {data['n_signals']} | {r['cost_points']}pts ({k}) | "
                     f"{r['expectancy_r']:+.3f}R | {r['win_rate_pct']}% | {r['profit_factor']} | {r['max_dd_pct']}% |")
    L.append("")
    L.append("## Fragility")
    for ver, data in report["candidates"].items():
        if "fragility" in data:
            L.append(f"- {ver}: {data['fragility']}")
    L.append("")
    L.append("Conclusion: a candidate is promotion-eligible only if expectancy stays "
             "positive at >= 1.5x baseline cost.")
    with open("reports/cost_robustness.md", "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\nreports/cost_robustness.json + .md written.")


if __name__ == "__main__":
    main()
