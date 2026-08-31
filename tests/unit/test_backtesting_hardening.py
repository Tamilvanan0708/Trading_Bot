"""
Backtesting hardening tests: SL/TP priority, costs, look-ahead safety,
and candle data robustness.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtesting.engine import BacktestEngine
from app.config.settings import Settings
from app.core.constants import (
    MarketBias,
    SignalDirection,
    SignalQuality,
    StrategyType,
)
from app.data.models import Candle
from app.signals.models import SignalPayload


def _candle(ts: datetime, o, h, l, c, v=100.0) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def make_flat_history(n=160, base=2650.0, step=0.0):
    """Synthetic neutral history with small oscillation."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        o = base + (step * i)
        c = base + (step * i) + (1.0 if i % 2 else -1.0)
        h = max(o, c) + 2.0
        l = min(o, c) - 2.0
        candles.append(_candle(ts, o, h, l, c))
    return candles


def make_fixed_signal(direction=SignalDirection.LONG):
    """A fixed tradable signal used to deterministically exercise the engine."""
    if direction == SignalDirection.SHORT:
        return SignalPayload(
            instrument="XAUUSD",
            direction=SignalDirection.SHORT,
            strategy=StrategyType.SMC,
            timeframe="15m",
            entry=2650.0,
            stop_loss=2660.0,        # above entry
            take_profit_1=2640.0,
            take_profit_2=2620.0,    # main target, below entry
            take_profit_3=2600.0,
            risk_reward=3.0,
            confidence_score=85.0,
            signal_quality=SignalQuality.STRONG,
            market_bias=MarketBias.BEARISH,
            reasons=["fixed test signal"],
            invalidation_conditions=[],
        )
    return SignalPayload(
        instrument="XAUUSD",
        direction=SignalDirection.LONG,
        strategy=StrategyType.SMC,
        timeframe="15m",
        entry=2650.0,
        stop_loss=2640.0,        # below entry
        take_profit_1=2665.0,
        take_profit_2=2680.0,    # main target, above entry
        take_profit_3=2700.0,
        risk_reward=3.0,
        confidence_score=85.0,
        signal_quality=SignalQuality.STRONG,
        market_bias=MarketBias.BULLISH,
        reasons=["fixed test signal"],
        invalidation_conditions=[],
    )


@pytest.fixture
def bt_settings():
    return Settings(
        MAX_OPEN_TRADES=1,
        WEIGHT_HTF_BIAS=20,
        WEIGHT_MARKET_STRUCTURE=20,
        WEIGHT_SMC_CONFIRMATION=20,
        WEIGHT_FIB_CONFIRMATION=15,
        WEIGHT_LIQUIDITY=10,
        WEIGHT_ENTRY_CONFIRMATION=10,
        WEIGHT_RISK_REWARD=5,
        BACKTEST_SPREAD_POINTS=0.0,
        BACKTEST_SLIPPAGE_PCT=0.0,
        BACKTEST_TRANSACTION_COST_USD=0.0,
    )


def test_backtest_long_trade_sl_hit(bt_settings, monkeypatch):
    """Long trade: price drops to SL, exit reason STOP_LOSS_HIT, PnL = -risk."""
    candles = make_flat_history(n=200)
    # Append candles that crash below the SL (2640)
    for i in range(5):
        ts = candles[-1].timestamp + timedelta(minutes=15 * (i + 1))
        candles.append(_candle(ts, 2645.0, 2647.0, 2630.0, 2632.0))

    engine = BacktestEngine(bt_settings)
    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: make_fixed_signal(SignalDirection.LONG),
    )

    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    assert result.trades, "Expected at least one trade."
    trade = result.trades[0]
    assert trade.exit_reason == "STOP_LOSS_HIT"
    assert trade.pnl_usd < 0
    assert trade.pnl_r == -1.0


def test_backtest_short_trade_tp_hit(bt_settings, monkeypatch):
    candles = make_flat_history(n=200)
    for i in range(5):
        ts = candles[-1].timestamp + timedelta(minutes=15 * (i + 1))
        candles.append(_candle(ts, 2648.0, 2649.0, 2620.0, 2625.0))

    engine = BacktestEngine(bt_settings)
    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: make_fixed_signal(SignalDirection.SHORT),
    )
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    assert result.trades
    trade = result.trades[0]
    assert trade.exit_reason == "TP2_HIT"
    assert trade.pnl_usd > 0


def test_backtest_same_candle_sl_priority(bt_settings, monkeypatch):
    """Bar touches both SL and TP -> SL wins (conservative)."""
    candles = make_flat_history(n=200)
    # A single candle whose range spans both SL (2640) and TP2 (2680)
    ts = candles[-1].timestamp + timedelta(minutes=15)
    candles.append(_candle(ts, 2650.0, 2690.0, 2630.0, 2645.0))

    engine = BacktestEngine(bt_settings)
    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: make_fixed_signal(SignalDirection.LONG),
    )
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    assert result.trades
    trade = result.trades[0]
    assert trade.exit_reason == "STOP_LOSS_HIT"


