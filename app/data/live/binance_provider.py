"""
Binance USD-M Futures feed for XAUUSDT perpetual contracts.

Connects to the public combined stream endpoint and normalizes
``bookTicker`` (best bid/ask) and ``aggTrade`` (last trade) frames into
:class:`Tick` models.  No authentication required; read-only public data.
"""

import json
import time
from datetime import datetime, timezone

from app.data.models import Tick
from app.data.websocket_provider import WebSocketMarketFeed

BINANCE_FUTURES_STREAM_URL = "wss://fstream.binance.com/stream"

# Logical symbol -> Binance USD-M futures ticker.
# XAUUSD (paper label) maps to the XAUUSDT perpetual contract.
BINANCE_SYMBOL_MAP = {
    "XAUUSD": "xauusdt",
    "XAUUSDT": "xauusdt",
}

STREAM_BOOK_TICKER = "bookTicker"
STREAM_AGG_TRADE = "aggTrade"
STREAM_KLINE_15M = "kline_15m"


def build_binance_stream_url(
    base_url: str = BINANCE_FUTURES_STREAM_URL,
    streams: list[str] | None = None,
) -> str:
    """Build the combined-stream URL from a list of ``symbol@event`` streams."""
    if not streams:
        raise ValueError("At least one stream is required.")
    return f"{base_url}?streams=" + "/".join(streams)


class BinanceGoldMarketProvider(WebSocketMarketFeed):
    """Real-time gold futures feed via Binance public WebSocket streams."""

    def __init__(
        self,
        symbol: str = "XAUUSD",
        streams: list[str] | None = None,
        capacity: int = 10_000,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 0,
    ) -> None:
        binance_symbol = BINANCE_SYMBOL_MAP.get(symbol.upper(), symbol.lower())
        self._streams = streams or [f"{binance_symbol}@{STREAM_BOOK_TICKER}", f"{binance_symbol}@{STREAM_AGG_TRADE}"]
        self._binance_symbol = binance_symbol
        url = build_binance_stream_url(streams=self._streams)
        super().__init__(
            url=url,
            symbols=[symbol],
            capacity=capacity,
            reconnect_delay=reconnect_delay,
            max_reconnect_attempts=max_reconnect_attempts,
        )

    @property
    def streams(self) -> list[str]:
        return list(self._streams)

    def parse_message(self, raw: str) -> list[Tick]:
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        data = message.get("data", message)
        event = data.get("e")
        timestamp = datetime.fromtimestamp(data.get("E", time.time()) / 1000.0, tz=timezone.utc)

        if event == STREAM_BOOK_TICKER:
            bid = float(data.get("b", 0.0))
            ask = float(data.get("a", 0.0))
            if bid <= 0 or ask <= 0:
                return []
            return [
                Tick(
                    symbol=self.symbols[0],
                    timestamp=timestamp,
                    bid=bid,
                    ask=ask,
                    last=(bid + ask) / 2.0,
                )
            ]

        if event == STREAM_AGG_TRADE:
            price = float(data.get("p", 0.0))
            if price <= 0:
                return []
            return [
                Tick(
                    symbol=self.symbols[0],
                    timestamp=timestamp,
                    last=price,
                    volume=float(data.get("q", 0.0)),
                )
            ]

        return []