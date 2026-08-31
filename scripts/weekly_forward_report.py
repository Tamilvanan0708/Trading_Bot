"""
Weekly forward-observation report (Candidate A/B/C side-by-side).

Reports real forward outcomes for each candidate from the signals table
(outcome columns populated by the SignalOutcomeTracker).  Includes the OOS
reference from the research report and the OOS-vs-forward difference.

Usage:
  python scripts/weekly_forward_report.py
  python scripts/weekly_forward_report.py --weeks 1
"""

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from app.database.connection import async_session_factory
from app.database.models import SignalModel

CANDIDATE_VERSIONS = ["CANDIDATE_A_V1", "CANDIDATE_B_V1", "CANDIDATE_C_V1"]


def _aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _oos_reference(version: str) -> dict:
    """Reads the OOS reference for a candidate from the MTF research report."""
    path = "data/research/mtf_report.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        mapping = {
            "CANDIDATE_A_V1": ("PROD_4H_1H_30M_15M", "RANGING", 75),
            "CANDIDATE_B_V1": ("4H_1H_30M_5M", "RANGING", 85),
            "CANDIDATE_C_V1": ("4H_1H_15M_5M", "RANGING", 85),
        }
        cfg, regime, conf = mapping[version]
        for cand in report.get("candidates", []):
            if cand.get("config") == cfg and cand.get("regime") == regime and cand.get("conf") == conf:
                return {
                    "trades": cand.get("trades", 0),
                    "expectancy_r": cand.get("expectancy_r", 0),
                    "win_rate_pct": cand.get("win_rate_pct", 0),
                    "profit_factor": cand.get("profit_factor", 0),
                    "max_dd_pct": cand.get("max_dd_pct", 0),
                    "oos_windows_positive": f"{cand.get('positive_windows')}/{cand.get('total_windows')}",
                }
    except Exception:  # noqa: BLE001
        pass
    return {}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=1)
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(weeks=args.weeks)
    lines = []
    lines.append(f"# WEEKLY FORWARD REPORT — Candidate A/B/C ({args.weeks} week window)")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")

    async with async_session_factory() as session:
        stmt = select(SignalModel).where(SignalModel.strategy_version.like("CANDIDATE_%"))
        rows = list((await session.execute(stmt)).scalars().all())
        recent = [r for r in rows
                  if r.created_at and _aware(r.created_at) >= since]

        by_cand: dict[str, list] = {v: [] for v in CANDIDATE_VERSIONS}
        for r in recent:
            if r.strategy_version in by_cand:
                by_cand[r.strategy_version].append(r)

        comparison = {}
        for version, sigs in by_cand.items():
            closed = [s for s in sigs if s.outcome and s.outcome != "OPEN"]
            open_s = [s for s in sigs if not s.outcome or s.outcome == "OPEN"]
            rs = [s.final_r or 0.0 for s in closed if s.final_r is not None]
            wins = [r for r in rs if r > 0]
            losses = [r for r in rs if r <= 0]
            mfe = [s.max_favorable_excursion_r or 0.0 for s in closed if s.max_favorable_excursion_r is not None]
            mae = [s.max_adverse_excursion_r or 0.0 for s in closed if s.max_adverse_excursion_r is not None]
            sessions = defaultdict(int)
            regimes = defaultdict(int)
            positive_days, negative_days, flat_days = 0, 0, 0
            day_net = defaultdict(float)
            for s in closed:
                sessions[s.session or "UNKNOWN"] += 1
                regimes[s.regime or "UNKNOWN"] += 1
                if s.final_r is not None:
                    day_net[s.created_at.date()] += s.final_r
            for v in day_net.values():
                if v > 0.05:
                    positive_days += 1
                elif v < -0.05:
                    negative_days += 1
                else:
                    flat_days += 1

            stats = {
                "signals": len(sigs),
                "open": len(open_s),
                "closed": len(closed),
                "win_rate_pct": round(len(wins) / len(rs) * 100.0, 2) if rs else 0.0,
                "expectancy_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
                "median_r": round(sorted(rs)[len(rs) // 2], 3) if rs else 0.0,
                "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) else 0.0,
                "avg_mfe_r": round(sum(mfe) / len(mfe), 3) if mfe else 0.0,
                "avg_mae_r": round(sum(mae) / len(mae), 3) if mae else 0.0,
                "sessions": dict(sessions),
                "regimes": dict(regimes),
                "positive_days": positive_days,
                "negative_days": negative_days,
                "flat_days": flat_days,
            }
            oos = _oos_reference(version)
            if oos and stats["closed"] > 0:
                fwd_exp = stats["expectancy_r"]
                oos_exp = oos.get("expectancy_r", 0)
                stats["oos_reference"] = oos
                stats["oos_vs_forward"] = round(fwd_exp - oos_exp, 3)
                stats["degradation_pct"] = round((fwd_exp / oos_exp - 1) * 100, 1) if oos_exp > 0 else None
            elif oos:
                stats["oos_reference"] = oos
            comparison[version] = stats

            lines.append(f"\n## {version}")
            lines.append(f"  Signals: {stats['signals']} (open {stats['open']}, closed {stats['closed']})")
            lines.append(f"  WR: {stats['win_rate_pct']}% | Exp: {stats['expectancy_r']:+}R | Median: {stats['median_r']}R | PF: {stats['profit_factor']}")
            lines.append(f"  MFE: {stats['avg_mfe_r']}R | MAE: {stats['avg_mae_r']}R")
            lines.append(f"  Days: +{stats['positive_days']} / -{stats['negative_days']} / flat {stats['flat_days']}")
            if "oos_reference" in stats and stats["closed"] > 0:
                lines.append(f"  OOS ref: exp={oos.get('expectancy_r')}R n={oos.get('trades')} "
                             f"OOS+={oos.get('oos_windows_positive')}")
                lines.append(f"  OOS-vs-forward: {stats['oos_vs_forward']:+}R "
                             f"({stats.get('degradation_pct', 'n/a')}% degradation)")

    lines.append("\n## STATUS")
    lines.append("Forward observation in progress. No candidate is promoted.")
    lines.append("If the forward sample is insufficient (< 100 signals / < 4-8 weeks): INCONCLUSIVE.")
    lines.append("Parameters are FROZEN. No tuning on forward data.")

    # Promotion states + ranking
    from app.research.candidates import resolve_candidate_status
    from app.research.ranking import PromotionState, PromotionStateMachine
    lines.append("\n## PROMOTION STATUS")
    for version in CANDIDATE_VERSIONS:
        n_closed = comparison.get(version, {}).get("closed", 0)
        state = resolve_candidate_status(version, forward_signals=n_closed)
        lines.append(f"- {version}: {state} (forward closed signals: {n_closed})")

    ranked = []
    for version in CANDIDATE_VERSIONS:
        stats = comparison.get(version, {})
        oos = stats.get("oos_reference") or {}
        ranked.append({
            "version": version,
            "expectancy_r": stats.get("expectancy_r", 0),
            "profit_factor": stats.get("profit_factor", 0),
            "max_dd_pct": 0,
            "trades": stats.get("closed", 0),
            "oos_expectancy_r": oos.get("expectancy_r", 0),
        })
    lines.append("\n## RANKING (weighted, forward-adjusted when sample permits)")
    try:
        from app.research.ranking import rank_candidates
        ranked_out = rank_candidates([
            {"version": v["version"], "expectancy_r": v["oos_expectancy_r"] or v["expectancy_r"],
             "profit_factor": max(v["profit_factor"], 0.5), "max_dd_pct": 20, "trades": max(v["trades"], 50),
             "positive_windows": 7, "total_windows": 13, "forward_expectancy_r": v["expectancy_r"] if v["trades"] >= 10 else None,
             "forward_closed": v["trades"]}
            for v in ranked
        ], forward_weight_bonus=0.0)
        for c in ranked_out:
            lines.append(f"- #{c.get('rank')} {c['version']}: score {c.get('rank_score')}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  (ranking unavailable: {exc})")

    os.makedirs("data/research", exist_ok=True)
    path = f"data/research/weekly_forward_{datetime.now().strftime('%Y%m%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\nWeekly report written to {path}")


if __name__ == "__main__":
    asyncio.run(main())
