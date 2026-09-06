import pytest
from datetime import datetime, timezone
from app.backtesting.data_loader import fetch_historical_candles

@pytest.mark.asyncio
async def test_master_cache_range_slicing():
    """Verify that requesting a sub-range slices correctly from the master 3-month dataset."""
    start_dt = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 8, 30, 23, 59, 59, tzinfo=timezone.utc)

    candles = await fetch_historical_candles("XAUUSD", "1h", start_dt, end_dt, use_cache=True)
    assert len(candles) > 0, "Should load sliced candles from master cache"
    assert candles[0].timestamp >= start_dt, "First candle must be on or after start_dt"
    assert candles[-1].timestamp <= end_dt, "Last candle must be on or before end_dt"

    # Check chronological ordering
    for i in range(1, len(candles)):
        assert candles[i].timestamp > candles[i-1].timestamp, "Candles must be strictly ascending"

@pytest.mark.asyncio
async def test_5m_master_cache_slicing():
    """Verify 5m candles slice properly from master dataset."""
    start_dt = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 8, 11, 23, 59, 59, tzinfo=timezone.utc)

    candles = await fetch_historical_candles("XAUUSD", "5m", start_dt, end_dt, use_cache=True)
    assert len(candles) > 0, "5m candles should be sliced from master cache"
    assert candles[0].timestamp >= start_dt
    assert candles[-1].timestamp <= end_dt
