"""
Unit tests for the live market data service (tick aggregation + look-ahead safety).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.data.live.service import LiveMarketDataService
from app.data.models import Candle, Tick


def _tick(price: float, ts: datetime, symbol="XAUUSD") -> Tick:
    return Tick(symbol=symbol, timestamp=ts, bid=price, ask=price, volume=1.0)


def _service_with_history():
    service = LiveMarketDataService()
    base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=15)
    service._closed_15m = [
        Candle(timestamp=base_ts - timedelta(minutes=15 * i), open=2600.0 + i, high=2602.0 + i, low=2598.0 + i, close=2601.0 + i, volume=10.0)
        for i in range(10)
    ][::-1]
    return service


def _service_with_long_history(n=24):
    """Creates 6+ hours of closed candles so complete 30M/1H/4H candles exist."""
    service = LiveMarketDataService()
    base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=15)
    service._closed_15m = [
        Candle(timestamp=base_ts - timedelta(minutes=15 * i), open=2600.0 + i, high=2602.0 + i, low=2598.0 + i, close=2601.0 + i, volume=10.0)
        for i in range(n)
    ][::-1]
    return service


@pytest.mark.asyncio
async def test_aggregate_ticks_into_forming_candle():
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket = now - timedelta(minutes=5)

    ticks = [
        _tick(2650.0, bucket + timedelta(seconds=1)),
        _tick(2655.0, bucket + timedelta(seconds=2)),
        _tick(2648.0, bucket + timedelta(seconds=3)),
        _tick(2652.0, bucket + timedelta(seconds=4)),
    ]
    for t in ticks:
        await service.on_tick(t)

    assert service._forming_15m is not None
    assert service._forming_15m.open == 2650.0
    assert service._forming_15m.high == 2655.0
    assert service._forming_15m.low == 2648.0
    assert service._forming_15m.close == 2652.0


@pytest.mark.asyncio
async def test_forming_candle_finalized_when_bucket_changes():
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Ensure ticks are in different 15M buckets
    bucket1 = now - timedelta(minutes=30)
    bucket2 = now - timedelta(minutes=5)

    await service.on_tick(_tick(2650.0, bucket1))
    await service.on_tick(_tick(2655.0, bucket2))

    assert len(service._closed_15m) == 11
    assert service._closed_15m[-1].close == 2650.0
    assert service._forming_15m is not None
    assert service._forming_15m.open == 2655.0


@pytest.mark.asyncio
async def test_snapshot_uses_closed_candles_only():
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket = now - timedelta(minutes=5)

    await service.on_tick(_tick(2650.0, bucket))
    await service.on_tick(_tick(2655.0, bucket + timedelta(seconds=1)))

    snap = await service.get_multi_timeframe_snapshot("XAUUSD")
    assert snap.m15[-1].close == service._closed_15m[-1].close

    snap_form = await service.get_multi_timeframe_snapshot("XAUUSD", include_forming=True)
    assert snap_form.m15[-1].close == 2655.0


@pytest.mark.asyncio
async def test_snapshot_builds_all_timeframes():
    service = _service_with_long_history()
    snap = await service.get_multi_timeframe_snapshot("XAUUSD")
    assert len(snap.m15) > 0
    assert len(snap.m30) > 0
    assert len(snap.h1) > 0
    assert len(snap.h4) > 0
    assert snap.current_price == snap.m15[-1].close


@pytest.mark.asyncio
async def test_snapshot_htf_filter_drops_incomplete_4h():
    """With only 10 candles (135 min), no complete 4H candle can exist."""
    service = LiveMarketDataService()
    # Fixed timestamps so the test is deterministic regardless of wall-clock time.
    # 4H boundaries at 00:00/04:00: candles 01:30..03:45 leave the 00:00->04:00
    # bucket incomplete (04:00 > 03:45).
    base_ts = datetime(2026, 8, 24, 3, 45, tzinfo=timezone.utc)
    service._closed_15m = [
        Candle(timestamp=base_ts - timedelta(minutes=15 * (10 - i)), open=2600.0, high=2602.0, low=2598.0, close=2601.0, volume=10.0)
        for i in range(10)
    ]
    snap = await service.get_multi_timeframe_snapshot("XAUUSD")
    assert len(snap.m15) > 0
    assert len(snap.m30) > 0
    assert len(snap.h4) == 0  # correctly filtered as incomplete


@pytest.mark.asyncio
async def test_out_of_order_tick_dropped_not_merged():
    """Regression: a stale tick (older bucket) must NOT corrupt the forming candle."""
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    current_bucket = now - timedelta(minutes=5)
    stale_bucket = now - timedelta(minutes=30)

    # Current-bucket ticks first
    await service.on_tick(_tick(2650.0, current_bucket))
    await service.on_tick(_tick(2655.0, current_bucket + timedelta(seconds=10)))

    # Now a stale tick from an OLDER bucket arrives
    await service.on_tick(_tick(9999.0, stale_bucket))

    # The forming candle must be unaffected by the stale tick
    assert service._forming_15m is not None
    assert service._forming_15m.high == 2655.0
    assert service._forming_15m.low == 2650.0
    assert service._forming_15m.close == 2655.0


@pytest.mark.asyncio
async def test_historical_tick_finalizes_older_candle():
    """Regression: a past-bucket tick must finalize the older candle, not merge."""
    from app.data.live.service import _bucket_start
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    older_bucket = now - timedelta(minutes=60)
    current_bucket = now - timedelta(minutes=45)

    await service.on_tick(_tick(2650.0, older_bucket))
    await service.on_tick(_tick(2655.0, current_bucket))

    expected_ts = _bucket_start(older_bucket, 15)
    # The older candle should now be in closed list
    assert len(service._closed_15m) == 11
    assert service._closed_15m[-1].timestamp == expected_ts
    assert service._closed_15m[-1].close == 2650.0
    assert service._forming_15m is not None
    assert service._forming_15m.open == 2655.0