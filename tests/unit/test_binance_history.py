"""
Unit tests for the Binance REST history provider (real XAUUSDT klines).
"""

from datetime import datetime, timezone

import pytest

from app.core.constants import TimeFrame
from app.data.live.binance_history import BinanceHistoryProvider

FAKE_KLINE = [
    1787508900000,
    "4617.89", "4617.90", "4617.89", "4617.89", "146.780",
    1787509799999, "677815.04888", 405, "115.468", "533219.67720", "0",
]


class _FakeResponse:
    def __init__(self, rows):
        self._rows = rows
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
        self._request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, params=None):
        self._request = (url, params)
        return _FakeResponse(self._rows)


def test_binance_history_parses_klines(monkeypatch):
    provider = BinanceHistoryProvider()
    fake = _FakeClient([FAKE_KLINE])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: fake)

    import asyncio
    candles = asyncio.run(provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=1))

    assert len(candles) == 1
    c = candles[0]
    assert c.timestamp == datetime.fromtimestamp(1787508900, tz=timezone.utc)
    assert c.open == 4617.89
    assert c.high == 4617.90
    assert c.low == 4617.89
    assert c.close == 4617.89
    assert c.volume == 146.780


def test_binance_history_uses_xauusdt_symbol(monkeypatch):
    provider = BinanceHistoryProvider()
    fake = _FakeClient([FAKE_KLINE])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: fake)

    import asyncio
    asyncio.run(provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=1))
    url, params = fake._request
    assert params["symbol"] == "XAUUSDT"
    assert params["interval"] == "15m"


def test_binance_history_timeframe_mapping(monkeypatch):
    provider = BinanceHistoryProvider()
    fake = _FakeClient([FAKE_KLINE])
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: fake)

    import asyncio
    asyncio.run(provider.get_ohlcv("XAUUSD", TimeFrame.H4, limit=1))
    _, params = fake._request
    assert params["interval"] == "4h"


def test_binance_history_unsupported_timeframe():
    provider = BinanceHistoryProvider()
    with pytest.raises(ValueError, match="Unsupported"):
        provider._interval("2h")  # type: ignore