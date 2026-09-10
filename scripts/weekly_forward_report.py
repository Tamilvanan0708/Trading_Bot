"""
Weekly forward-observation report.

Renders the outcome of the three frozen forward candidates (CANDIDATE_A/B/C)
across the last N weeks into reports/weekly/forward_YYYYWW.md.

    python scripts/weekly_forward_report.py [--weeks 1]

Cron-safe: an empty/absent database still writes a valid report (exit 0).
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from _forward_report_lib import (  # noqa: E402
    build_report_body,
    ensure_reports_dir,
    parse_flag,
)


def main(argv: list[str]) -> int:
    weeks = max(1, parse_flag(argv, "--weeks", 1))
    days = weeks * 7
    since = datetime.now(timezone.utc) - timedelta(days=days)
    body = build_report_body(days, since)

    out_dir = ensure_reports_dir("weekly")
    iso = datetime.now(timezone.utc).isocalendar()
    fname = f"forward_{iso[0]}W{iso[1]:02d}.md"
    out_path = os.path.join(out_dir, fname)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)

    print(body)
    print(f"Weekly report written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