def test_backtest_transaction_cost_reduces_pnl(bt_settings, monkeypatch):
    candles = make_flat_history(n=200)
    for i in range(5):
        ts = candles[-1].timestamp + timedelta(minutes=15 * (i + 1))
        candles.append(_candle(ts, 2648.0, 2649.0, 2620.0, 2625.0))

    engine = BacktestEngine(bt_settings)
    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: make_fixed_signal(SignalDirection.SHORT),
    )
    result_no_cost = engine.run(
        candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150,
        transaction_cost_usd=0.0,
    )
    result_with_cost = engine.run(
        candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150,
        transaction_cost_usd=10.0,
    )
    assert result_no_cost.trades and result_with_cost.trades
    pnl_no_cost = sum(t.pnl_usd for t in result_no_cost.trades if t.exit_reason == "TP2_HIT")
    pnl_with_cost = sum(t.pnl_usd for t in result_with_cost.trades if t.exit_reason == "TP2_HIT")
    assert pnl_with_cost < pnl_no_cost


def test_backtest_zero_lookahead_identical_prefix(bt_settings, monkeypatch):
    """Adding future candles must not change earlier trade outcomes."""
    # 152 flat bars for warmup, then 5 crash bars (indices 152-156)
    candles = make_flat_history(n=152)
    for i in range(5):
        ts = candles[-1].timestamp + timedelta(minutes=15 * (i + 1))
        candles.append(_candle(ts, 2645.0, 2647.0, 2630.0, 2632.0))

    engine_a = BacktestEngine(bt_settings)
    monkeypatch.setattr(
        engine_a.signal_engine, "generate_signal",
        lambda snapshot: make_fixed_signal(SignalDirection.LONG),
    )
    result_a = engine_a.run(candles[:157], initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)

    # Extend with additional flat candles after the crash
    candles_ext = list(candles[:157])
    for i in range(10):
        ts = candles_ext[-1].timestamp + timedelta(minutes=15 * (i + 1))
        candles_ext.append(_candle(ts, 2650.0, 2660.0, 2640.0, 2655.0))

    engine_b = BacktestEngine(bt_settings)
    monkeypatch.setattr(
        engine_b.signal_engine, "generate_signal",
        lambda snapshot: make_fixed_signal(SignalDirection.LONG),
    )
    result_b = engine_b.run(candles_ext, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)

    # The trade must close identically (STOP_LOSS_HIT at bar 156) in both runs.
    # Ignore END_OF_BACKTEST force-closes which differ by construction.
    closed_a = [t for t in result_a.trades if t.exit_time <= result_a.end_time and t.exit_reason != "END_OF_BACKTEST"]
    closed_b = [t for t in result_b.trades if t.exit_time <= result_a.end_time and t.exit_reason != "END_OF_BACKTEST"]
    assert len(closed_a) == len(closed_b)
    for ta, tb in zip(closed_a, closed_b):
        assert ta.exit_reason == tb.exit_reason
        assert ta.pnl_usd == tb.pnl_usd
        assert ta.exit_time == tb.exit_time


def test_backtest_insufficient_data_raises(bt_settings):
    engine = BacktestEngine(bt_settings)
    candles = make_flat_history(n=50)
    with pytest.raises(ValueError, match="warmup"):
        engine.run(candles, warmup_bars=150)


def test_backtest_equity_curve_tracked(bt_settings, monkeypatch):
    candles = make_flat_history(n=200)
    for i in range(5):
        ts = candles[-1].timestamp + timedelta(minutes=15 * (i + 1))
        candles.append(_candle(ts, 2645.0, 2647.0, 2630.0, 2632.0))

    engine = BacktestEngine(bt_settings)
    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: make_fixed_signal(SignalDirection.LONG),
    )
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    assert len(result.equity_curve) > 0
    assert "balance" in result.equity_curve[0]
    assert "equity" in result.equity_curve[0]


def test_backtest_spread_widens_sl(bt_settings, monkeypatch):
    """Spread makes SL harder to hit (SL widened for long)."""
    candles = make_flat_history(n=200)
    # Candle drops to 2642 (above raw SL 2640 but at/under effective SL 2641)
    ts = candles[-1].timestamp + timedelta(minutes=15)
    candles.append(_candle(ts, 2650.0, 2652.0, 2642.0, 2643.0))

    engine = BacktestEngine(bt_settings)
    monkeypatch.setattr(
        engine.signal_engine, "generate_signal",
        lambda snapshot: make_fixed_signal(SignalDirection.LONG),
    )
    result = engine.run(
        candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150,
        spread_points=2.0,  # effective SL = 2640 + 1 = 2641 -> low 2642 NOT below it
    )
    assert result.trades
    for t in result.trades:
        assert t.exit_reason != "STOP_LOSS_HIT"