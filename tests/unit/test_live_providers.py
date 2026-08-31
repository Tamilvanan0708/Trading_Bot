"""
Unit tests for live market feed providers: Binance, cTrader, TradingView webhook,
and the generic WebSocket consumer with a local mock server.
"""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.data.live.binance_provider import (
    STREAM_AGG_TRADE,
    STREAM_BOOK_TICKER,
    BinanceGoldMarketProvider,
    build_binance_stream_url,
)
from app.data.live.ctrader_provider import (
    MSG_AUTH,
    CTraderMarketProvider,
    _parse_timestamp,
    build_auth_message,
    build_subscribe_spots_message,
)
from app.data.live.registry import FeedRegistry
from app.data.live.tradingview_webhook import router as webhook_router
from app.data.models import Tick
from app.data.websocket_provider import WebSocketMarketFeed

# ---------------------------------------------------------------------------
# Binance URL builder
# ---------------------------------------------------------------------------

def test_build_binance_stream_url():
    url = build_binance_stream_url(
        streams=["xauusdt@bookTicker", "xauusdt@aggTrade"]
    )
    assert "xauusdt@bookTicker" in url
    assert "xauusdt@aggTrade" in url


def test_binance_provider_uses_correct_xauusdt_symbol():
    """Regression: XAUUSD must map to the real Binance ticker xauusdt."""
    feed = BinanceGoldMarketProvider(symbol="XAUUSD", max_reconnect_attempts=0)
    assert feed._binance_symbol == "xauusdt"
    assert feed._url == "wss://fstream.binance.com/stream?streams=xauusdt@bookTicker/xauusdt@aggTrade"


# ---------------------------------------------------------------------------
# Binance message parsing
# ---------------------------------------------------------------------------

def _binance_frame(event: str, **overrides) -> str:
    base = {"E": 1723000000000, "s": "XAUUSDT"}
    if event == STREAM_BOOK_TICKER:
        base.update({"e": "bookTicker", "b": "2650.0", "a": "2650.4", "B": "1.0", "A": "1.0"})
    elif event == STREAM_AGG_TRADE:
        base.update({"e": "aggTrade", "p": "2650.2", "q": "0.5", "T": 1723000000000})
    base.update(overrides)
    return json.dumps({"stream": f"{event}", "data": base})


def test_binance_parse_book_ticker():
    feed = BinanceGoldMarketProvider(symbol="XAUUSD", max_reconnect_attempts=0)
    ticks = feed.parse_message(_binance_frame(STREAM_BOOK_TICKER))
    assert len(ticks) == 1
    assert ticks[0].bid == 2650.0
    assert ticks[0].ask == 2650.4
    assert ticks[0].symbol == "XAUUSD"


def test_binance_parse_agg_trade():
    feed = BinanceGoldMarketProvider(symbol="XAUUSD", max_reconnect_attempts=0)
    ticks = feed.parse_message(_binance_frame(STREAM_AGG_TRADE))
    assert len(ticks) == 1
    assert ticks[0].last == 2650.2
    assert ticks[0].volume == 0.5


def test_binance_parse_invalid_json_returns_empty():
    feed = BinanceGoldMarketProvider(symbol="XAUUSD", max_reconnect_attempts=0)
    assert feed.parse_message("not json") == []


def test_binance_parse_unknown_event_returns_empty():
    feed = BinanceGoldMarketProvider(symbol="XAUUSD", max_reconnect_attempts=0)
    ticks = feed.parse_message(json.dumps({"data": {"e": "unknown"}}))
    assert ticks == []


# ---------------------------------------------------------------------------
# cTrader message building & parsing
# ---------------------------------------------------------------------------

def test_ctrader_auth_message():
    msg = build_auth_message("test_token", 42)
    assert msg["type"] == MSG_AUTH
    assert msg["data"]["accessToken"] == "test_token"
    assert msg["cid"] == 42


def test_ctrader_subscribe_spots_message():
    msg = build_subscribe_spots_message(account_id=100, symbol_ids=[1, 2, 3], cid=7)
    assert msg["type"] == "spot.subscribe.spots"
    assert msg["data"]["accountId"] == 100
    assert msg["data"]["symbolIds"] == [1, 2, 3]
    assert msg["cid"] == 7


def test_ctrader_constructor_requires_token():
    with pytest.raises(ValueError, match="access token"):
        CTraderMarketProvider(symbol="XAUUSD", access_token="")


def test_ctrader_constructor_requires_account_and_symbol():
    with pytest.raises(ValueError, match="account_id and symbol_id"):
        CTraderMarketProvider(
            symbol="XAUUSD", access_token="tok", account_id=None, symbol_id=None
        )


def test_ctrader_parse_auth_response():
    provider = CTraderMarketProvider(
        symbol="XAUUSD",
        access_token="tok",
        account_id=1,
        symbol_id=1,
        max_reconnect_attempts=0,
    )
    raw = json.dumps({"type": "spot.app.auth", "data": {"isAuthorized": True}})
    ticks = provider.parse_message(raw)
    assert ticks == []
    assert provider._authenticated is True


def test_ctrader_parse_spot_message():
    provider = CTraderMarketProvider(
        symbol="XAUUSD",
        access_token="tok",
        account_id=1,
        symbol_id=1,
        max_reconnect_attempts=0,
    )
    raw = json.dumps({
        "type": "spot.spot",
        "data": {"bid": 2650.0, "ask": 2650.5, "timestamp": "2024-08-23T10:00:00.000Z"},
    })
    ticks = provider.parse_message(raw)
    assert len(ticks) == 1
    assert ticks[0].bid == 2650.0
    assert ticks[0].ask == 2650.5
    assert ticks[0].symbol == "XAUUSD"


