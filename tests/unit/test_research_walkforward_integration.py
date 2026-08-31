"""
Walk-forward integration smoke test using a fast stub engine.
Verifies the orchestration (window splitting, chronological slicing, metric
computation) without the slow full pipeline.
"""

from datetime import datetime, timedelta, timezone

from app.backtesting.models import BacktestResult, PerformanceSummary
from app.data.models import Candle
from app.research.walkforward import run_walk_forward


def _candles(n=12000):  # ~125 days
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = 4600.0
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        price += 0.5
        candles.append(Candle(timestamp=ts, open=price, high=price + 1.0,
                              low=price - 1.0, close=price + 0.5, volume=10.0))
    return candles


class _StubEngine:
    """Deterministic fast engine returning a fixed empty result."""
    def run(self, candles, initial_balance=10000.0, risk_percent=1.0,
            warmup_bars=150, **kwargs) -> BacktestResult:
        assert all(candles[i].timestamp <= candles[i + 1].timestamp for i in range(len(candles) - 1))
        return BacktestResult(
            symbol="XAUUSD",
            start_time=candles[warmup_bars].timestamp if len(candles) > warmup_bars else candles[0].timestamp,
            end_time=candles[-1].timestamp,
            summary=PerformanceSummary(
                initial_balance=initial_balance, final_balance=initial_balance,
                net_profit_usd=0.0, net_return_pct=0.0, total_trades=0,
                winning_trades=0, losing_trades=0, win_rate_pct=0.0, profit_factor=0.0,
                max_drawdown_usd=0.0, max_drawdown_pct=0.0, expectancy_r=0.0,
                avg_win_usd=0.0, avg_loss_usd=0.0, long_trades_count=0,
                long_win_rate_pct=0.0, short_trades_count=0, short_win_rate_pct=0.0,
            ),
            trades=[],
        )


def test_walk_forward_orchestration():
    candles = _candles(12000)
    windows = run_walk_forward(
        candles,
        train_months=1, val_months=1, test_months=1, step_months=1,
        warmup_bars=150,
        engine_factory=_StubEngine,
    )
    assert len(windows) >= 1
    wr = windows[0]
    assert "total_trades" in wr.train_metrics
    assert "total_trades" in wr.val_metrics
    assert "total_trades" in wr.test_metrics
    # Strictly increasing, non-overlapping boundaries (no future leakage).
    assert wr.train_end <= wr.val_start < wr.val_end <= wr.test_start < wr.test_end
    # The stub receives chronologically sorted windows only.
    assert all(wr.train_start <= c.timestamp < wr.train_end for c in candles if wr.train_start <= c.timestamp < wr.train_end)


def test_walk_forward_insufficient_data():
    windows = run_walk_forward(
        _candles(500),
        train_months=3, val_months=1, test_months=1,
        engine_factory=_StubEngine,
    )
    assert windows == []


def test_data_fetch_labels_real():
    """The research data file must be explicitly labelled REAL DATA."""
    import json
    import os
    path = "data/research/xauusd_15m_real.json"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        assert "REAL DATA" in payload.get("data_label", "")
        assert payload.get("symbol") == "XAUUSDT"
        assert payload.get("timeframe") == "15m"