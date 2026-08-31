"""
Regression tests: the pure-Python fast resampler must produce results
numerically identical to the pandas reference implementation.
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import TimeFrame
from app.data.models import Candle
from app.data.timeframe_resampler import (
    resample_candles,
    resample_candles_pandas,
)


def _random_candles(n=500, seed=7, start=None):
    rng = random.Random(seed)
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = 4000.0
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        o = price + rng.uniform(-2, 2)
        h = max(o, o + rng.uniform(0, 3))
        l = min(o, o - rng.uniform(0, 3))
        c = (h + l) / 2
        price = c
        candles.append(Candle(timestamp=ts, open=round(o, 2), high=round(h, 2),
                              low=round(l, 2), close=round(c, 2),
                              volume=round(rng.uniform(1, 100), 2)))
    return candles


@pytest.mark.parametrize("tf", [TimeFrame.M15, TimeFrame.M30, TimeFrame.H1, TimeFrame.H4, TimeFrame.D1])
def test_fast_resampler_equals_pandas(tf):
    candles = _random_candles(800, seed=11)
    fast = resample_candles(candles, tf)
    ref = resample_candles_pandas(candles, tf)
    assert len(fast) == len(ref), f"{tf}: count mismatch {len(fast)} vs {len(ref)}"
    for a, b in zip(fast, ref):
        assert a.timestamp == b.timestamp, f"{tf}: ts {a.timestamp} != {b.timestamp}"
        assert a.open == b.open, f"{tf}: open"
        assert a.high == b.high, f"{tf}: high"
        assert a.low == b.low, f"{tf}: low"
        assert a.close == b.close, f"{tf}: close"
        assert abs(a.volume - b.volume) < 1e-6, f"{tf}: volume"


def test_fast_resampler_preserves_order():
    candles = _random_candles(300, seed=3)
    for tf in [TimeFrame.M30, TimeFrame.H1, TimeFrame.H4]:
        res = resample_candles(candles, tf)
        ts = [c.timestamp for c in res]
        assert ts == sorted(ts)


def test_fast_resampler_empty_and_single():
    assert resample_candles([], TimeFrame.H1) == []
    single = _random_candles(1, seed=1)
    assert len(resample_candles(single, TimeFrame.H1)) == 1


def test_fast_resampler_volume_sum_and_ohlc():
    candles = _random_candles(30, seed=5)
    res = resample_candles(candles, TimeFrame.M30)
    # 30 15m candles -> 15 x 30m buckets
    assert len(res) == 15
    # Each 30m bucket = 2 x 15m candles
    for r in res:
        assert r.volume == pytest.approx(sum(c.volume for c in candles
                                             if c.timestamp >= r.timestamp
                                             and c.timestamp < r.timestamp + timedelta(minutes=30)), abs=1e-6)


def test_backtest_results_unchanged_with_fast_resampler():
    """A full backtest must produce identical trades after the optimization."""
    import app.backtesting.engine as bt_mod
    import app.data.timeframe_resampler as mod
    from app.backtesting.engine import BacktestEngine
    from app.data.timeframe_resampler import resample_candles_pandas as ref

    candles = _random_candles(400, seed=9)
    engine_a = BacktestEngine()
    res_a = engine_a.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)

    # Force the engine to use the reference (pandas) resampling path.
    original = mod.resample_candles

    class _RefResampler:
        """IncrementalResampler-compatible wrapper around the pandas reference."""
        def __init__(self):
            self._count = 0

        def add(self, candle):
            self._count += 1

        def series(self, tf, tail):
            prefix = candles[: self._count]
            return ref(prefix, tf)[-tail:]

    mod.resample_candles = ref
    original_inc = bt_mod.IncrementalResampler
    bt_mod.IncrementalResampler = _RefResampler
    try:
        engine_b = BacktestEngine()
        res_b = engine_b.run(candles, initial_balance=10000.0, risk_percent=1.0, warmup_bars=150)
    finally:
        mod.resample_candles = original
        bt_mod.IncrementalResampler = original_inc

    assert len(res_a.trades) == len(res_b.trades)
    for ta, tb in zip(res_a.trades, res_b.trades):
        assert ta.entry_price == tb.entry_price
        assert ta.exit_reason == tb.exit_reason
        assert ta.pnl_usd == tb.pnl_usd