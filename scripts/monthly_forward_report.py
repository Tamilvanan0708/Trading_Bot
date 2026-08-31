"""
Monthly forward-observation report (Phase 17).

Side-by-side candidate comparison + cumulative observation statistics.
Writes reports/monthly/forward_YYYYMM.md.

Usage:
  python scripts/monthly_forward_report.py
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

from sqlalchemy import select  # noqa: E402

from app.database.connection import async_session_factory  # noqa: E402
from app.database.models import SignalModel  # noqa: E402

CANDIDATE_VERSIONS = ["CANDIDATE_A_V1", "CANDIDATE_B_V1", "CANDIDATE_C_V1"]


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _oos_reference(version):
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
                return {"expectancy_r": cand.get("expectancy_r", 0), "trades": cand.get("trades", 0)}
    except Exception:  # noqa: BLE001
        pass
    return {}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=1)
    args = parser.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=30 * args.months)

    lines = [f"# Monthly Forward Report — {args.months} month window",
             f"Generated: {datetime.now(timezone.utc).isoformat()}", ""]

    async with async_session_factory() as session:
        stmt = select(SignalModel).where(SignalModel.strategy_version.like("CANDIDATE_%"))
        rows = list((await session.execute(stmt)).scalars().all())
        recent = [r for r in rows if _aware(r.created_at) and _aware(r.created_at) >= since]

        lines.append("## Cumulative observation")
        lines.append(f"- Total candidate signals (all time): {len(rows)}")
        lines.append(f"- Signals in window: {len(recent)}")

        for ver in CANDIDATE_VERSIONS:
            sigs = [r for r in recent if r.strategy_version == ver]
            closed = [s for s in sigs if s.outcome and s.outcome != "OPEN"]
            rs = [s.final_r or 0.0 for s in closed if s.final_r is not None]
            wins = [r for r in rs if r > 0]
            mfe = [s.max_favorable_excursion_r or 0.0 for s in closed if s.max_favorable_excursion_r is not None]
            mae = [s.max_adverse_excursion_r or 0.0 for s in closed if s.max_adverse_excursion_r is not None]
            oos = _oos_reference(ver)
            lines.append(f"\n### {ver}")
            if not sigs:
                lines.append("- no signals in window")
                continue
            lines.append(f"- Signals: {len(sigs)} | Closed: {len(closed)}")
            if rs:
                exp = sum(rs) / len(rs)
                pf = sum(wins) / abs(sum(r for r in rs if r <= 0)) if any(r <= 0 for r in rs) else float("inf")
                lines.append(f"- WR: {len(wins)/len(rs)*100.0:.1f}% | Exp: {exp:+.3f}R | PF: {pf:.2f}")
                lines.append(f"- MFE: {sum(mfe)/len(mfe):.3f}R | MAE: {sum(mae)/len(mae):.3f}R")
                if oos:
                    deg = (exp / oos.get("expectancy_r", 1) - 1) * 100 if oos.get("expectancy_r") else None
                    lines.append(f"- OOS ref exp: {oos.get('expectancy_r'):+.3f}R "
                                 f"| forward vs OOS: {deg:+.1f}% " if deg is not None else f"- OOS ref exp: {oos.get('expectancy_r'):+.3f}R")

    lines.append("\n## Promotion status")
    from app.research.candidates import resolve_candidate_status
    for ver in CANDIDATE_VERSIONS:
        n = sum(1 for r in recent if r.strategy_version == ver and r.outcome and r.outcome != "OPEN")
        lines.append(f"- {ver}: {resolve_candidate_status(ver, forward_signals=n)} ({n} closed)")

    lines.append("\nNo candidate is promoted automatically. Human approval required.")

    os.makedirs("reports/monthly", exist_ok=True)
    path = f"reports/monthly/forward_{datetime.now().strftime('%Y%m')}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\nMonthly report written to {path}")


if __name__ == "__main__":
    asyncio.run(main())
