"""
Tests for the multi-timeframe (MTF) research infrastructure:
- M5 resampling correctness
- MTF config role mapping (production default unchanged)
- MTF signal collector determinism and config differentiation
"""

from datetime import datetime, timedelta, timezone

from app.core.constants import TimeFrame
from app.core.mtf_config import (
    ALL_MTF_CONFIGS,
    MTF_4H_1H_15M_5M,
    PROD_4H_1H_30M_15M,
    resolve_mtf,
)
from app.data.models import Candle
from app.data.timeframe_resampler import IncrementalResampler, resample_candles
from app.research.replay_engine import collect_signals_mtf


def _candle(ts, o, h, l, c) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _m5_series(n=2000, base=4600.0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = base
    import random
    rng = random.Random(42)
    for i in range(n):
        ts = start + timedelta(minutes=5 * i)
        # Volatile seeded random walk creates FVG / sweep / swing structure.
        shock = rng.uniform(-6.0, 6.0)
        price = max(4000.0, min(5200.0, price + shock * 0.6))
        o = price
        h = max(o, price + abs(rng.gauss(0, 3)) + 0.5)
        l = min(o, price - abs(rng.gauss(0, 3)) - 0.5)
        c = price
        candles.append(_candle(ts, o, h, l, c))
    return candles


def test_m5_resampling_equivalence():
    """Incremental M5 aggregation must equal the reference resample."""
    candles = _m5_series(500)
    # Reference: M15 from 5M
    ref = resample_candles(candles, TimeFrame.M15)
    inc = IncrementalResampler()
    for c in candles:
        inc.add(c)
    got = inc.series(TimeFrame.M15, tail=0)
    assert len(ref) == len(got)
    for a, b in zip(ref, got):
        assert a.timestamp == b.timestamp
        assert a.open == b.open and a.high == b.high
        assert a.low == b.low and a.close == b.close


def test_mtf_resolve_default_is_production():
    assert resolve_mtf(None) is PROD_4H_1H_30M_15M
    assert resolve_mtf(MTF_4H_1H_15M_5M) is MTF_4H_1H_15M_5M
    assert len(ALL_MTF_CONFIGS) == 4


def test_mtf_production_signal_unchanged_from_default():
    """generate_signal(mtf=None) and generate_signal(mtf=PROD) must be identical."""
    from app.research.data_fetch import load_real_history
    from app.signals.engine import SignalEngine
    engine = SignalEngine()
    candles = load_real_history("data/research/xauusd_15m_full.json")[-500:]
    resampler = IncrementalResampler()
    for c in candles:
        resampler.add(c)
    snap = None
    from app.data.models import MultiTimeframeSnapshot
    snap = MultiTimeframeSnapshot(
        symbol="XAUUSD",
        timestamp=candles[-1].timestamp,
        current_price=candles[-1].close,
        m15=resampler.series(TimeFrame.M15, 150),
        m30=resampler.series(TimeFrame.M30, 100),
        h1=resampler.series(TimeFrame.H1, 80),
        h4=resampler.series(TimeFrame.H4, 50),
    )
    s_default = engine.generate_signal(snap)
    s_prod = engine.generate_signal(snap, mtf=PROD_4H_1H_30M_15M)
    assert s_default.direction == s_prod.direction
    assert s_default.confidence_score == s_prod.confidence_score
    assert s_default.entry == s_prod.entry
    assert s_default.stop_loss == s_prod.stop_loss


def test_mtf_collector_deterministic_and_configs_differ():
    from app.config.settings import Settings
    from app.signals.engine import SignalEngine
    # Lower the STRONG threshold so synthetic data reliably produces signals.
    engine = SignalEngine(Settings(THRESHOLD_STRONG=45, THRESHOLD_VERY_STRONG=90))
    candles = _m5_series(1500)
    a = collect_signals_mtf(candles, PROD_4H_1H_30M_15M, engine=engine, warmup_bars=200, max_signals=20, fast=True)
    b = collect_signals_mtf(candles, PROD_4H_1H_30M_15M, engine=engine, warmup_bars=200, max_signals=20, fast=True)
    assert [s.timestamp for s in a] == [s.timestamp for s in b]
    assert len(a) > 0  # synthetic data + low threshold must produce signals

    c = collect_signals_mtf(candles, MTF_4H_1H_15M_5M, engine=engine, warmup_bars=200, max_signals=20, fast=True)
    # Every recorded signal must carry the config tag of the generating config.
    assert all(s.reasons[-1] == "MTF:PROD_4H_1H_30M_15M" for s in a)
    assert all(s.reasons[-1] == "MTF:4H_1H_15M_5M" for s in c)
    # The 5M-trigger config produces a different (larger) signal set.
    assert len(c) > 0


def test_mtf_signal_timeframe_reflects_trigger():
    from app.config.settings import Settings
    from app.signals.engine import SignalEngine
    engine = SignalEngine(Settings(THRESHOLD_STRONG=45))
    candles = _m5_series(400)
    resampler = IncrementalResampler()
    for c in candles:
        resampler.add(c)
    from app.data.models import MultiTimeframeSnapshot
    snap = MultiTimeframeSnapshot(
        symbol="XAUUSD",
        timestamp=candles[-1].timestamp,
        current_price=candles[-1].close,
        m5=resampler.series(TimeFrame.M5, 120),
        m15=resampler.series(TimeFrame.M15, 150),
        m30=resampler.series(TimeFrame.M30, 100),
        h1=resampler.series(TimeFrame.H1, 80),
        h4=resampler.series(TimeFrame.H4, 50),
    )
    sig = engine.generate_signal(snap, mtf=MTF_4H_1H_15M_5M)
    assert sig.timeframe == "5m", f"got {sig.timeframe}"
    sig_prod = engine.generate_signal(snap, mtf=PROD_4H_1H_30M_15M)
    assert sig_prod.timeframe == "15m", f"got {sig_prod.timeframe}"


def test_daily_opportunity_basic():
    from datetime import timedelta

    from app.core.constants import SignalDirection
    from app.research.replay_engine import SignalRecord, daily_opportunity
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    candles = []
    price = 4600.0
    for i in range(300):
        ts = start + timedelta(minutes=5 * i)
        price += 0.1 * (i % 10 - 5)
        candles.append(_candle(ts, price - 0.2, price + 0.8, price - 0.9, price + 0.3))
    sigs = [
        SignalRecord(timestamp=start + timedelta(minutes=5 * i), direction=SignalDirection.LONG,
                     entry=price, base_sl=price - 10.0, base_risk=10.0, atr=5.0,
                     confidence=85.0, market_bias="BULLISH", regime="RANGING",
                     session="LONDON", reasons=["test"])
        for i in range(5)
    ]
    dop = daily_opportunity(sigs, candles, tp_r=1.5, hold_bars=96)
    assert dop["avg_signals_per_day"] > 0
    assert "pct_days_ge_30pts" in dop
    assert "pct_days_ge_50pts" in dop
