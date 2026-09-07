"""
Generic WebSocket market feed consumer.

Subclass :class:`WebSocketMarketFeed`, implement ``build_subscribe_message``
(optional) and ``parse_message`` (required), then ``await feed.start()`` to
spawn a self-healing background task that maintains per-symbol tick ring
buffers and reconnects with exponential backoff on disconnect.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any

import websockets
import websockets.asyncio.client

from app.core.logging import logger
from app.data.models import Tick
from app.data.ring_buffer import TickRingBuffer


class FeedConnectionError(Exception):
    """Raised when a live feed exhausts its reconnect budget."""


class WebSocketMarketFeed(ABC):
    """Base class for WebSocket-based real-time market feeds."""

    def __init__(
        self,
        url: str,
        symbols: list[str],
        capacity: int = 10_000,
        reconnect_delay: float = 1.0,
        max_reconnect_attempts: int = 0,
        buffers: dict[str, TickRingBuffer] | None = None,
    ) -> None:
        if not symbols:
            raise ValueError("WebSocketMarketFeed requires at least one symbol.")
        if reconnect_delay <= 0:
            raise ValueError("reconnect_delay must be positive.")
        if max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts cannot be negative.")

        self._url = url
        self._symbols = list(symbols)
        self._buffers: dict[str, TickRingBuffer] = buffers or {
            symbol: TickRingBuffer(capacity) for symbol in symbols
        }
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_attempts = max_reconnect_attempts

        self._connected = False
        self._running = False
        self._task: asyncio.Task | None = None
        self._on_tick_callback: Any = None  # Optional async callable(tick)
        self._on_reconnect_callback: Any = None  # Optional async callable()
        self._ever_connected = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_running(self) -> bool:
        return self._running

    def buffer_for(self, symbol: str) -> TickRingBuffer | None:
        return self._buffers.get(symbol)

    def set_on_tick_callback(self, callback) -> None:
        """Registers an async callback invoked for every parsed tick."""
        self._on_tick_callback = callback

    def set_on_reconnect_callback(self, callback) -> None:
        """Registers an async callback invoked after a successful RECONNECT
        (not on the initial connection).  Used to trigger history backfill."""
        self._on_reconnect_callback = callback

    def start(self) -> None:
        """Spawn the background receive loop task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name=f"feed:{self.__class__.__name__}")

    async def stop(self) -> None:
        """Gracefully stop the receive loop and wait for the task to finish."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._connected = False
        self._task = None

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def parse_message(self, raw: str) -> list[Tick]:
        """Parse a raw WS frame into zero or more normalized Tick models."""

    def build_subscribe_message(self) -> str | None:
        """Return a subscription frame to send after connect (None to skip)."""
        return None

    def build_followup_messages(self, message: dict) -> list[str]:
        """Return additional frames to send in response to a server frame.

        The default returns no follow-ups.  Subclasses (e.g. protocols that
        must wait for an AUTH confirmation before subscribing) override this
        to emit extra frames.
        """
        return []

    def encode_message(self, message: Any) -> str:
        """Serialize a subscription message to a string frame."""
        if isinstance(message, str):
            return message
        return json.dumps(message)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _dispatch(self, tick: Tick) -> None:
        # Notify the registered aggregation callback (e.g. LiveMarketDataService)
        # so candle-building and status tracking happen for every live tick.
        if self._on_tick_callback is not None:
            try:
                await self._on_tick_callback(tick)
                return
            except Exception as exc:  # noqa: BLE001 - never drop the feed
                logger.error("Feed tick callback failed: %s", exc)

        buffer = self._buffers.get(tick.symbol)
        if buffer is None:
            logger.debug("Dropping tick for unsubscribed symbol %s", tick.symbol)
            return
        await buffer.push(tick)

    async def _on_message(self, raw: str, ws=None) -> None:
        ticks = self.parse_message(raw)
        for tick in ticks:
            await self._dispatch(tick)
        # Allow protocol-specific follow-up frames (e.g. subscribe after AUTH).
        if ws is not None:
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                message = {}
            if isinstance(message, dict):
                for frame in self.build_followup_messages(message):
                    await ws.send(self.encode_message(frame))

    async def _run(self) -> None:
        attempt = 0
        while self._running:
            try:
                await self._listen_loop()
            except asyncio.CancelledError:
                raise
            except FeedConnectionError:
                logger.error("Feed %s exhausted reconnect budget; giving up.", self._url)
                break
            except Exception as exc:  # noqa: BLE001 - reconnect on any transport error
                logger.warning(
                    "Feed %s connection error: %s (attempt %d)",
                    self._url, exc, attempt,
                )
            finally:
                self._connected = False

            if not self._running:
                break

            attempt += 1
            if self._max_reconnect_attempts and attempt > self._max_reconnect_attempts:
                raise FeedConnectionError(
                    f"Feed {self._url} failed after {attempt} reconnect attempts."
                )
            delay = self._reconnect_delay * min(2 ** (attempt - 1), 16)
            logger.info("Feed %s reconnecting in %.1fs...", self._url, delay)
            await asyncio.sleep(delay)

        self._running = False
        logger.info("Feed %s stopped.", self._url)

    async def _listen_loop(self) -> None:
        async with websockets.asyncio.client.connect(
            self._url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._connected = True
            # Fire reconnect callback only on RECONNECTS (not the initial connect)
            if self._ever_connected and self._on_reconnect_callback is not None:
                try:
                    await self._on_reconnect_callback()
                except Exception as exc:  # noqa: BLE001 - never drop the feed
                    logger.error("Feed reconnect callback failed: %s", exc)
            self._ever_connected = True
            logger.info("Feed %s connected.", self._url)
            subscribe = self.build_subscribe_message()
            if subscribe is not None:
                await ws.send(self.encode_message(subscribe))
            async for raw in ws:
                if not self._running:
                    break
                await self._on_message(raw, ws=ws)