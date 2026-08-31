"""
Confluence-score bucket analysis.

Groups trades by their signal confidence score to evaluate whether the
configured STRONG threshold (75) is actually supported by OOS data.
"""

from collections import defaultdict

from app.backtesting.models import SimulatedTrade

BUCKETS = [(0, 49), (50, 59), (60, 69), (70, 74), (75, 79), (80, 89), (90, 100)]


def _bucket(score: float) -> str:
    for lo, hi in BUCKETS:
        if lo <= score <= hi:
            return f"{lo}–{hi}"
    return "OTHER"


def confluence_bucket_analysis(trades: list[SimulatedTrade]) -> dict:
    """Group trades by confidence score bucket."""
    groups: dict[str, list[SimulatedTrade]] = defaultdict(list)
    for t in trades:
        cs = t.confidence_score or 0.0
        groups[_bucket(cs)].append(t)

    result = {}
    ordered = sorted(groups.items(), key=lambda x: int(x[0].split("–")[0]))
    for bucket_name, group in ordered:
        rs = [t.pnl_r or 0.0 for t in group]
        wins = [r for r in rs if r > 0]
        pnls = [t.pnl_usd for t in group]
        gross_win = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p <= 0))
        cum = 0.0
        peak = 0.0
        dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        result[bucket_name] = {
            "trades": len(group),
            "win_rate_pct": round(len(wins) / len(group) * 100.0, 2) if group else 0.0,
            "expectancy_r": round(sum(rs) / len(rs), 2) if rs else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0,
            "avg_r": round(sum(rs) / len(rs), 2) if rs else 0.0,
            "max_drawdown_usd": round(dd, 2),
        }
    return result