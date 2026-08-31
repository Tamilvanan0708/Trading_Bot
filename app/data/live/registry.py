"""
Central registry of live market feeds and their tick ring buffers.

The registry owns the per-symbol :class:`TickRingBuffer` instances so that
any consumer (WebSocket feeds, webhook receivers, API routes) writes into /
reads from the same in-memory cache without explicit coupling.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.core.logging import logger
from app.data.models import FeedHealth, Tick
from app.data.ring_buffer import TickRingBuffer
from app.data.websocket_provider import WebSocketMarketFeed


class FeedRegistry:
    """Singleton registry mapping symbols to live tick ring buffers."""

    _instance: Optional["FeedRegistry"] = None

    def __init__(self) -> None:
        self._buffers: dict[str, TickRingBuffer] = {}
        self._feeds: dict[str, WebSocketMarketFeed] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "FeedRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Buffers
    # ------------------------------------------------------------------

    async def register_buffer(self, symbol: str, capacity: int = 10_000) -> TickRingBuffer:
        async with self._lock:
            buffer = self._buffers.get(symbol)
            if buffer is None:
                buffer = TickRingBuffer(capacity)
                self._buffers[symbol] = buffer
            return buffer

    async def get_buffer(self, symbol: str) -> TickRingBuffer | None:
        async with self._lock:
            return self._buffers.get(symbol)

    async def push_tick(self, tick: Tick) -> bool:
        """Push a tick directly (used by webhook receivers)."""
        buffer = await self.get_buffer(tick.symbol)
        if buffer is None:
            return False
        await buffer.push(tick)
        return True

    # ------------------------------------------------------------------
    # Feeds
    # ------------------------------------------------------------------

    async def register_feed(self, feed: WebSocketMarketFeed) -> None:
        async with self._lock:
            for symbol in feed.symbols:
                if symbol not in self._buffers:
                    self._buffers[symbol] = feed.buffer_for(symbol) or TickRingBuffer(10_000)
            self._feeds[feed.__class__.__name__] = feed
        feed.start()
        logger.info("Registered live feed %s for %s", feed.__class__.__name__, feed.symbols)

    async def start_all(self) -> None:
        async with self._lock:
            feeds = list(self._feeds.values())
        for feed in feeds:
            feed.start()

    async def stop_all(self) -> None:
        async with self._lock:
            feeds = list(self._feeds.values())
        for feed in feeds:
            await feed.stop()
        logger.info("Stopped all registered live feeds.")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def latest_tick(self, symbol: str) -> Tick | None:
        buffer = await self.get_buffer(symbol)
        if buffer is None:
            return None
        return await buffer.latest()

    async def snapshot(self, symbol: str, limit: int | None = None) -> list[Tick]:
        buffer = await self.get_buffer(symbol)
        if buffer is None:
            return []
        return await buffer.snapshot(limit=limit)

    async def health(self) -> list[FeedHealth]:
        """Build a health snapshot for every registered buffer/feed."""
        results: list[FeedHealth] = []
        async with self._lock:
            buffer_items = list(self._buffers.items())
        for symbol, buffer in buffer_items:
            feed = next((f for f in self._feeds.values() if symbol in f.symbols), None)
            latest = await buffer.latest()
            results.append(
                FeedHealth(
                    provider=feed.__class__.__name__ if feed else "manual",
                    symbol=symbol,
                    connected=feed.is_connected if feed else True,
                    running=feed.is_running if feed else False,
                    ticks_cached=await buffer.size(),
                    buffer_capacity=buffer.capacity,
                    latest_tick=latest,
                    metadata={
                        "registered_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )
        return results