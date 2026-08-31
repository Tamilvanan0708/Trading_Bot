"""
Trading-session analysis (UTC based).

ASIA        : 00:00 - 07:59 UTC
LONDON      : 08:00 - 11:59 UTC
NEW_YORK    : 12:00 - 20:59 UTC
LONDON_NY   : 12:00 - 15:59 UTC (overlap)
OFF_HOURS   : 21:00 - 23:59 UTC
"""

from collections import defaultdict

from app.backtesting.models import SimulatedTrade

_SESSIONS = [
    (0, 8, "ASIA"),
    (8, 12, "LONDON"),
    (12, 16, "LONDON_NY"),
    (12, 21, "NEW_YORK"),
    (21, 24, "OFF_HOURS"),
]


def _session_for(hour: int) -> str:
    for start, end, name in _SESSIONS:
        if start <= hour < end:
            return name
    return "OFF_HOURS"


def session_analysis(trades: list[SimulatedTrade]) -> dict:
    """Break performance down by trading session."""
    groups: dict[str, list[SimulatedTrade]] = defaultdict(list)
    for t in trades:
        if not t.entry_time:
            continue
        groups[_session_for(t.entry_time.hour)].append(t)

    result = {}
    for name, group in sorted(groups.items()):
        pnls = [t.pnl_usd for t in group]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        rs = [t.pnl_r or 0.0 for t in group]
        result[name] = {
            "trades": len(group),
            "win_rate_pct": round(len(wins) / len(group) * 100.0, 2) if group else 0.0,
            "expectancy_r": round(sum(rs) / len(rs), 2) if rs else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0,
            "net_r": round(sum(rs), 2),
            "net_pnl_usd": round(sum(pnls), 2),
        }
    return result