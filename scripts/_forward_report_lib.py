"""
Shared helpers for the daily and weekly forward-observation reports.

Both scripts aggregate the outcome of every forward-observation candidate
(CANDIDATE_A_V1 / B_V1 / C_V1) from the `signals` table and render a
Markdown report. They are cron-safe: an empty or unavailable database still
produces a valid report (returncode 0), never a traceback.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

from app.database.connection import async_session_factory  # noqa: E402
from app.database.models import SignalModel  # noqa: E402
from app.research.candidate_health import assess_candidate_health  # noqa: E402
from app.research.candidates import ALL_FORWARD_CANDIDATES, resolve_candidate_status  # noqa: E402

# Outcomes that signal the paper/research engine counts as resolved.
_CLOSED_OUTCOMES = {"TP1", "TP2", "TP3", "SL", "EXPIRED", "TP_HIT", "SL_HIT", "BREAKEVEN_HIT"}
_WIN_OUTCOMES = {"TP1", "TP2", "TP3", "TP_HIT"}


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def _load_signals(since: datetime) -> list[SignalModel]:
    """Load forward-candidate signals whose outcome/creation is within range.

    Returns [] on any DB error so the report scripts are cron-safe.
    """
    try:
        async with async_session_factory() as session:
            rows = (await session.execute(
                select(SignalModel)
                .where(SignalModel.strategy_version.isnot(None))
                .order_by(SignalModel.created_at.asc())
            )).scalars().all()
    except Exception as exc:  # noqa: BLE001 - never crash a scheduled report
        print(f"[report] signal load skipped ({exc.__class__.__name__}: {exc})")
        return []
    cutoff = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    out = []
    for r in rows:
        ct = _aware(r.created_at)
        if ct is not None and ct < cutoff:
            continue
        out.append(r)
    return out


def _stats_for_rows(rows) -> dict:
    closed = [r for r in rows if r.outcome in _CLOSED_OUTCOMES]
    wins = [r for r in closed if r.outcome in _WIN_OUTCOMES]
    rs = [r.final_r for r in closed if r.final_r is not None]
    n = len(closed)
    return {
        "signals": len(rows),
        "closed": n,
        "win_rate_pct": round(len(wins) / n * 100.0, 2) if n else None,
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "best_r": round(max(rs), 2) if rs else None,
        "worst_r": round(min(rs), 2) if rs else None,
    }


def _fmt(x, suffix="", dashes=2) -> str:
    return "n/a" if x is None else (f"{x:.{dashes}f}{suffix}" if isinstance(x, float) else f"{x}{suffix}")


def build_report_body(window_days: int, since: datetime) -> str:
    """Render the full report Markdown. Always returns valid text."""
    signals = asyncio.run(_load_signals(since))

    title = "Daily forward-observation report" if window_days <= 1 else "Weekly forward-observation report"
    lines: list[str] = []
    generated = datetime.now(timezone.utc)
    lines.append(f"# {title}")
    lines.append(f"Generated: {generated.isoformat()}")
    lines.append(f"Window: last {window_days} day(s) from {since.date().isoformat()}")
    lines.append("")
    lines.append(f"Forward-candidate signals in window: **{len(signals)}**")
    lines.append("")

    lines.append("## Candidate summary")
    lines.append("| Candidate | Config | Status | Signals | Closed | Win% | Expectancy(R) | Health |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for cand in ALL_FORWARD_CANDIDATES:
        rows = [s for s in signals if s.strategy_version == cand.version]
        st = _stats_for_rows(rows)
        status = resolve_candidate_status(cand.version, forward_signals=st["closed"])
        health = assess_candidate_health(cand.version, rows).state if rows else "YELLOW"
        lines.append(
            f"| {cand.name} | {cand.mtf.label} | {status} | {st['signals']} | {st['closed']} | "
            f"{_fmt(st['win_rate_pct'], '%')} | {_fmt(st['expectancy_r'], 'R', 3)} | {health} |"
        )
    lines.append("")

    lines.append("## Outcome breakdown")
    by_outcome: dict[str, int] = defaultdict(int)
    for s in signals:
        by_outcome[s.outcome or "OPEN"] += 1
    if by_outcome:
        lines.append("| Outcome | Count |")
        lines.append("|---|---|")
        for k, v in sorted(by_outcome.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"| {k} | {v} |")
    else:
        lines.append("_No forward-candidate signals recorded yet._")
    lines.append("")

    lines.append("## Regime / session split")
    by_axis: dict[str, dict[str, int]] = {"regime": defaultdict(int), "session": defaultdict(int)}
    for s in signals:
        if s.regime:
            by_axis["regime"][s.regime] += 1
        if s.session:
            by_axis["session"][s.session] += 1
    for axis in ("regime", "session"):
        vals = by_axis[axis]
        if vals:
            lines.append(f"- **{axis}**: " + ", ".join(f"{k}={v}" for k, v in sorted(vals.items(), key=lambda kv: kv[1], reverse=True)))
    if not by_axis["regime"] and not by_axis["session"]:
        lines.append("_No regime/session data in window._")
    lines.append("")

    lines.append("## Promotion policy")
    lines.append("No automatic promotion. Candidates must clear 100+ forward")
    lines.append("signals across 4-8 weeks before HUMAN_REVIEW (docs strategy")
    lines.append("promotion policy). None of these candidates place real orders.")
    lines.append("")
    if not signals:
        lines.append("_Note: this window had no forward-candidate signals. The")
        lines.append("observation loop writes signals while OBSERVATION_MODE/auto")
        lines.append("analysis is running against live or research data._")
    return "\n".join(lines) + "\n"


def ensure_reports_dir(*sub: str) -> str:
    path = os.path.join("reports", *sub)
    os.makedirs(path, exist_ok=True)
    return path


def parse_flag(argv: list[str], flag: str, default: int) -> int:
    for i, tok in enumerate(argv):
        if tok == flag and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return default
    return default
