"""
Unit tests for research metrics, R-multiple distribution, and strategy
classification.
"""

from datetime import datetime, timedelta, timezone

from app.backtesting.models import SimulatedTrade
from app.core.constants import SignalDirection, StrategyType, TradeState
from app.research.classification import classify_strategy
from app.research.metrics import compute_extended_metrics
from app.research.rmultiple import r_multiple_distribution


def _trade(i, pnl, r, reason="TP2_HIT", direction="LONG", score=80.0, entry=None, exit=None):
    entry = entry or datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc) + timedelta(days=i)
    return SimulatedTrade(
        trade_id=f"t{i}", direction=SignalDirection(direction), strategy=StrategyType.CONFLUENCE,
        entry_time=entry, entry_price=4600.0, stop_loss=4590.0,
        take_profit_1=4615.0, take_profit_2=4630.0, take_profit_3=4650.0,
        lot_size=0.1, risk_usd=100.0, pnl_usd=pnl, pnl_r=r, exit_reason=reason,
        state=TradeState.CLOSED, confidence_score=score,
        exit_time=exit or (entry + timedelta(hours=4)),
    )


def test_extended_metrics_basic():
    trades = [
        _trade(0, 250.0, 2.5),      # win TP2
        _trade(1, -100.0, -1.0, "STOP_LOSS_HIT"),
        _trade(2, 450.0, 4.5, "TP3_HIT"),
        _trade(3, -100.0, -1.0, "STOP_LOSS_HIT"),
    ]
    m = compute_extended_metrics(trades)
    assert m["total_trades"] == 4
    assert m["winning_trades"] == 2
    assert m["losing_trades"] == 2
    assert m["win_rate_pct"] == 50.0
    assert m["net_profit_usd"] == 500.0
    assert m["profit_factor"] == 3.5  # 700 / 200
    assert m["avg_r"] == 1.25  # (2.5 - 1 + 4.5 - 1)/4
    assert m["tp3_hit_rate_pct"] == 25.0
    assert m["sl_hit_rate_pct"] == 50.0


def test_extended_metrics_empty():
    m = compute_extended_metrics([])
    assert m["total_trades"] == 0
    assert m["profit_factor"] == 0.0


def test_extended_metrics_tp1_rate_includes_tp2_tp3():
    trades = [
        _trade(0, 100.0, 1.0, "TP2_HIT"),
        _trade(1, 100.0, 1.0, "TP3_HIT"),
        _trade(2, -100.0, -1.0, "STOP_LOSS_HIT"),
    ]
    m = compute_extended_metrics(trades)
    assert m["tp1_hit_rate_pct"] == 66.67  # reached TP1 in both TP2/TP3 wins


def test_rmultiple_distribution():
    rs = [-1.0, -1.0, 0.5, 1.0, 2.0, 3.0, -1.0, 1.5]
    d = r_multiple_distribution(rs)
    assert d["count"] == 8
    assert d["p50"] is not None
    assert d["positive_count"] == 5
    assert d["positive_pct"] == 62.5
    assert d["best_r"] == 3.0
    assert d["worst_r"] == -1.0
    assert d["p5"] <= d["p95"]


def test_rmultiple_percentile():
    from app.research.rmultiple import percentile_sorted
    assert percentile_sorted([1, 2, 3, 4], 50) == 2.5
    assert percentile_sorted([5], 50) == 5.0
    assert percentile_sorted([], 50) == 0.0


class _FakeResult:
    def __init__(self, trades):
        self.trades = trades


class _FakeWindow:
    def __init__(self, test_metrics, trades=None):
        self.test_metrics = test_metrics
        self.test_result = _FakeResult(trades or [])


def _trades(*rs):
    return [_trade(i, pnl, r) for i, (pnl, r) in enumerate(rs)]


def test_classify_robust():
    windows = [
        _FakeWindow({"error": None, "total_trades": 20, "expectancy_r": 0.4, "profit_factor": 1.6, "max_drawdown_pct": 10.0},
                    _trades((150, 1.5), (100, 1.0), (200, 2.0), (80, 0.8))),
        _FakeWindow({"error": None, "total_trades": 15, "expectancy_r": 0.3, "profit_factor": 1.4, "max_drawdown_pct": 8.0},
                    _trades((120, 1.2), (180, 1.8), (-80, -0.8), (90, 0.9))),
        _FakeWindow({"error": None, "total_trades": 18, "expectancy_r": 0.5, "profit_factor": 1.8, "max_drawdown_pct": 12.0},
                    _trades((90, 0.9), (160, 1.6), (50, 0.5), (60, 0.6))),
    ]
    c = classify_strategy(windows)
    assert c.grade == "ROBUST"


def test_classify_failed_negative_expectancy():
    windows = [
        _FakeWindow({"error": None, "total_trades": 20, "expectancy_r": -0.2, "profit_factor": 0.8, "max_drawdown_pct": 20.0},
                    _trades((-100, -1.0), (-100, -1.0), (60, 0.6))),
        _FakeWindow({"error": None, "total_trades": 15, "expectancy_r": -0.1, "profit_factor": 0.9, "max_drawdown_pct": 15.0},
                    _trades((-100, -1.0), (-100, -1.0), (80, 0.8))),
    ]
    c = classify_strategy(windows)
    assert c.grade == "FAILED"


def test_classify_failed_lookahead():
    windows = [_FakeWindow({"error": None, "total_trades": 20, "expectancy_r": 0.5, "profit_factor": 2.0, "max_drawdown_pct": 5.0},
                           _trades((100, 1.0), (200, 2.0)))]
    c = classify_strategy(windows, lookahead_detected=True)
    assert c.grade == "FAILED"


def test_classify_inconclusive_no_windows():
    c = classify_strategy([])
    assert c.grade == "INCONCLUSIVE"


def test_classify_failed_when_pooled_negative_despite_positive_window_average():
    """A strategy must NOT be upgraded when pooled OOS expectancy is negative,
    even if the simple average of small windows is slightly positive."""
    windows = [
        _FakeWindow({"error": None, "total_trades": 100, "expectancy_r": -0.1, "profit_factor": 0.95, "max_drawdown_pct": 20.0},
                    _trades((-100, -1.0), (-100, -1.0), (-100, -1.0), (-100, -1.0), (60, 0.6))),
        _FakeWindow({"error": None, "total_trades": 5, "expectancy_r": 0.6, "profit_factor": 2.0, "max_drawdown_pct": 5.0},
                    _trades((100, 1.0), (200, 2.0), (50, 0.5), (-30, -0.3))),
    ]
    c = classify_strategy(windows)
    assert c.grade == "FAILED"