def test_ctrader_parse_unknown_type_returns_empty():
    provider = CTraderMarketProvider(
        symbol="XAUUSD",
        access_token="tok",
        account_id=1,
        symbol_id=1,
        max_reconnect_attempts=0,
    )
    assert provider.parse_message(json.dumps({"type": "spot.heartbeat"})) == []


def test_parse_timestamp_variants():
    ts = _parse_timestamp("2024-08-23T10:00:00.000Z")
    assert ts.hour == 10
    assert ts.tzinfo == timezone.utc

    ts2 = _parse_timestamp(1723000000000)
    assert isinstance(ts2, datetime)

    ts3 = _parse_timestamp(None)
    assert isinstance(ts3, datetime)


# ---------------------------------------------------------------------------
# Generic WebSocketMarketFeed with local mock server
# ---------------------------------------------------------------------------

class _EchoFeed(WebSocketMarketFeed):
    """Concrete test feed that parses a simple JSON tick format."""

    def parse_message(self, raw: str) -> list[Tick]:
        try:
            msg = json.loads(raw)
        except Exception:
            return []
        return [
            Tick(
                symbol=msg.get("symbol", "XAUUSD"),
                timestamp=datetime.now(timezone.utc),
                bid=float(msg["bid"]),
                ask=float(msg["ask"]),
            )
        ]


async def _mock_server_handler(websocket):
    await websocket.send(json.dumps({"symbol": "XAUUSD", "bid": 2650.0, "ask": 2650.4}))
    await websocket.send(json.dumps({"symbol": "XAUUSD", "bid": 2651.0, "ask": 2651.4}))
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_websocket_feed_receives_ticks():
    import websockets.asyncio.server

    server = await websockets.asyncio.server.serve(_mock_server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}"

    feed = _EchoFeed(url=url, symbols=["XAUUSD"], capacity=100, max_reconnect_attempts=0)
    feed.start()
    await asyncio.sleep(0.2)

    buf = feed.buffer_for("XAUUSD")
    assert buf is not None
    snap = await buf.snapshot()
    assert len(snap) == 2
    assert snap[0].bid == 2650.0
    assert snap[1].bid == 2651.0

    await feed.stop()
    server.close()


@pytest.mark.asyncio
async def test_websocket_feed_calls_on_tick_callback():
    """Regression: live ticks must route through the aggregation callback."""
    import websockets.asyncio.server

    server = await websockets.asyncio.server.serve(_mock_server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}"

    feed = _EchoFeed(url=url, symbols=["XAUUSD"], capacity=100, max_reconnect_attempts=0)
    received = []

    async def callback(tick):
        received.append(tick)

    feed.set_on_tick_callback(callback)
    feed.start()
    await asyncio.sleep(0.2)

    assert len(received) == 2
    assert received[0].bid == 2650.0

    await feed.stop()
    server.close()


async def _reconnect_server_handler(websocket, call_count):
    if call_count[0] == 0:
        await websocket.send(json.dumps({"symbol": "XAUUSD", "bid": 2650.0, "ask": 2650.4}))
        call_count[0] += 1
        return
    await websocket.send(json.dumps({"symbol": "XAUUSD", "bid": 2651.0, "ask": 2651.4}))


@pytest.mark.asyncio
async def test_websocket_feed_reconnects():
    import websockets.asyncio.server

    call_count = [0]
    async def handler(websocket):
        await _reconnect_server_handler(websocket, call_count)

    server = await websockets.asyncio.server.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}"

    feed = _EchoFeed(url=url, symbols=["XAUUSD"], capacity=100, reconnect_delay=0.05, max_reconnect_attempts=5)
    feed.start()
    await asyncio.sleep(0.15)

    buf = feed.buffer_for("XAUUSD")
    snap = await buf.snapshot()
    assert len(snap) >= 1

    await feed.stop()
    server.close()


# ---------------------------------------------------------------------------
# FeedRegistry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_register_buffer_and_push():
    registry = FeedRegistry.get_instance()
    buf = await registry.register_buffer("TEST_REG", capacity=10)
    assert buf is not None

    tick = Tick(symbol="TEST_REG", timestamp=datetime.now(timezone.utc), bid=100.0, ask=100.1)
    pushed = await registry.push_tick(tick)
    assert pushed is True

    latest = await registry.latest_tick("TEST_REG")
    assert latest is not None and latest.bid == 100.0

    snap = await registry.snapshot("TEST_REG")
    assert len(snap) == 1


# ---------------------------------------------------------------------------
# TradingView Webhook
# ---------------------------------------------------------------------------

def test_webhook_handler_receives_alert():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    registry = FeedRegistry.get_instance()
    loop.run_until_complete(registry.register_buffer("TEST_WEBHOOK"))

    app = FastAPI()
    app.include_router(webhook_router)
    client = TestClient(app)

    resp = client.post("/webhook/tradingview", json={
        "symbol": "TEST_WEBHOOK",
        "bid": 2700.0,
        "ask": 2700.5,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "received"
    assert data["buffer_size"] == 1

    latest = loop.run_until_complete(registry.latest_tick("TEST_WEBHOOK"))
    assert latest is not None and latest.bid == 2700.0
    loop.close()


def test_webhook_handler_404_for_unregistered_symbol():
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(webhook_router)
    client = TestClient(app)

    resp = client.post("/webhook/tradingview", json={
        "symbol": "UNREGISTERED",
        "bid": 2700.0,
        "ask": 2700.5,
    })
    assert resp.status_code == 404