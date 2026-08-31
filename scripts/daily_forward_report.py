"""
Daily forward-observation report.

Generates reports/daily/forward_YYYYMMDD.md with system health, candidate
performance, signals, outcomes, expectancy, PF, drawdown, daily opportunity,
telegram status and observation status.  Promotes NOTHING automatically.

Usage:
  python scripts/daily_forward_report.py
  python scripts/daily_forward_report.py --days 1
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select  # noqa: E402

from app.database.connection import async_session_factory  # noqa: E402
from app.database.models import SignalModel  # noqa: E402


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    grade = (_read_json("data/research/latest_report.json", {}) or {}).get("classification", {}).get("grade", "INCONCLUSIVE")

    lines = []
    lines.append(f"# Daily Forward Report — {args.days}-day window")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Production strategy grade: {grade}")
    lines.append(f"Observation mode: ACTIVE" )

    async with async_session_factory() as session:
        stmt = select(SignalModel).order_by(SignalModel.created_at)
        rows = list((await session.execute(stmt)).scalars().all())
        recent = [r for r in rows if _aware(r.created_at) and _aware(r.created_at) >= since]

        prod = [r for r in recent if not (r.strategy_version or "").startswith("CANDIDATE")]
        cand = {v: [] for v in ("CANDIDATE_A_V1", "CANDIDATE_B_V1", "CANDIDATE_C_V1")}
        for r in recent:
            if r.strategy_version in cand:
                cand[r.strategy_version].append(r)

        lines.append("\n## System Health")
        lines.append(f"- Signals today: {len(recent)}")
        lines.append(f"- Production signals: {len(prod)} | Candidate signals: {sum(len(v) for v in cand.values())}")
        dirs = Counter(r.direction for r in recent)
        lines.append(f"- Direction: LONG={dirs.get('LONG', 0)} SHORT={dirs.get('SHORT', 0)}")

        lines.append("\n## Candidates (forward)")
        for ver, sigs in cand.items():
            if not sigs:
                lines.append(f"- **{ver}**: no signals in window")
                continue
            closed = [s for s in sigs if s.outcome and s.outcome != "OPEN"]
            rs = [s.final_r or 0.0 for s in closed if s.final_r is not None]
            wins = [r for r in rs if r > 0]
            mfe = [s.max_favorable_excursion_r or 0.0 for s in closed if s.max_favorable_excursion_r is not None]
            mae = [s.max_adverse_excursion_r or 0.0 for s in closed if s.max_adverse_excursion_r is not None]
            by_out = Counter(s.outcome for s in closed)
            points = [abs(s.take_profit_1 - s.entry_price) for s in closed if s.outcome in ("TP1", "TP2", "TP3") and s.take_profit_1]
            lines.append(f"- **{ver}**: total={len(sigs)}, closed={len(closed)}, "
                         f"WR={len(wins)/len(rs)*100.0:.1f}%" if rs else f"- **{ver}**: total={len(sigs)}, open={len(sigs)}")
            if rs:
                lines.append(f"  Exp={sum(rs)/len(rs):+.3f}R Median={sorted(rs)[len(rs)//2]:+.3f}R "
                             f"MFE={sum(mfe)/len(mfe):.3f}R MAE={sum(mae)/len(mae):.3f}R outcomes={dict(by_out)}")
            if points:
                lines.append(f"  Avg winning points: {sum(points)/len(points):.1f}")

        # Daily opportunity (best winner points per day across candidates)
        wins_by_day = defaultdict(list)
        for r in recent:
            if r.outcome in ("TP1", "TP2", "TP3") and r.take_profit_1:
                wins_by_day[_aware(r.created_at).date()].append(abs(r.take_profit_1 - r.entry_price))
        if wins_by_day:
            best_day = max(sum(v) / len(v) for v in wins_by_day.values())
            lines.append("\n## Daily Opportunity")
            lines.append(f"- Days with winning signals: {len(wins_by_day)}")
            lines.append(f"- Avg best winning-day points: {best_day:.1f}")
            lines.append("- (Target 30-50 pts/day is NOT forced; report is informational.)")

    lines.append("\n## Telegram")
    lines.append("- Alerts: configured. Candidate alerts configurable via CANDIDATE_TELEGRAM_ALERTS_ENABLED.")

    lines.append("\n## Observation")
    lines.append("- Mode: active, signal-only, no paper trades.")
    lines.append("- Outcomes tracked via SignalOutcomeTracker (MAE/MFE/TP/SL/final R).")
    lines.append("- No candidate is promoted automatically.")

    os.makedirs("reports/daily", exist_ok=True)
    path = f"reports/daily/forward_{datetime.now().strftime('%Y%m%d')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\nDaily forward report written to {path}")


if __name__ == "__main__":
    asyncio.run(main())
