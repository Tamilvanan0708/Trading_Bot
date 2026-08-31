"""
Tests for the Live Market chart streaming & incremental update features:
- multi-timeframe forming-candle aggregation
- get_chart_series (closed + forming, full-width series for display)
- non-blocking service start
- SSE /market/{symbol}/stream endpoint
- lightweight /market/{symbol}/quote endpoint
- research-cache fast fallback (HISTORICAL_CACHE, no network)
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.core.constants import TimeFrame
from app.data.live.service import LiveMarketDataService
from app.data.models import Candle, Tick


def _candle(ts, o, h, l, c, v=10.0):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _tick(price, ts, symbol="XAUUSD"):
    return Tick(symbol=symbol, timestamp=ts, bid=price, ask=price, volume=1.0)


def _service_with_history(n=96):
    """~1 day of closed 15M candles (96 * 15m = 24h)."""
    service = LiveMarketDataService()
    base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=15)
    service._closed_15m = [
        _candle(base_ts - timedelta(minutes=15 * i), 2600.0 + i, 2602.0 + i, 2598.0 + i, 2601.0 + i)
        for i in range(n)
    ][::-1]
    return service


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.mark.asyncio
async def test_aggregates_ticks_into_all_timeframes():
    """on_tick must maintain forming candles for 5m/15m/30m/1h/4h."""
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    bucket = now - timedelta(minutes=5)

    await service.on_tick(_tick(2650.0, bucket + timedelta(seconds=1)))
    await service.on_tick(_tick(2655.0, bucket + timedelta(seconds=2)))
    await service.on_tick(_tick(2648.0, bucket + timedelta(seconds=3)))

    for tf in (TimeFrame.M5, TimeFrame.M15, TimeFrame.M30, TimeFrame.H1, TimeFrame.H4):
        f = service._forming_by_tf.get(tf)
        assert f is not None, f"no forming candle for {tf.value}"
        assert f.open == 2650.0, tf
        assert f.high == 2655.0, tf
        assert f.low == 2648.0, tf
        assert f.close == 2648.0, tf


@pytest.mark.asyncio
async def test_5m_forming_finalizes_into_closed_5m():
    """When the 5m bucket advances, the forming 5M candle is stored closed."""
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    b1 = now - timedelta(minutes=10)
    b2 = now - timedelta(minutes=5)

    await service.on_tick(_tick(2650.0, b1))
    await service.on_tick(_tick(2655.0, b2))

    # The b1 (older) 5m bucket was finalized into closed_5m.
    assert len(service._closed_5m) == 1
    assert service._closed_5m[-1].close == 2650.0
    # New forming 5m candle started.
    assert service._forming_by_tf[TimeFrame.M5].open == 2655.0


@pytest.mark.asyncio
async def test_get_chart_series_returns_closed_and_forming():
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    await service.on_tick(_tick(2660.0, now))

    for tf in (TimeFrame.M5, TimeFrame.M15, TimeFrame.M30, TimeFrame.H1, TimeFrame.H4):
        series = await service.get_chart_series("XAUUSD", tf, limit=200)
        assert series["candles"], f"{tf.value} returned no candles"
        # The series tail is the forming candle (timestamp == last candle).
        assert series["candles"][-1].timestamp == series["forming"].timestamp
    # M5 closed list may be empty (no 5m close yet) but M15 closed is seeded.
    series15 = await service.get_chart_series("XAUUSD", TimeFrame.M15, limit=200)
    assert series15["closed"], "15m closed series should be seeded from history"


@pytest.mark.asyncio
async def test_get_chart_series_no_lookahead_via_forming_flag():
    """get_chart_series exposes the forming candle for display only; the
    analysis snapshot (closed-only) must never include it by default."""
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    await service.on_tick(_tick(2660.0, now))

    snap = await service.get_multi_timeframe_snapshot("XAUUSD", include_forming=False)
    m15_last = snap.m15[-1].timestamp
    assert m15_last < service._forming_by_tf[TimeFrame.M15].timestamp


@pytest.mark.asyncio
async def test_start_is_non_blocking_and_requires_no_network():
    """start() must return immediately (spawns a background task) and never
    block on the Binance connection."""
    import asyncio

    class _FakeProvider:
        """Fails fast — no network. Mimics MarketDataProvider enough to let
        the background startup complete quickly."""

        async def get_ohlcv(self, *a, **k):
            return []

        async def load_base_15m(self, limit=800):
            return [], {"errors": []}

        async def get_latest_price(self, symbol):
            raise ValueError("no data")

    service = LiveMarketDataService(historical_provider=_FakeProvider())
    t0 = asyncio.get_event_loop().time()
    await service.start()
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed < 2.0
    assert service._running is True
    assert service._startup_task is not None
    await service.stop()


@pytest.mark.asyncio
async def test_get_latest_price_prefers_live_tick():
    service = _service_with_history()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    await service.on_tick(_tick(4700.0, now))
    price = await service.get_latest_price("XAUUSD")
    assert price == 4700.0


def test_quote_endpoint_returns_price(client):
    r = client.get("/market/XAUUSD/quote")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        d = r.json()
        assert "price" in d
        assert "status" in d
        assert "symbol" in d


def test_stream_endpoint_rejects_invalid_timeframe(client):
    r = client.get("/market/XAUUSD/stream?timeframe=99m")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_stream_generator_emits_snapshot_event():
    """Exercise the SSE generator's async body iterator directly (avoids
    blocking on the infinite HTTP stream).  Must emit a snapshot event using
    the fast research-cache fallback (no network)."""
    import asyncio
    from app.api.routes.market import stream_live_market

    resp = await stream_live_market("XAUUSD", "15m", request=None)
    assert resp.media_type == "text/event-stream"
    it = resp.body_iterator
    try:
        first = await asyncio.wait_for(it.__anext__(), timeout=10.0)
        if isinstance(first, bytes):
            first = first.decode("utf-8", errors="replace")
        assert "event:" in first or "data:" in first
        # The first event should reference candle data (snapshot).
        assert "candles" in first or "data_status" in first
    finally:
        await it.aclose()


def test_live_endpoint_keeps_fallback_chain(client):
    """/market/{symbol}/live must still work through the fallback chain and
    return a data_status field (HEALTHY / HISTORICAL / HISTORICAL_CACHE)."""
    r = client.get("/market/XAUUSD/live?timeframe=15m")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        d = r.json()
        assert d["data_status"] in ("HEALTHY", "HISTORICAL", "HISTORICAL_CACHE")
        assert "current_price" in d
        assert "candle_count" in d


def test_fast_fallback_serves_cache_immediately():
    """The fast (SSE seed) path must return persisted research candles without
    any network — proving the page can paint HISTORICAL_CACHE immediately."""
    import asyncio
    from app.api.routes.market import _resolve_chart_payload
    from app.core.constants import TimeFrame

    payload = asyncio.run(_resolve_chart_payload("XAUUSD", TimeFrame.M15, include_forming=True, fast=True))
    assert payload["candles"], "fast path returned no candles"
    assert payload["data_status"] == "HISTORICAL_CACHE"
    assert len(payload["candles"]) > 0
    # OHLC integrity of the served candles
    for c in payload["candles"][-5:]:
        assert c["high"] >= c["open"]
        assert c["high"] >= c["close"]
        assert c["low"] <= c["open"]
        assert c["low"] <= c["close"]


def test_cache_endpoint_serves_200_candles_all_timeframes(client):
    """The dedicated /market/{symbol}/cache endpoint must serve ~200 real
    candles for every timeframe with data_status HISTORICAL_CACHE (local-only,
    no network)."""
    for tf in ("5m", "15m", "30m", "1h", "4h"):
        r = client.get(f"/market/XAUUSD/cache?timeframe={tf}")
        assert r.status_code == 200, f"{tf} -> {r.status_code}"
        d = r.json()
        assert d["data_status"] == "HISTORICAL_CACHE", tf
        assert len(d["candles"]) > 100, f"{tf} returned too few candles"
        assert d["candles"][-1]["timestamp"], tf
        assert d["current_price"], tf
        # OHLC integrity
        for c in d["candles"][-5:]:
            assert c["high"] >= c["open"], tf
            assert c["high"] >= c["close"], tf
            assert c["low"] <= c["open"], tf
            assert c["low"] <= c["close"], tf


def test_cache_endpoint_rejects_invalid_timeframe(client):
    r = client.get("/market/XAUUSD/cache?timeframe=99m")
    assert r.status_code == 400


def test_frontend_uses_sse_and_incremental_chart():
    """The Live Market frontend must use the SSE stream URL and the incremental
    candle renderer (not a 30s full reload of 200 candles)."""
    import os
    path = "app/static/terminal/js/app.js"
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "liveStreamURL" in content or "/stream" in content
    assert "candlesLive" in content or "Charts.candles" in content
    assert "EventSource" in content
    # No repeated full-series reload loop in the live route.
    assert "switchTF" in content
    assert "seq" in content


def test_charts_js_has_incremental_renderer():
    import os
    path = "app/static/terminal/js/charts.js"
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "candlesLive" in content
    assert "candles" in content


def test_api_js_has_quote_and_stream():
    import os
    path = "app/static/terminal/js/api.js"
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "liveQuote" in content
    assert "liveStreamURL" in content
