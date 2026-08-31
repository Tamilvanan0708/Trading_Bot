"""
AI-validation outcome analysis.

Compares AI APPROVE / CAUTION / REJECT against actual trade outcomes to
measure whether the AI validator adds information on top of the deterministic
engine.  The AI remains a validator — it never produces price predictions.
"""

from collections import defaultdict

from app.backtesting.models import SimulatedTrade

Status = str  # APPROVE | CAUTION | REJECT | None


def _metrics_for(pairs: list[tuple[Status, float]]) -> dict:
    """pairs = [(ai_status, pnl_usd), ...] with non-null AI status."""
    if not pairs:
        return {"trades": 0, "win_rate_pct": 0.0, "expectancy_r": 0.0, "net_pnl_usd": 0.0}
    rs = [1.0 if p > 0 else -1.0 for _, p in pairs]  # R approx for win/loss
    wins = [p for _, p in pairs if p > 0]
    pnls = [p for _, p in pairs]
    return {
        "trades": len(pairs),
        "win_rate_pct": round(len(wins) / len(pairs) * 100.0, 2),
        "expectancy_r": round(sum(rs) / len(rs), 2),
        "net_pnl_usd": round(sum(pnls), 2),
    }


def ai_outcome_analysis(trades: list[SimulatedTrade]) -> dict:
    """Group trade outcomes by the AI status recorded on the signal."""
    groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for t in trades:
        status = t.ai_status
        if status:
            groups[status].append((status, t.pnl_usd))

    breakdown = {k: _metrics_for(v) for k, v in sorted(groups.items())}

    # False-positive / false-negative rates relative to APPROVE as the signal.
    approved = groups.get("APPROVE", [])
    rejected = groups.get("REJECT", [])
    cautioned = groups.get("CAUTION", [])
    total_analyzed = len(approved) + len(rejected)

    false_positive = len([p for _, p in approved if p <= 0])  # APPROVE but lost
    false_negative = len([p for _, p in rejected if p > 0])   # REJECT but would have won

    return {
        "by_status": breakdown,
        "false_positive_rate_pct": round(false_positive / len(approved) * 100.0, 2) if approved else 0.0,
        "false_negative_rate_pct": round(false_negative / len(rejected) * 100.0, 2) if rejected else 0.0,
        "total_analyzed": total_analyzed,
        "not_analyzed_count": sum(1 for t in trades if not t.ai_status),
    }