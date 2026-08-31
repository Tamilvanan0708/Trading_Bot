"""
Daily / weekly forward-observation research summary.

Summarises the real forward signal-outcome data collected in observation mode
and writes a markdown report to data/research/forward_summary_YYYYMMDD.md.
Can be run by cron / Task Scheduler each morning.

Usage:
  python scripts/daily_research_summary.py [--days 1 | --days 7]
"""

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config.settings import get_settings
from app.database.connection import async_session_factory
from app.database.repository import Repository


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="Summary window (1=daily, 7=weekly).")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    settings = get_settings()

    def _aware(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    async with async_session_factory() as session:
        repo = Repository(session)
        rows = await repo.list_open_signals_for_tracking(limit=5000)
        recent = [r for r in rows if _aware(r.created_at) and _aware(r.created_at) >= since]
        closed = [r for r in recent if r.outcome and r.outcome != "OPEN"]
        open_rows = [r for r in recent if not r.outcome or r.outcome == "OPEN"]

        lines = []
        lines.append(f"# Forward Observation Summary ({args.days}-day window)")
        lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
        lines.append(f"Strategy grade: {_read_grade()}")
        lines.append(f"Observation mode: {settings.OBSERVATION_MODE}")
        lines.append("")
        lines.append("## Signals")
        lines.append(f"- Total signals in window: {len(recent)}")
        lines.append(f"- Open (still tracking): {len(open_rows)}")
        lines.append(f"- Closed (outcome reached): {len(closed)}")
        dirs = Counter(r.direction for r in recent)
        lines.append(f"- LONG: {dirs.get('LONG', 0)} | SHORT: {dirs.get('SHORT', 0)}")
        lines.append("")

        if closed:
            rs = [r.final_r or 0.0 for r in closed if r.final_r is not None]
            wins = [r for r in rs if r > 0]
            mfe = [r.max_favorable_excursion_r or 0.0 for r in closed if r.max_favorable_excursion_r is not None]
            mae = [r.max_adverse_excursion_r or 0.0 for r in closed if r.max_adverse_excursion_r is not None]
            lines.append("## Forward Outcomes")
            lines.append(f"- Closed: {len(closed)}")
            lines.append(f"- Win rate: {len(wins) / len(rs) * 100.0:.1f}%")
            lines.append(f"- Expectancy: {sum(rs) / len(rs):+.3f} R")
            lines.append(f"- Avg MFE: {sum(mfe) / len(mfe):.3f} R")
            lines.append(f"- Avg MAE: {sum(mae) / len(mae):.3f} R")
            by_out = Counter(r.outcome for r in closed)
            lines.append(f"- Outcomes: {dict(by_out)}")
        else:
            lines.append("## Forward Outcomes")
            lines.append("- No closed signals yet. Observation continues.")

        lines.append("")
        lines.append("> Research only. No strategy changes are made automatically.")

        # Per-candidate forward data
        lines.append("")
        lines.append("## Candidate Forward Data (A/B/C)")
        for ver in ("CANDIDATE_A_V1", "CANDIDATE_B_V1", "CANDIDATE_C_V1"):
            sub = [r for r in recent if r.strategy_version == ver]
            if not sub:
                lines.append(f"- **{ver}**: no signals in window")
                continue
            sub_c = [r for r in closed if r.strategy_version == ver]
            rs_c = [r.final_r or 0.0 for r in sub_c if r.final_r is not None]
            wins_c = [r for r in rs_c if r > 0]
            if rs_c:
                lines.append(f"- **{ver}**: total={len(sub)}, closed={len(sub_c)}, "
                             f"WR={len(wins_c) / len(rs_c) * 100.0:.1f}%, "
                             f"Exp={sum(rs_c) / len(rs_c):+.3f}R")
            else:
                lines.append(f"- **{ver}**: total={len(sub)}, open (no closed yet)")

    os.makedirs("data/research", exist_ok=True)
    path = f"data/research/forward_summary_{datetime.now().strftime('%Y%m%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\nSummary written to {path}")


def _read_grade() -> str:
    import json
    path = "data/research/latest_report.json"
    if not os.path.exists(path):
        return "INCONCLUSIVE"
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("classification", {}).get("grade", "INCONCLUSIVE")


if __name__ == "__main__":
    asyncio.run(main())
