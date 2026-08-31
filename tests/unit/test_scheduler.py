"""
Tests for the candle-boundary scheduler: dedup, closed-candle detection,
and restart safety.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.data.live.service import LiveMarketDataService
from app.data.models import Candle
from app.services.scheduler import AnalysisScheduler

_TEST_SETTINGS = dict(SCHEDULER_POLL_INTERVAL=1, LIVE_HISTORY_REFRESH_ON_DEGRADED=False)


def _candle(ts: datetime, o=2650.0, h=2655.0, l=2645.0, c=2652.0) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _service_with_closed_candle(ts: datetime):
    service = LiveMarketDataService()
    # History before the latest closed candle
    service._closed_15m = [
        _candle(ts - timedelta(minutes=15 * i), c=2650.0 + i) for i in range(5, 0, -1)
    ]
    service._closed_15m.append(_candle(ts, c=2655.0))
    service._last_price = 2655.0
    return service


@pytest.mark.asyncio
async def test_scheduler_skips_candle_before_close():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = now - timedelta(minutes=5)  # candle not yet closed (close at ts+15min)
    service = _service_with_closed_candle(ts)
    scheduler = AnalysisScheduler(service, Settings(**_TEST_SETTINGS))
    await scheduler._tick()
    assert scheduler._last_processed_ts is None


@pytest.mark.asyncio
async def test_scheduler_skips_first_candle_after_restart():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = now - timedelta(minutes=30)  # candle fully closed
    service = _service_with_closed_candle(ts)
    scheduler = AnalysisScheduler(service, Settings(**_TEST_SETTINGS))
    await scheduler._tick()
    # Restart safety: current candle recorded but not processed
    assert scheduler._last_processed_ts == ts
    # A second tick should not process the same candle
    await scheduler._tick()
    assert scheduler._last_processed_ts == ts


@pytest.mark.asyncio
async def test_scheduler_processes_new_candle_after_restart_skip(monkeypatch):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = now - timedelta(minutes=60)  # fully closed with margin
    service = _service_with_closed_candle(ts)
    scheduler = AnalysisScheduler(service, Settings(**_TEST_SETTINGS))

    processed = []
    async def fake_run_analysis(snap, candle):
        processed.append(candle.timestamp)
        scheduler._last_processed_ts = candle.timestamp  # replicate real dedup update

    monkeypatch.setattr(scheduler, "_run_analysis", fake_run_analysis)

    # First tick: restart skip
    await scheduler._tick()
    assert scheduler._last_processed_ts == ts
    assert processed == []

    # New candle closes (30 min ago — fully closed)
    new_ts = ts + timedelta(minutes=30)
    service._closed_15m.append(_candle(new_ts, c=2660.0))
    await scheduler._tick()
    assert processed == [new_ts]

    # Duplicate: same candle again → not processed
    await scheduler._tick()
    assert processed == [new_ts]


@pytest.mark.asyncio
async def test_scheduler_process_last_closed_on_start(monkeypatch):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = now - timedelta(minutes=60)
    service = _service_with_closed_candle(ts)
    scheduler = AnalysisScheduler(service, Settings(**_TEST_SETTINGS, PROCESS_LAST_CLOSED_ON_START=True))

    processed = []
    async def fake_run_analysis(snap, candle):
        processed.append(candle.timestamp)

    monkeypatch.setattr(scheduler, "_run_analysis", fake_run_analysis)
    await scheduler._tick()
    assert processed == [ts]


@pytest.mark.asyncio
async def test_scheduler_real_tick_runs_analysis_with_db(tmp_path, monkeypatch):
    """Regression: _tick -> _run_analysis must not crash (Candle vs datetime)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.database.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/sched.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.services.scheduler.async_session_factory", maker)

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = now - timedelta(minutes=90)
    service = _service_with_closed_candle(ts)
    scheduler = AnalysisScheduler(service, Settings(**_TEST_SETTINGS, PROCESS_LAST_CLOSED_ON_START=True))

    # Run the real _tick (which runs the real _run_analysis against a real DB)
    await scheduler._tick()

    # It must not raise; the candle should be marked processed
    assert scheduler._last_processed_ts == ts
    await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_skips_weekend_when_enabled(monkeypatch):
    """Scheduler must skip analysis entirely when the market is closed (e.g. weekend)."""
    import app.core.market_hours as mh

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = now - timedelta(minutes=60)
    service = _service_with_closed_candle(ts)
    scheduler = AnalysisScheduler(service, Settings(**_TEST_SETTINGS, MARKET_HOURS_UTC_ENABLED=True))

    processed = []
    async def fake_run_analysis(snap, candle):
        processed.append(candle.timestamp)

    monkeypatch.setattr(scheduler, "_run_analysis", fake_run_analysis)
    monkeypatch.setattr(mh, "is_market_open", lambda dt: False)
    monkeypatch.setattr(mh, "next_market_open", lambda dt: datetime(2026, 8, 23, 23, 0, tzinfo=timezone.utc))

    await scheduler._tick()
    assert processed == []  # no analysis on a closed market