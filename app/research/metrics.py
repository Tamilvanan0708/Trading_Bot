"""
Extended performance metrics for strategy research.

Computes a comprehensive, trade-level performance report from a list of
:class:`SimulatedTrade` objects, normalised where possible to R-multiples
rather than raw USD so results are comparable across risk sizes.
"""

import math
from collections import defaultdict

from app.backtesting.models import SimulatedTrade

CLOSING_REASONS = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "STOP_LOSS_HIT", "END_OF_BACKTEST"}


def _closed(trades: list[SimulatedTrade]) -> list[SimulatedTrade]:
    return [t for t in trades if t.exit_reason and t.exit_reason in CLOSING_REASONS]


def _sharpe(values: list[float], rf: float = 0.0) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return round((mean - rf) / std * math.sqrt(len(values)), 2)


def _sortino(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    downside = [v for v in values if v < 0]
    if not downside:
        return 0.0
    dd = math.sqrt(sum((v - mean) ** 2 for v in downside) / len(downside))
    if dd == 0:
        return 0.0
    return round(mean / dd * math.sqrt(len(values)), 2)


def compute_extended_metrics(trades: list[SimulatedTrade], initial_balance: float = 10000.0) -> dict:
    """Computes the full trade-level performance report."""
    closed = _closed(trades)
    n = len(closed)
    empty = {
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "win_rate_pct": 0.0, "gross_profit_usd": 0.0, "gross_loss_usd": 0.0,
        "net_profit_usd": 0.0, "profit_factor": 0.0, "avg_r": 0.0, "median_r": 0.0,
        "r_std": 0.0, "expectancy_r": 0.0, "max_drawdown_usd": 0.0,
        "max_drawdown_pct": 0.0, "recovery_factor": 0.0, "sharpe_like": 0.0,
        "sortino_like": 0.0, "consecutive_wins": 0, "consecutive_losses": 0,
        "avg_trade_hours": 0.0, "tp1_hit_rate_pct": 0.0, "tp2_hit_rate_pct": 0.0,
        "tp3_hit_rate_pct": 0.0, "sl_hit_rate_pct": 0.0, "exit_reason_breakdown": {},
        "monthly_pnl": {}, "daily_pnl": {}, "worst_day_usd": 0.0, "worst_month_usd": 0.0,
        "return_over_max_dd": 0.0, "profit_factor_over_max_dd": 0.0,
        "long_trades": 0, "long_win_rate_pct": 0.0,
        "short_trades": 0, "short_win_rate_pct": 0.0,
        "strategy_breakdown": {},
    }
    if n == 0:
        return empty

    pnls = [t.pnl_usd for t in closed]
    rs = [t.pnl_r or 0.0 for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = gross_profit - gross_loss
    win_rate = len(wins) / n * 100.0
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    # Drawdown from cumulative USD P&L (percentage relative to peak equity).
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        peak_equity = initial_balance + peak
        if peak_equity > 0:
            max_dd_pct = max(max_dd_pct, (peak - cum) / peak_equity * 100.0)

    total_risk = sum(t.risk_usd for t in closed if t.risk_usd > 0)
    max_dd_pct = round(max_dd_pct, 2)
    recovery = round(net / max_dd, 2) if max_dd > 0 else 0.0

    avg_r = round(sum(rs) / n, 2)
    median_r = round(sorted(rs)[n // 2], 2)
    r_std = round(math.sqrt(sum((r - sum(rs) / n) ** 2 for r in rs) / n), 2)

    # Consecutive wins / losses
    cw = cl = max_cw = max_cl = 0
    for r in rs:
        if r > 0:
            cw += 1
            cl = 0
        else:
            cl += 1
            cw = 0
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    # Duration
    durations = []
    for t in closed:
        if t.exit_time and t.entry_time:
            durations.append((t.exit_time - t.entry_time).total_seconds() / 3600.0)
    avg_hours = round(sum(durations) / len(durations), 2) if durations else 0.0

    # Exit reason rates
    reason_counts = defaultdict(int)
    for t in closed:
        reason_counts[t.exit_reason or "UNKNOWN"] += 1
    n_tp3 = reason_counts.get("TP3_HIT", 0)
    n_tp2 = reason_counts.get("TP2_HIT", 0)
    n_tp1 = n_tp2 + n_tp3  # reaching TP2/TP3 implies TP1 was hit
    n_sl = reason_counts.get("STOP_LOSS_HIT", 0)
    reason_breakdown = {k: {"count": v, "pct": round(v / n * 100.0, 2)} for k, v in reason_counts.items()}

    # Monthly / daily P&L
    monthly: dict[str, float] = defaultdict(float)
    daily: dict[str, float] = defaultdict(float)
    for t in closed:
        if t.exit_time:
            monthly[t.exit_time.strftime("%Y-%m")] += t.pnl_usd
            daily[t.exit_time.strftime("%Y-%m-%d")] += t.pnl_usd
    worst_day = round(min(daily.values()), 2) if daily else 0.0
    worst_month = round(min(monthly.values()), 2) if monthly else 0.0

    # Long / short split
    longs = [t for t in closed if t.direction.value == "LONG"]
    shorts = [t for t in closed if t.direction.value == "SHORT"]
    long_wr = round(sum(1 for t in longs if t.pnl_usd > 0) / len(longs) * 100.0, 2) if longs else 0.0
    short_wr = round(sum(1 for t in shorts if t.pnl_usd > 0) / len(shorts) * 100.0, 2) if shorts else 0.0

    # Strategy breakdown
    strat: dict[str, dict] = {}
    for t in closed:
        key = t.strategy.value if t.strategy else "UNKNOWN"
        s = strat.setdefault(key, {"trades": 0, "net_pnl_usd": 0.0, "wins": 0})
        s["trades"] += 1
        s["net_pnl_usd"] = round(s["net_pnl_usd"] + t.pnl_usd, 2)
        if t.pnl_usd > 0:
            s["wins"] += 1
    for key, s in strat.items():
        s["win_rate_pct"] = round(s["wins"] / s["trades"] * 100.0, 2) if s["trades"] else 0.0

    return {
        "total_trades": n,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "gross_profit_usd": round(gross_profit, 2),
        "gross_loss_usd": round(gross_loss, 2),
        "net_profit_usd": round(net, 2),
        "profit_factor": pf,
        "avg_r": avg_r,
        "median_r": median_r,
        "r_std": r_std,
        "expectancy_r": avg_r,
        "max_drawdown_usd": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "recovery_factor": recovery,
        "sharpe_like": _sharpe(rs),
        "sortino_like": _sortino(rs),
        "consecutive_wins": max_cw,
        "consecutive_losses": max_cl,
        "avg_trade_hours": avg_hours,
        "tp1_hit_rate_pct": round(n_tp1 / n * 100.0, 2),
        "tp2_hit_rate_pct": round(n_tp2 / n * 100.0, 2),
        "tp3_hit_rate_pct": round(n_tp3 / n * 100.0, 2),
        "sl_hit_rate_pct": round(n_sl / n * 100.0, 2),
        "exit_reason_breakdown": reason_breakdown,
        "monthly_pnl": {k: round(v, 2) for k, v in sorted(monthly.items())},
        "daily_pnl": {k: round(v, 2) for k, v in sorted(daily.items())},
        "worst_day_usd": worst_day,
        "worst_month_usd": worst_month,
        "return_over_max_dd": round(net / max_dd, 2) if max_dd > 0 else 0.0,
        "profit_factor_over_max_dd": round(pf / max_dd, 4) if max_dd > 0 else 0.0,
        "long_trades": len(longs),
        "long_win_rate_pct": long_wr,
        "short_trades": len(shorts),
        "short_win_rate_pct": short_wr,
        "strategy_breakdown": strat,
    }