"""
Performance analytics engine for paper trading.

Computes detailed, statistically meaningful metrics from closed paper trades:
win rate, profit factor, expectancy, average R, max drawdown, recovery factor,
long/short breakdown, and per-strategy breakdown.
"""

import math

from app.core.constants import SignalDirection
from app.paper_trading.state_machine import PaperPosition


def compute_performance(trades: list[PaperPosition]) -> dict:
    """Computes performance metrics from a list of closed paper positions."""
    closed = [t for t in trades if t.closed_at is not None and t.realized_pnl_usd is not None]

    if not closed:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "avg_win_usd": 0.0,
            "avg_loss_usd": 0.0,
            "profit_factor": 0.0,
            "expectancy_usd": 0.0,
            "avg_r": 0.0,
            "max_drawdown_usd": 0.0,
            "recovery_factor": 0.0,
            "sharpe_like": 0.0,
            "long_wins": 0,
            "long_losses": 0,
            "long_win_rate_pct": 0.0,
            "short_wins": 0,
            "short_losses": 0,
            "short_win_rate_pct": 0.0,
            "strategy_breakdown": {},
            "monthly_pnl": {},
        }

    pnls = [t.realized_pnl_usd for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_trades = len(closed)
    win_rate = len(wins) / total_trades * 100.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.99 if gross_win > 0 else 0.0)
    expectancy = (win_rate / 100.0 * avg_win) - ((1 - win_rate / 100.0) * abs(avg_loss))

    rs = [t.realized_r for t in closed if t.realized_r is not None]
    avg_r = sum(rs) / len(rs) if rs else 0.0

    # Max drawdown & recovery factor from cumulative P&L
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    net = sum(pnls)
    recovery = net / max_dd if max_dd > 0 else 0.0

    # Sharpe-like metric (per-trade, annualized-ish)
    if len(pnls) > 1:
        mean = sum(pnls) / len(pnls)
        var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(var)
        sharpe = mean / std * math.sqrt(365) if std > 0 else 0.0
    else:
        sharpe = 0.0

    # Long vs short
    longs = [t for t in closed if t.direction == SignalDirection.LONG]
    shorts = [t for t in closed if t.direction == SignalDirection.SHORT]
    long_wins = [t for t in longs if t.realized_pnl_usd > 0]
    short_wins = [t for t in shorts if t.realized_pnl_usd > 0]

    # Strategy/exit-reason breakdown
    reason_map: dict[str, list[float]] = {}
    for t in closed:
        reason = t.exit_reason or "UNKNOWN"
        reason_map.setdefault(reason, []).append(t.realized_pnl_usd)

    strategy_breakdown = {
        reason: {
            "total": len(pnls_list),
            "net_pnl_usd": round(sum(pnls_list), 2),
            "win_rate_pct": round(len([p for p in pnls_list if p > 0]) / len(pnls_list) * 100.0, 2) if pnls_list else 0.0,
        }
        for reason, pnls_list in reason_map.items()
    }

    # Monthly P&L
    monthly: dict[str, float] = {}
    for t in closed:
        month_key = t.closed_at.strftime("%Y-%m")
        monthly[month_key] = round(monthly.get(month_key, 0.0) + t.realized_pnl_usd, 2)

    return {
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "avg_win_usd": round(avg_win, 2),
        "avg_loss_usd": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy_usd": round(expectancy, 2),
        "avg_r": round(avg_r, 2),
        "max_drawdown_usd": round(max_dd, 2),
        "recovery_factor": round(recovery, 2),
        "sharpe_like": round(sharpe, 2),
        "net_pnl_usd": round(net, 2),
        "long_trades": len(longs),
        "long_win_rate_pct": round(len(long_wins) / len(longs) * 100.0, 2) if longs else 0.0,
        "short_trades": len(shorts),
        "short_win_rate_pct": round(len(short_wins) / len(shorts) * 100.0, 2) if shorts else 0.0,
        "exit_reason_breakdown": strategy_breakdown,
        "monthly_pnl": monthly,
    }