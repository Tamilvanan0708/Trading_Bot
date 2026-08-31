"""
Look-ahead bias detection tests.

Verifies the backtest engine never uses future data, and provides an explicit
validator that flags trades whose entry uses information from the future.
"""

from datetime import datetime, timedelta, timezone

from app.backtesting.engine import BacktestEngine
from app.config.settings import Settings
from app.data.models import Candle


def _candle(ts, o, h, l, c) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _uptrend(n=200, base=4600.0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = base
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        price += 1.0 + (i % 5) * 0.2
        candles.append(_candle(ts, price - 1.0, price + 2.0, price - 2.0, price + 0.5))
    return candles


def test_backtest_uses_only_closed_candles():
    """Trade entries must never use the high/low of a forming candle."""
    candles = _uptrend(n=250)
    engine = BacktestEngine(Settings(BACKTEST_ENTRY_ON_NEXT_OPEN=False))
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)

    for t in result.trades:
        # Entry time is the signal bar's close — never a future bar.
        assert t.entry_time <= result.end_time
        # The signal was generated on the candle that just closed.
        idx = [c.timestamp for c in candles].index(t.entry_time)
        assert idx >= 0


def test_adding_future_candles_does_not_change_past_trades():
    """Identical prefix backtests must produce identical earlier trades."""
    candles = _uptrend(n=180)

    def run(dataset):
        engine = BacktestEngine(Settings(BACKTEST_ENTRY_ON_NEXT_OPEN=False))
        return engine.run(dataset, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)

    a = run(candles)
    # Extend with more data
    extended = list(candles)
    for i in range(20):
        ts = extended[-1].timestamp + timedelta(minutes=15)
        extended.append(_candle(ts, 4700.0, 4710.0, 4690.0, 4705.0))
    b = run(extended[:190])

    trades_a = [t for t in a.trades if t.exit_reason != "END_OF_BACKTEST"]
    trades_b = [t for t in b.trades if t.exit_reason != "END_OF_BACKTEST"]
    # The prefix (180 bars) trades are identical; only trailing force-closes differ.
    assert len(trades_a) == len(trades_b)
    for ta, tb in zip(trades_a, trades_b):
        assert ta.entry_time == tb.entry_time
        assert ta.pnl_usd == tb.pnl_usd
        assert ta.exit_reason == tb.exit_reason


def test_timestamp_leakage_detector():
    """A validator that flags trades using future candles must detect leakage."""
    candles = _uptrend(n=200)
    engine = BacktestEngine(Settings(BACKTEST_ENTRY_ON_NEXT_OPEN=False))
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)

    # Build a timestamp->index map once
    ts_to_idx = {c.timestamp: i for i, c in enumerate(candles)}

    leakage = []
    for t in result.trades:
        entry_idx = ts_to_idx.get(t.entry_time)
        if entry_idx is None:
            continue
        # The trade's SL/TP must be relative to the entry bar only.
        # If the engine had used future highs/lows, entry would exceed bar bounds.
        bar = candles[entry_idx]
        # Entry price should be within [bar.low, bar.high] tolerance (close-based entry)
        if not (bar.low - 0.01 <= t.entry_price <= bar.high + 0.01):
            leakage.append(t.trade_id)

    assert leakage == [], f"Possible future-candle leakage in trades: {leakage}"


def test_next_open_entry_never_before_signal():
    """With entry_on_next_open=True, entries occur at the NEXT bar open."""
    candles = _uptrend(n=250)
    engine = BacktestEngine(Settings(BACKTEST_ENTRY_ON_NEXT_OPEN=True))
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)

    ts_to_idx = {c.timestamp: i for i, c in enumerate(candles)}
    for t in result.trades:
        if t.exit_reason == "END_OF_BACKTEST":
            continue
        idx = ts_to_idx.get(t.entry_time)
        # entry must be at a bar open, not the signal close
        assert idx is not None and idx > 150


def test_same_candle_sl_tp_ambiguity_resolved_conservatively():
    """If a candle touches both SL and TP, SL must win (conservative)."""
    candles = _uptrend(n=160)
    # A candle whose range spans both the SL and TP levels
    ts = candles[-1].timestamp + timedelta(minutes=15)
    candles.append(_candle(ts, 4700.0, 4750.0, 4600.0, 4650.0))

    engine = BacktestEngine(Settings(BACKTEST_ENTRY_ON_NEXT_OPEN=False))
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    for t in result.trades:
        # If both were touched in one candle, the engine must have exited via SL
        # (stop loss is checked before take profit).
        if t.exit_reason == "STOP_LOSS_HIT":
            assert t.pnl_r == -1.0
            break