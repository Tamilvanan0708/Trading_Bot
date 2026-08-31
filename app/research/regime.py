"""
Market-regime performance breakdown.

Each trade is assigned the regime detected over the candle series up to its
entry time (no future information).  Analysis only — no automatic filter.
"""

from collections import defaultdict

from app.backtesting.models import SimulatedTrade
from app.data.models import Candle
from app.market_regime.detector import MarketRegimeDetector


def regime_analysis(
    trades: list[SimulatedTrade],
    candles: list[Candle],
    detector: MarketRegimeDetector = None,
) -> dict:
    """Group trades by the market regime active at their entry time."""
    detector = detector or MarketRegimeDetector()
    # Pre-sort candles for binary search
    candles = sorted(candles, key=lambda c: c.timestamp)

    groups: dict[str, list[SimulatedTrade]] = defaultdict(list)
    for t in trades:
        if not t.entry_time:
            continue
        # candles up to and including entry time
        upto = [c for c in candles if c.timestamp <= t.entry_time]
        regime = detector.analyze(upto).regime.value
        groups[regime].append(t)

    result = {}
    for name, group in sorted(groups.items()):
        rs = [t.pnl_r or 0.0 for t in group]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
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
        result[name] = {
            "trades": len(group),
            "win_rate_pct": round(len(wins) / len(group) * 100.0, 2) if group else 0.0,
            "expectancy_r": round(sum(rs) / len(rs), 2) if rs else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0,
            "net_r": round(sum(rs), 2),
            "max_drawdown_usd": round(dd, 2),
        }
    return result