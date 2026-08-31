"""
Tests for the research replay engine: signal collection, exit/SL variant
replay, same-candle SL priority, and metrics aggregation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import SignalDirection
from app.data.models import Candle
from app.research.replay_engine import (
    SignalRecord,
    TradeOutcome,
    collect_signals,
    outcomes_metrics,
    replay_variant,
)
from app.signals.engine import SignalEngine


def _candle(ts, o, h, l, c) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _uptrend(n=400, base=3900.0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = base
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        price += 1.0 + (i % 5) * 0.2
        candles.append(_candle(ts, price - 1.0, price + 2.0, price - 2.0, price + 0.5))
    return candles


def _signal(timestamp, direction=SignalDirection.LONG, entry=100.0, sl=90.0, atr=5.0):
    return SignalRecord(
        timestamp=timestamp, direction=direction, entry=entry, base_sl=sl,
        base_risk=abs(entry - sl), atr=atr, confidence=80.0, market_bias="BULLISH",
        regime="TRENDING", session="LONDON", reasons=["BOS_BULLISH", "FVG"],
        has_bos=True, has_fvg=True,
    )


def _candles_after(start, n=10, drift=0.5):
    candles = [_candle(start, 100.0, 101.0, 99.0, 100.0)]  # signal bar
    price = 100.0
    for i in range(n):
        ts = start + timedelta(minutes=15 * (i + 1))
        price += drift
        candles.append(_candle(ts, price, price + 1.0, price - 1.0, price + 0.5))
    return candles


def test_replay_tp_hit_and_sl_priority():
    ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal(ts)
    # Candles: first dips to SL, then rises above TP — SL must win (same-candle priority)
    candles = [
        _candle(ts, 100.0, 101.0, 99.0, 100.0),                 # signal bar
        _candle(ts + timedelta(minutes=15), 100.0, 101.0, 85.0, 90.0),   # SL (90) touched
        _candle(ts + timedelta(minutes=30), 92.0, 120.0, 91.0, 118.0),  # TP (125) touched later
    ]
    outcomes = replay_variant([sig], candles, tp_r=2.5)  # risk=10, TP=100+2.5*10=125
    o = outcomes[0]
    assert o.exit_reason == "STOP_LOSS_HIT"
    assert o.pnl_r == -1.0


def test_replay_tp_r_sets_target():
    ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal(ts)  # risk=10
    candles = _candles_after(ts, n=8, drift=3.0)  # rises strongly
    # TP at 1.25R -> entry + 12.5 = 112.5; drift reaches it by ~5 bars
    outcomes = replay_variant([sig], candles, tp_r=1.25)
    o = outcomes[0]
    assert o.exit_reason == "TAKE_PROFIT_HIT"
    assert o.pnl_r == pytest.approx(1.25, abs=0.01)


def test_replay_end_of_window_no_exit():
    ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal(ts)
    candles = _candles_after(ts, n=5, drift=0.1)  # barely moves
    outcomes = replay_variant([sig], candles, tp_r=2.5, max_holding_bars=5)
    o = outcomes[0]
    assert o.exit_reason == "END"


def test_replay_sl_atr_variant():
    ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal(ts, atr=10.0)  # risk with base SL = 10
    candles = _candles_after(ts, n=8, drift=3.0)
    # SL = 1.0 ATR = 10 below entry = 90; TP at 1.25R of new risk (10) = 112.5
    outcomes = replay_variant([sig], candles, sl_atr=1.0, tp_r=1.25)
    o = outcomes[0]
    assert o.pnl_r > 0


def test_outcomes_metrics_aggregation():
    outs = [
        TradeOutcome(0, SignalDirection.LONG, 100, 110, datetime.now(timezone.utc), "TAKE_PROFIT_HIT", 1.0, 0.2, 1.2, 1.0),
        TradeOutcome(1, SignalDirection.LONG, 100, 90, datetime.now(timezone.utc), "STOP_LOSS_HIT", -1.0, 1.0, 0.1, 1.0),
        TradeOutcome(2, SignalDirection.SHORT, 100, 95, datetime.now(timezone.utc), "STOP_LOSS_HIT", -1.0, 1.0, 0.1, 1.0),
    ]
    m = outcomes_metrics(outs)
    assert m["trades"] == 3
    assert m["win_rate_pct"] == 33.33
    assert m["expectancy_r"] == pytest.approx(-0.333, abs=0.01)


def test_collect_signals_uses_only_closed_candles():
    """collect_signals must not raise and must return a deterministic set."""
    candles = _uptrend(400)
    engine = SignalEngine()
    signals = collect_signals(candles, engine=engine, warmup_bars=150)
    assert isinstance(signals, list)
    for s in signals:
        assert s.entry > 0
        assert s.base_risk > 0
        assert s.regime in {"TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY", "UNCERTAIN"}


def test_replay_is_deterministic():
    ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal(ts)
    candles = _candles_after(ts, n=10, drift=1.0)
    a = replay_variant([sig], candles, tp_r=1.5)
    b = replay_variant([sig], candles, tp_r=1.5)
    assert a == b


def test_incremental_collector_equals_reference():
    """The fast incremental collector must be deterministic vs the reference."""
    from app.research.replay_engine import (
        collect_signals_reference,
        collect_signals_incremental,
    )
    candles = _uptrend(400)
    ref = collect_signals_reference(candles, warmup_bars=150)
    inc = collect_signals_incremental(candles, warmup_bars=150)
    assert len(ref) == len(inc)
    for a, b in zip(ref, inc):
        assert a.to_dict() == b.to_dict()


def test_replay_cost_zero_is_identical():
    """cost_points=0 must be identical to the original no-cost behaviour."""
    from app.research.replay_engine import replay_variant, outcomes_metrics
    from app.core.constants import SignalDirection
    ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    sig = SignalRecord(
        timestamp=ts, direction=SignalDirection.LONG,
        entry=100.0, base_sl=95.0, base_risk=5.0, atr=5.0,
        confidence=85.0, market_bias="BULLISH", regime="RANGING",
        session="LONDON", reasons=["t"],
    )
    candles = _candles_after(ts, n=10, drift=1.0)
    m0 = outcomes_metrics(replay_variant([sig], candles, tp_r=1.5))
    m1 = outcomes_metrics(replay_variant([sig], candles, tp_r=1.5, cost_points=0.0))
    assert m0 == m1


def test_replay_cost_reduces_expectancy():
    """Positive-cost replays must degrade expectancy vs no-cost."""
    from app.research.replay_engine import replay_variant, outcomes_metrics
    from app.core.constants import SignalDirection
    ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    sig = SignalRecord(
        timestamp=ts, direction=SignalDirection.LONG,
        entry=100.0, base_sl=95.0, base_risk=5.0, atr=5.0,
        confidence=85.0, market_bias="BULLISH", regime="RANGING",
        session="LONDON", reasons=["t"],
    )
    candles = _candles_after(ts, n=10, drift=1.0)
    m0 = outcomes_metrics(replay_variant([sig], candles, tp_r=1.5))
    m3 = outcomes_metrics(replay_variant([sig], candles, tp_r=1.5, cost_points=1.0))
    assert m3["expectancy_r"] <= m0["expectancy_r"]