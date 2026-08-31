"""
Unit tests for Binance history provider reliability: retries, timeouts,
HTTP failures, and validation.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.config.settings import Settings
from app.core.constants import TimeFrame
from app.data.live.binance_history import BinanceHistoryProvider

FAKE_KLINE = [
    1787508900000, "4617.89", "4617.90", "4617.89", "4617.89", "146.780",
    1787509799999, "677815.04888", 405, "115.468", "533219.67720", "0",
]


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or []
        self.text = "error" if status != 200 else "ok"

    def raise_for_status(self):
        if self.status_code != 200:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, params=None):
        self.calls += 1
        resp = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(resp, Exception):
            raise resp
        return resp


def _install(monkeypatch, responses, settings=None):
    client = _Client(responses)
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout: client)
    provider = BinanceHistoryProvider(settings or Settings(BINANCE_HISTORY_MAX_RETRIES=2, BINANCE_HISTORY_RETRY_BACKOFF=0.01))
    return provider, client


def test_binance_history_retries_then_succeeds(monkeypatch):
    provider, client = _install(monkeypatch, [
        _Resp(500),
        _Resp(500),
        _Resp(200, [FAKE_KLINE]),
    ])
    candles = asyncio.run(provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=1))
    assert len(candles) == 1
    assert client.calls == 3  # two failures then success


def test_binance_history_timeout_raises_after_retries(monkeypatch):
    provider, client = _install(monkeypatch, [
        httpx.TimeoutException("timeout"),
        httpx.TimeoutException("timeout"),
        httpx.TimeoutException("timeout"),
    ])
    with pytest.raises(RuntimeError, match="failed after"):
        asyncio.run(provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=1))
    assert client.calls == 3


def test_binance_history_http_500_raises(monkeypatch):
    provider, client = _install(monkeypatch, [
        _Resp(500), _Resp(500), _Resp(500),
    ])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        asyncio.run(provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=1))


def test_binance_history_malformed_row_skipped(monkeypatch):
    bad_row = [1787508900000, "4617.89"]  # too short
    provider, _ = _install(monkeypatch, [_Resp(200, [bad_row, FAKE_KLINE])])
    candles = asyncio.run(provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=2))
    assert len(candles) == 1


def test_load_base_15m_returns_validation_report(monkeypatch):
    now = datetime.now(timezone.utc)
    ts = int(now.timestamp() * 1000)
    # Ascending order: oldest first, newest last
    rows = [[ts - (4 - i) * 900000, "4617", "4618", "4616", "4617", "10", ts, "0", 1, "1", "0", "0"] for i in range(5)]
    provider, _ = _install(monkeypatch, [_Resp(200, rows)])
    candles, report = asyncio.run(provider.load_base_15m(limit=5))
    assert len(candles) == 5
    assert report["total"] == 5
    assert report["valid"] is True


def test_load_base_15m_reports_gaps(monkeypatch):
    now = datetime.now(timezone.utc)
    ts = int(now.timestamp() * 1000)
    # 3 candles ascending: oldest first, newest last
    rows = sorted([
        [ts - 6 * 900000, "4617", "4618", "4616", "4617", "10", ts, "0", 1, "1", "0", "0"],
        [ts - 5 * 900000, "4617", "4618", "4616", "4617", "10", ts, "0", 1, "1", "0", "0"],
        [ts - 1 * 900000, "4617", "4618", "4616", "4617", "10", ts, "0", 1, "1", "0", "0"],
    ], key=lambda r: r[0])
    provider, _ = _install(monkeypatch, [_Resp(200, rows)])
    candles, report = asyncio.run(provider.load_base_15m(limit=3))
    assert report["gaps"] >= 1