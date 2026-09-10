"""
Generates the formal MTF comparison publication documents:

  reports/mtf_comparison.json
  reports/mtf_comparison.md

Derived from data/research/mtf_report.json (the raw research output).
Includes per-config best exit, top candidates with OOS windows, robustness,
Monte Carlo and bootstrap, and a balanced ranking (Phase N).
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.research.ranking import rank_candidates  # noqa: E402


def main() -> None:
    src = "data/research/mtf_report.json"
    if os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            report = json.load(f)
    else:
        # Awaiting research: run scripts/run_mtf_research.py to populate the raw
        # report. Until then, emit a valid but empty comparison so downstream
        # consumers (dashboard, reports/, this CLI) never hard-fail.
        print(f"MTF report not found: {src}. Emitting empty comparison (run scripts/run_mtf_research.py to populate).")
        report = {
            "candidates": [],
            "data": {},
            "best_exit_per_config": {},
            "note": "Awaiting MTF research output.",
        }

    candidates = report.get("candidates", [])
    # Merge cost-robustness data into candidates for cost-aware ranking.
    cost_path = "reports/cost_robustness.json"
    cost_map = {}
    if os.path.exists(cost_path):
        with open(cost_path, encoding="utf-8") as f:
            cost_report = json.load(f)
        version_map = {
            "PROD_4H_1H_30M_15M": "CANDIDATE_A_V1",
            "4H_1H_30M_5M": "CANDIDATE_B_V1",
            "4H_1H_15M_5M": "CANDIDATE_C_V1",
        }
        for cand in candidates:
            ver = version_map.get(cand.get("config"))
            if ver and ver in cost_report.get("candidates", {}):
                cost_map[cand.get("config")] = cost_report["candidates"][ver].get("cost_sweep", {})
    for cand in candidates:
        if cand.get("config") in cost_map:
            cand["cost_sweep"] = cost_map[cand["config"]]

    ranked = rank_candidates(candidates)

    comparison = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": report.get("data"),
        "best_exit_per_config": report.get("best_exit_per_config"),
        "ranked_candidates": ranked,
        "candidate_count": len(ranked),
        "note": "Balanced risk-adjusted ranking (NOT primarily win rate).",
    }
    os.makedirs("reports", exist_ok=True)
    with open("reports/mtf_comparison.json", "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, default=str)

    L = []
    L.append("# XAU/USD MTF + Candidate Comparison")
    L.append(f"Generated: {comparison['generated_at']}")
    data = report.get("data", {})
    L.append(f"Data: {data.get('start')} -> {data.get('end')} "
             f"(5M {data.get('5m_candles')} candles / 15M {data.get('15m_candles')})")
    L.append("")
    L.append("## Best exit per configuration (ALL signals, pooled OOS)")
    L.append("| Config | Exit | Trades | Expectancy |")
    L.append("|---|---|---|---|")
    for k, v in (report.get("best_exit_per_config") or {}).items():
        L.append(f"| {k} | {v['exit']} | {v['metrics'].get('trades')} | {v['expectancy_r']:+.3f}R |")
    L.append("")
    L.append("## Ranked candidates (balanced score)")
    L.append("| Rank | Config | Regime | Conf | TP | Trades | WR | PF | Exp | DD | OOS+ |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for c in ranked:
        L.append(f"| {c.get('rank')} | {c['config']} | {c.get('regime')} | {c.get('conf')} | "
                 f"{c.get('tp_r')} | {c.get('trades')} | {c.get('win_rate_pct')}% | "
                 f"{c.get('profit_factor')} | {c.get('expectancy_r'):+.3f}R | "
                 f"{c.get('max_dd_pct')}% | {c.get('positive_windows')}/{c.get('total_windows')} |")
    L.append("")
    L.append("## Top 3 detail")
    for c in ranked[:3]:
        L.append(f"\n### #{c.get('rank')} {c['config']} R:{c.get('regime')} C:{c.get('conf')} TP:{c.get('tp_r')}")
        L.append(f"- Trades: {c.get('trades')} | WR: {c.get('win_rate_pct')}% | PF: {c.get('profit_factor')} "
                 f"| Exp: {c.get('expectancy_r'):+.3f}R | DD: {c.get('max_dd_pct')}%")
        L.append(f"- OOS windows positive: {c.get('positive_windows')}/{c.get('total_windows')}")
        mc = c.get("monte_carlo", {})
        if mc:
            L.append(f"- Monte Carlo: P(neg)={mc.get('probability_negative_return_pct')}% "
                     f"p95DD={mc.get('p95_drawdown_pct')}%")
        boot = c.get("bootstrap", {})
        mr = boot.get("mean_r", {})
        if mr:
            L.append(f"- Bootstrap mean-R CI: [{mr.get('lo')}, {mr.get('hi')}]")
        pert = c.get("robustness_tp", {})
        if pert:
            exps = [round(v.get("expectancy_r", 0), 3) if isinstance(v, dict) else v for v in pert.values()]
            L.append(f"- TP-perturbation expectancy: {exps}")
    L.append("")
    L.append("## Conclusion")
    L.append("No candidate is promoted. All candidates must pass forward observation "
             "(100+ signals, 4-8 weeks) before promotion review per docs/strategy_promotion_policy.md.")

    with open("reports/mtf_comparison.md", "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print("\nreports/mtf_comparison.json + reports/mtf_comparison.md written.")


if __name__ == "__main__":
    main()
