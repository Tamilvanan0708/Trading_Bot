"""
Unit tests for Market Data Provider and Multi-Timeframe Resampling.
"""

from datetime import datetime, timezone

import pytest

from app.core.constants import TimeFrame
from app.data.csv_provider import CsvMarketDataProvider
from app.data.models import Candle


def test_candle_model_validation():
    # Valid candle
    c = Candle(
        timestamp=datetime.now(timezone.utc),
        open=2650.0,
        high=2660.0,
        low=2645.0,
        close=2655.0,
        volume=1200.0,
    )
    assert c.is_bullish is True
    assert c.body_size == 5.0
    assert c.total_range == 15.0

    # Invalid candle where high is lower than open
    with pytest.raises(ValueError):
        Candle(
            timestamp=datetime.now(timezone.utc),
            open=2650.0,
            high=2640.0,  # Invalid
            low=2630.0,
            close=2645.0,
            volume=100.0,
        )


@pytest.mark.asyncio
async def test_csv_provider_and_multi_tf_snapshot():
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")

    price = await provider.get_latest_price("XAUUSD")
    assert price > 1000.0

    m15 = await provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=50)
    assert len(m15) == 50

    m30 = await provider.get_ohlcv("XAUUSD", TimeFrame.M30, limit=50)
    assert len(m30) == 50

    h1 = await provider.get_ohlcv("XAUUSD", TimeFrame.H1, limit=50)
    assert len(h1) == 50

    h4 = await provider.get_ohlcv("XAUUSD", TimeFrame.H4, limit=20)
    assert len(h4) == 20

    snapshot = await provider.get_multi_timeframe_snapshot("XAUUSD")
    assert len(snapshot.m15) > 0
    assert len(snapshot.m30) > 0
    assert len(snapshot.h1) > 0
    assert len(snapshot.h4) > 0
    assert snapshot.current_price == snapshot.m15[-1].close
