"""
Tests for strategy versioning, signal outcome tracking, and the
observation-mode paper-trading safety fix.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import Settings
from app.config.strategy_version import derive_strategy_version
from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.data.models import Candle
from app.research.outcome_tracker import SignalOutcomeTracker
from app.signals.models import SignalPayload


def _candle(ts, o, h, l, c) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _signal(direction="LONG", entry=100.0, sl=90.0, ts=None):
    return SignalPayload(
        signal_id=f"sig-{direction}-{entry}",
        instrument="XAUUSD",
        direction=SignalDirection(direction),
        strategy=StrategyType.CONFLUENCE,
        timeframe="15m",
        timestamp=ts or datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        entry=entry,
        stop_loss=sl,
        take_profit_1=entry + 5.0,
        take_profit_2=entry + 10.0,
        take_profit_3=entry + 15.0,
        risk_reward=1.5,
        confidence_score=80.0,
        signal_quality=SignalQuality.STRONG,
        market_bias=MarketBias.BULLISH,
        strategy_version="test-version",
    )


# ---------------------------------------------------------------------------
# Strategy versioning
# ---------------------------------------------------------------------------


def test_strategy_version_is_stable():
    v1 = derive_strategy_version(Settings())
    v2 = derive_strategy_version(Settings())
    assert v1 == v2
    assert len(v1) > 20  # base + digest
    assert ":" in v1


def test_strategy_version_changes_with_config():
    base = Settings()
    changed = Settings(THRESHOLD_STRONG=78)
    assert derive_strategy_version(base) != derive_strategy_version(changed)


def test_signal_payload_carries_strategy_version():
    sig = _signal()
    assert sig.strategy_version == "test-version"


# ---------------------------------------------------------------------------
# Signal outcome tracker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outcome_tracker_sl_priority_same_candle():
    tracker = SignalOutcomeTracker(Settings())
    ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal("LONG", entry=100.0, sl=90.0, ts=ts)
    tracker.register(sig)
    # Same candle touches both TP1 (108) and SL (88): SL wins (conservative).
    candle = _candle(ts + timedelta(minutes=15), 100, 110, 88, 105)
    captured = {}

    async def fake_update(signal_id, updates):
        captured.update(updates)

    import types
    mock_repo = types.SimpleNamespace(update_signal_outcome=fake_update)

    await tracker.update([candle], repo=mock_repo)
    assert tracker.open_count == 0
    assert captured["outcome"] == "SL"
    assert captured["final_r"] == -1.0
    assert captured["sl_hit"] is True
    assert captured["time_to_outcome_hours"] is not None


@pytest.mark.asyncio
async def test_outcome_tracker_tp3_reached():
    tracker = SignalOutcomeTracker(Settings())
    ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal("LONG", entry=100.0, sl=90.0, ts=ts)
    tracker.register(sig)
    # Price skips to 118 (> TP3=115) without touching SL.
    candle = _candle(ts + timedelta(minutes=15), 100, 118, 99, 116)
    captured = {}

    async def fake_update(signal_id, updates):
        captured.update(updates)

    import types
    mock_repo = types.SimpleNamespace(update_signal_outcome=fake_update)

    await tracker.update([candle], repo=mock_repo)
    assert tracker.open_count == 0
    assert captured["outcome"] == "TP3"
    assert captured["tp3_hit"] is True
    assert captured["max_favorable_excursion_r"] >= 1.5


@pytest.mark.asyncio
async def test_outcome_tracker_open_after_partial_move():
    tracker = SignalOutcomeTracker(Settings())
    ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal("LONG", entry=100.0, sl=90.0, ts=ts)
    tracker.register(sig)
    # Favorable move but below TP1 (105): open remains.
    candle = _candle(ts + timedelta(minutes=15), 100, 104, 99, 103)
    captured = {}

    async def fake_update(signal_id, updates):
        captured.update(updates)

    import types
    mock_repo = types.SimpleNamespace(update_signal_outcome=fake_update)

    await tracker.update([candle], repo=mock_repo)
    assert tracker.open_count == 1  # still open
    assert captured["outcome"] == "OPEN"
    assert captured["max_favorable_excursion_r"] == 0.4
    assert captured["max_r_achieved"] == 0.4


def test_outcome_tracker_ignores_no_trade_signals():
    tracker = SignalOutcomeTracker(Settings())
    tracker.register(_signal("LONG"))
    # NO_TRADE signal has no direction to track.
    no_trade = _signal("LONG")
    no_trade.direction = SignalDirection.NO_TRADE
    tracker.register(no_trade)
    # Only the LONG signal is tracked.
    assert tracker.open_count == 1


@pytest.mark.asyncio
async def test_outcome_tracker_handles_naive_db_datetimes():
    """Regression: SQLite stores naive datetimes; tracker must normalize."""
    tracker = SignalOutcomeTracker(Settings())
    ts = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    sig = _signal("LONG", entry=100.0, sl=90.0, ts=ts)
    tracker.register(sig)
    # Simulate a naive candle timestamp (as loaded from SQLite).
    naive_ts = (ts + timedelta(minutes=15)).replace(tzinfo=None)
    candle = _candle(naive_ts, 100, 118, 99, 116)
    captured = {}

    async def fake_update(signal_id, updates):
        captured.update(updates)

    import types
    mock_repo = types.SimpleNamespace(update_signal_outcome=fake_update)

    await tracker.update([candle], repo=mock_repo)
    assert tracker.open_count == 0  # finalized (TP3 hit) without TypeError
    assert captured["outcome"] == "TP3"


# ---------------------------------------------------------------------------
# Observation-mode safety: the pipeline must never open paper trades
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_never_opens_paper_trade_directly(monkeypatch):
    """run_full_analysis must NOT call open_position_from_signal.

    Paper trades may only be opened by the scheduler AFTER the admission gate
    and the observation-mode / failed-strategy safety checks.  This test
    fails if a future refactor re-introduces an unconditional open.
    """
    from app.data.csv_provider import CsvMarketDataProvider
    from app.services.pipeline import AnalysisPipeline

    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    pipeline = AnalysisPipeline(provider)
    pipeline._paper_restored = True

    called = {"open": False}

    async def _forbid_open(signal, repo=None):
        called["open"] = True

    monkeypatch.setattr(pipeline.paper_service, "open_position_from_signal", _forbid_open)

    await pipeline.run_full_analysis(symbol="XAUUSD", db_session=None)

    assert called["open"] is False, (
        "run_full_analysis must not open paper trades directly — "
        "the scheduler is the single authority after admission + safety gates."
    )
