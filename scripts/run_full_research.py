"""
FULL REPRODUCIBLE RESEARCH PIPELINE
====================================

One clean command that runs the complete research program on REAL Binance
XAUUSDT data and produces a single merged report + human-readable summary:

  1. Data validation (gaps / duplicates / out-of-order / invalid OHLC)
  2. Walk-forward (multiple chronological TRAIN/VALIDATION/TEST windows)
  3. Out-of-sample metrics (WR, PF, expectancy, avg/median R, max DD,
     Sharpe/Sortino-like, recovery factor, streaks, MAE/MFE)
  4. Monte Carlo simulation
  5. Bootstrap confidence intervals
  6. Regime / session / confluence / R-multiple / forensics analysis
  7. Conservative strategy classification (pooled OOS)
  8. Scientific diagnosis (entry quality, target reachability, feature
     attribution, ATR quartiles, signal-clustering)
  9. Signal-selection robustness (ALL vs strongest-per-session / per-day /
     per-4H / min-spacing / highest-confluence / first-after-break /
     one-direction-per-session / cooldown-after-loss)
 10. Parameter robustness (TP sweep, SL sweep, random signal removal)
 11. Candidate OOS validation on strict chronological TEST windows

Outputs (data/research/):
  - latest_report.json        (steps 1-7, authoritative classification)
  - diagnosis_report.json     (steps 8-10)
  - candidate_oos_windows.json(step 11)
  - full_research_report.json (merged, single file)
  - full_research_report.txt  (human-readable summary)

Usage:
  python scripts/run_full_research.py --data data/research/xauusd_15m_full.json
  python scripts/run_full_research.py --days 240
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PY = sys.executable


def _run(*args):
    print(f"\n{'=' * 72}\n>>> {args[0]} {' '.join(args[1:])}\n{'=' * 72}")
    proc = subprocess.run([PY, *args], cwd=ROOT)
    if proc.returncode != 0:
        print(f"[ABORT] Sub-step failed with exit code {proc.returncode}")
        sys.exit(proc.returncode)


def _load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _human_readable(report: dict, path: str) -> None:
    lines = []
    lines.append("XAU/USD FULL RESEARCH REPORT")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 72)

    latest = report.get("latest_report", {})
    lines.append("\n## STRATEGY CLASSIFICATION (PRODUCTION STRATEGY)")
    lines.append(f"Grade: {latest.get('classification', {}).get('grade', 'INCONCLUSIVE')}")
    for r in latest.get("classification", {}).get("reasons", []):
        lines.append(f"  - {r}")
    oos = latest.get("out_of_sample", {})
    lines.append(f"Pooled OOS: trades={oos.get('trades')} WR={oos.get('win_rate_pct')}% "
                 f"PF={oos.get('profit_factor')} Exp={oos.get('expectancy_r')}R "
                 f"DD={oos.get('max_drawdown_pct')}%")
    mc = latest.get("monte_carlo", {})
    lines.append(f"Monte Carlo: P(negative return)={mc.get('probability_negative_return_pct')}% "
                 f"p95 DD={mc.get('p95_drawdown_pct')}%")
    boot = latest.get("bootstrap", {})
    mr = boot.get("mean_r", {})
    lines.append(f"Bootstrap mean-R CI: [{mr.get('lo')}, {mr.get('hi')}]")
    wf = latest.get("walk_forward", {})
    lines.append(f"Walk-forward: {wf.get('total_windows')} windows, "
                 f"{wf.get('positive_windows')} positive")

    diag = report.get("diagnosis_report", {})
    lines.append("\n## SCIENTIFIC DIAGNOSIS")
    eq = diag.get("phase3", {}).get("entry_quality", {})
    lines.append(f"Signals: {diag.get('signals')} | Median MFE: {eq.get('median_mfe_r')}R "
                 f"| Median MAE: {eq.get('median_mae_r')}R "
                 f"| MFE>=1R: {eq.get('mfe_ge_1r_pct')}%")
    cl = diag.get("phase3", {}).get("clustering", {})
    lines.append(f"Clustering: {cl.get('clustering_pct')}% of signals within 2h of previous "
                 f"({cl.get('avg_signals_per_day')}/day)")
    tp = diag.get("phase6", {}).get("tp_sweep", {})
    if tp:
        best_tp = max(tp, key=lambda k: tp[k].get("expectancy_r", -9))
        lines.append(f"Best TP sweep: {best_tp}R -> Exp={tp[best_tp].get('expectancy_r')}R")

    cand = report.get("candidate_oos_windows", {}).get("aggregate", {})
    lines.append("\n## CANDIDATE OOS (POOLED ACROSS TEST WINDOWS)")
    for name, m in cand.items():
        lines.append(f"  {name:<36} n={m.get('trades', 0):>5} "
                     f"WR={m.get('win_rate_pct', 0):>5}% PF={m.get('profit_factor', 0):>5} "
                     f"Exp={m.get('expectancy_r', 0):>7}R DD={m.get('max_dd_pct', 0):>5}%")

    lines.append("\n## PROMOTION STATUS")
    lines.append("No candidate has been promoted. Production strategy remains FAILED/UNPROVEN.")
    lines.append("Forward observation (OBSERVATION_MODE=true) is required to accumulate real")
    lines.append("forward evidence before any candidate may be considered for promotion.")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nHuman-readable report written to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Full reproducible research pipeline.")
    parser.add_argument("--data", type=str, help="Path to existing real-history JSON file.")
    parser.add_argument("--data5m", type=str, default="data/research/xauusd_5m_2yr.json",
                        help="Path to 5M real-history JSON file (for MTF research).")
    parser.add_argument("--days", type=int, default=240, help="Days of history to fetch if no --data.")
    parser.add_argument("--train-months", type=int, default=2)
    parser.add_argument("--val-months", type=int, default=1)
    parser.add_argument("--test-months", type=int, default=1)
    parser.add_argument("--simulations", type=int, default=5000)
    parser.add_argument("--skip-diagnosis", action="store_true",
                        help="Skip the (slow) full-history signal diagnosis.")
    args = parser.parse_args()

    data_arg = ["--data", args.data] if args.data else ["--days", str(args.days)]

    # Step 1-7: authoritative validation + classification
    _run("scripts/run_strategy_research.py", *data_arg,
         "--train-months", str(args.train_months),
         "--val-months", str(args.val_months),
         "--test-months", str(args.test_months),
         "--simulations", str(args.simulations))

    # Step 8-10: diagnosis + robustness (uses cached signals when available)
    if not args.skip_diagnosis:
        if args.data:
            _run("scripts/run_scientific_diagnosis.py", "--data", args.data)
        else:
            print("[SKIP] Diagnosis requires --data (full-history file). "
                  "Run: python scripts/run_scientific_diagnosis.py --data data/research/xauusd_15m_full.json")
    else:
        print("[SKIP] Diagnosis skipped (--skip-diagnosis).")

    # Step 11: candidate OOS on strict chronological test windows
    if args.data:
        _run("scripts/run_candidate_oos.py", "--data", args.data)
    else:
        _run("scripts/run_candidate_oos.py")

    # Step 12: MTF + candidate research (5M entries, alternative configurations)
    _run("scripts/run_mtf_research.py", "--data5m", args.data5m,
         "--cache", "data/research/mtf_signals", "--max-candidates", "12")

    # Merge reports
    merged = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_report": _load("data/research/latest_report.json"),
        "diagnosis_report": _load("data/research/diagnosis_report.json"),
        "candidate_oos_windows": _load("data/research/candidate_oos_windows.json"),
        "mtf_report": _load("data/research/mtf_report.json"),
    }
    os.makedirs("data/research", exist_ok=True)
    with open("data/research/full_research_report.json", "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, default=str)
    _human_readable(merged, "data/research/full_research_report.txt")
    _write_html(merged, "data/research/latest_report.html")
    print("\nFULL RESEARCH PIPELINE COMPLETE.")


def _write_html(report: dict, path: str) -> None:
    """Writes a self-contained HTML report for the dashboard / distribution."""
    def _esc(v):
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    latest = report.get("latest_report", {})
    mtf = report.get("mtf_report", {})
    cand = report.get("candidate_oos_windows", {}).get("aggregate", {})
    grade = latest.get("classification", {}).get("grade", "INCONCLUSIVE")
    oos = latest.get("out_of_sample", {})
    mc = latest.get("monte_carlo", {})
    boot = latest.get("bootstrap", {})
    mr = boot.get("mean_r", {})

    rows = []
    for name, m in cand.items():
        rows.append(f"<tr><td>{_esc(name)}</td><td>{m.get('trades', 0)}</td>"
                    f"<td>{m.get('win_rate_pct', 0)}%</td><td>{m.get('profit_factor', 0)}</td>"
                    f"<td>{m.get('expectancy_r', 0)}</td><td>{m.get('max_dd_pct', 0)}%</td></tr>")

    mtf_rows = []
    for c in mtf.get("candidates", [])[:10]:
        mtf_rows.append(f"<tr><td>{_esc(c.get('config'))}</td><td>{_esc(c.get('regime'))}</td>"
                        f"<td>{c.get('conf')}</td><td>{_esc(c.get('selection'))}</td><td>{c.get('tp_r')}</td>"
                        f"<td>{c.get('trades', 0)}</td><td>{c.get('win_rate_pct', 0)}%</td>"
                        f"<td>{c.get('expectancy_r', 0)}</td>"
                        f"<td>{c.get('positive_windows', 0)}/{c.get('total_windows', 0)}</td></tr>")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>XAU/USD Research Report</title>
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#0b0e14; color:#d1d5db; padding:20px; }}
h1,h2 {{ color:#fbbf24; }} table {{ border-collapse:collapse; width:100%; margin:10px 0; }}
th,td {{ border:1px solid #232b3b; padding:6px 10px; text-align:left; font-size:14px; }}
th {{ background:#1a2230; }} .card {{ background:#151a23; border:1px solid #232b3b; border-radius:8px; padding:14px; margin:12px 0; }}
.grade-failed {{ color:#dc2626; font-weight:bold; }} .grade-robust {{ color:#059669; font-weight:bold; }}
</style></head><body>
<h1>XAU/USD Signal Intelligence — Full Research Report</h1>
<p>Generated: {_esc(report.get('generated_at'))}</p>

<div class="card"><h2>Production Strategy Classification</h2>
<p>Grade: <span class="grade-{_esc(grade.lower())}">{_esc(grade)}</span></p>
<ul>{''.join(f'<li>{_esc(r)}</li>' for r in latest.get('classification', {}).get('reasons', []))}</ul>
<p>Pooled OOS: {oos.get('trades', 0)} trades, WR {oos.get('win_rate_pct', 0)}%, PF {oos.get('profit_factor', 0)}, Exp {oos.get('expectancy_r', 0)} R, DD {oos.get('max_drawdown_pct', 0)}%</p>
<p>Monte Carlo: P(negative) = {mc.get('probability_negative_return_pct', 0)}%, p95 DD = {mc.get('p95_drawdown_pct', 0)}%</p>
<p>Bootstrap mean-R CI: [{mr.get('lo')}, {mr.get('hi')}]</p></div>

<div class="card"><h2>MTF / Candidate Research (5M entries, 4 configurations)</h2>
<p>Data: {_esc(mtf.get('data', {}).get('start', '-'))} -> {_esc(mtf.get('data', {}).get('end', '-'))}</p>
<table><thead><tr><th>Config</th><th>Regime</th><th>Conf</th><th>Selection</th><th>TP</th><th>Trades</th><th>WR</th><th>Exp</th><th>OOS+</th></tr></thead>
<tbody>{''.join(mtf_rows)}</tbody></table></div>

<div class="card"><h2>Candidate OOS (pooled test windows)</h2>
<table><thead><tr><th>Candidate</th><th>Trades</th><th>WR</th><th>PF</th><th>Exp</th><th>Max DD</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>

<div class="card"><h2>Promotion Status</h2>
<p>No candidate is promoted automatically. Forward observation
(OBSERVATION_MODE=true) is required before promotion review. See
docs/strategy_promotion_policy.md.</p></div>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML report written to {path}")


if __name__ == "__main__":
    main()
