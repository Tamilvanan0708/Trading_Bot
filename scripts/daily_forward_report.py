"""
Daily forward-observation report.

Renders the outcome of the three frozen forward candidates (CANDIDATE_A/B/C)
into reports/daily/forward_YYYYMMDD.md.

    python scripts/daily_forward_report.py [--days 1]

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
    days = max(1, parse_flag(argv, "--days", 1))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    body = build_report_body(days, since)

    out_dir = ensure_reports_dir("daily")
    fname = f"forward_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    out_path = os.path.join(out_dir, fname)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)

    print(body)
    print(f"Daily forward report written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
