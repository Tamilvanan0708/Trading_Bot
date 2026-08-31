"""
Tests for walk-forward splitting, Monte Carlo, bootstrap, and
regime/session/confluence/AI analyses.
"""

from datetime import datetime, timedelta, timezone

from app.data.models import Candle
from app.research.bootstrap import bootstrap_confidence_intervals
from app.research.monte_carlo import monte_carlo_simulation
from app.research.session import session_analysis
from app.research.walkforward import split_windows


def _candles(n=1000, start=None):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(timestamp=start + timedelta(minutes=15 * i), open=4600.0, high=4601.0,
               low=4599.0, close=4600.0, volume=10.0)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Walk-forward splitting
# ---------------------------------------------------------------------------

def test_split_windows_chronological():
    candles = _candles(n=20000)  # ~208 days
    windows = split_windows(candles, train_months=2, val_months=1, test_months=1, step_months=2)
    assert len(windows) >= 2
    for (tr_s, tr_e, va_s, va_e, te_s, te_e) in windows:
        # strictly increasing boundaries
        assert tr_s < tr_e <= va_s < va_e <= te_s < te_e
        # no future leak: each boundary is later than the previous
        assert tr_e == va_s
        assert va_e == te_s


def test_split_windows_insufficient_data():
    windows = split_windows(_candles(n=500), train_months=3, val_months=1, test_months=1)
    assert windows == []


def test_split_windows_no_overlap():
    candles = _candles(n=20000)
    windows = split_windows(candles, train_months=2, val_months=1, test_months=1, step_months=1)
    # Train and test windows of the same iteration must not overlap
    for (tr_s, tr_e, va_s, va_e, te_s, te_e) in windows:
        assert te_s >= va_e >= tr_e


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

def test_monte_carlo_reproducible():
    rs = [-1.0, -1.0, 0.5, 1.0, 2.0, 3.0, -1.0, 1.5]
    a = monte_carlo_simulation(rs, simulations=500, seed=42)
    b = monte_carlo_simulation(rs, simulations=500, seed=42)
    assert a == b  # deterministic with same seed


def test_monte_carlo_empty():
    mc = monte_carlo_simulation([], simulations=100)
    assert mc["simulations"] == 100
    assert mc["median_equity"] == 10000.0


def test_monte_carlo_never_predicts_future():
    """Monte Carlo must produce a distribution, not a point prediction."""
    rs = [-1.0] * 5 + [1.0, 2.0, 3.0]
    mc = monte_carlo_simulation(rs, simulations=1000, seed=7)
    assert mc["p5_equity"] <= mc["p95_equity"]
    assert 0.0 <= mc["probability_negative_return_pct"] <= 100.0


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def test_bootstrap_reproducible():
    rs = [-1.0, -1.0, 0.5, 1.0, 2.0, 3.0, -1.0, 1.5]
    a = bootstrap_confidence_intervals(rs, samples=200, seed=1)
    b = bootstrap_confidence_intervals(rs, samples=200, seed=1)
    assert a == b


def test_bootstrap_ci_bounds():
    rs = [-1.0, -1.0, 0.5, 1.0, 2.0, 3.0, -1.0, 1.5]
    ci = bootstrap_confidence_intervals(rs, samples=200, seed=2)
    for key in ("win_rate", "mean_r", "median_r"):
        assert ci[key]["lo"] <= ci[key]["point"] <= ci[key]["hi"]


def test_bootstrap_empty():
    ci = bootstrap_confidence_intervals([])
    assert ci["win_rate"]["n"] == 0


# ---------------------------------------------------------------------------
# Session analysis
# ---------------------------------------------------------------------------

def test_session_analysis_buckets():
    from app.backtesting.models import SimulatedTrade
    from app.core.constants import SignalDirection, StrategyType, TradeState
    trades = [
        SimulatedTrade(trade_id="1", direction=SignalDirection.LONG, strategy=StrategyType.CONFLUENCE,
                       entry_time=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc), entry_price=4600.0,
                       stop_loss=4590.0, take_profit_1=4615.0, take_profit_2=4630.0, take_profit_3=4650.0,
                       lot_size=0.1, risk_usd=100.0, pnl_usd=200.0, pnl_r=2.0, exit_reason="TP2_HIT",
                       state=TradeState.CLOSED),  # ASIA
        SimulatedTrade(trade_id="2", direction=SignalDirection.SHORT, strategy=StrategyType.CONFLUENCE,
                       entry_time=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc), entry_price=4600.0,
                       stop_loss=4610.0, take_profit_1=4590.0, take_profit_2=4580.0, take_profit_3=4570.0,
                       lot_size=0.1, risk_usd=100.0, pnl_usd=-100.0, pnl_r=-1.0, exit_reason="STOP_LOSS_HIT",
                       state=TradeState.CLOSED),  # LONDON
        SimulatedTrade(trade_id="3", direction=SignalDirection.LONG, strategy=StrategyType.CONFLUENCE,
                       entry_time=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc), entry_price=4600.0,
                       stop_loss=4590.0, take_profit_1=4615.0, take_profit_2=4630.0, take_profit_3=4650.0,
                       lot_size=0.1, risk_usd=100.0, pnl_usd=150.0, pnl_r=1.5, exit_reason="TP2_HIT",
                       state=TradeState.CLOSED),  # LONDON_NY + NEW_YORK overlap
        SimulatedTrade(trade_id="4", direction=SignalDirection.SHORT, strategy=StrategyType.CONFLUENCE,
                       entry_time=datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc), entry_price=4600.0,
                       stop_loss=4610.0, take_profit_1=4590.0, take_profit_2=4580.0, take_profit_3=4570.0,
                       lot_size=0.1, risk_usd=100.0, pnl_usd=-100.0, pnl_r=-1.0, exit_reason="STOP_LOSS_HIT",
                       state=TradeState.CLOSED),  # NEW_YORK only
    ]
    result = session_analysis(trades)
    assert "ASIA" in result
    assert "LONDON" in result
    assert result["ASIA"]["trades"] == 1
    assert result["ASIA"]["net_r"] == 2.0
    assert "LONDON_NY" in result
    assert "NEW_YORK" in result
    assert result["LONDON_NY"]["trades"] == 1
    assert result["NEW_YORK"]["trades"] == 1


def test_confluence_buckets_exist():
    from app.backtesting.models import SimulatedTrade
    from app.core.constants import SignalDirection, StrategyType, TradeState
    from app.research.confluence import confluence_bucket_analysis
    trades = [
        SimulatedTrade(trade_id=f"t{i}", direction=SignalDirection.LONG, strategy=StrategyType.CONFLUENCE,
                       entry_time=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc), entry_price=4600.0,
                       stop_loss=4590.0, take_profit_1=4615.0, take_profit_2=4630.0, take_profit_3=4650.0,
                       lot_size=0.1, risk_usd=100.0, pnl_usd=100.0 if i % 2 else -100.0,
                       pnl_r=1.0 if i % 2 else -1.0, exit_reason="TP2_HIT" if i % 2 else "STOP_LOSS_HIT",
                       state=TradeState.CLOSED, confidence_score=score)
        for i, score in enumerate([45.0, 55.0, 65.0, 72.0, 78.0, 85.0, 95.0])
    ]
    result = confluence_bucket_analysis(trades)
    assert "75–79" in result
    assert "80–89" in result
    assert "90–100" in result
    assert result["90–100"]["trades"] == 1