"""
Fixed-capacity, asyncio-safe ring buffer for real-time tick caching.
Feeds push ticks asynchronously; API /dashboard consumers read snapshots
without blocking the producer.
"""

import asyncio
from collections import deque

from app.data.models import Tick


class TickRingBuffer:
    """Asyncio-safe ring buffer keeping the most recent *capacity* ticks.

    Designed for concurrent access: one background feed task pushes ticks
    while API handlers read the latest snapshot.  All mutating/reading
    operations are guarded by an ``asyncio.Lock``.
    """

    __slots__ = ("_buffer", "_capacity", "_lock")

    def __init__(self, capacity: int = 10_000) -> None:
        if capacity <= 0:
            raise ValueError("Ring buffer capacity must be positive.")
        self._capacity = capacity
        self._buffer: deque[Tick] = deque(maxlen=capacity)
        self._lock = asyncio.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    async def push(self, tick: Tick) -> None:
        async with self._lock:
            self._buffer.append(tick)

    async def latest(self) -> Tick | None:
        async with self._lock:
            return self._buffer[-1] if self._buffer else None

    async def snapshot(self, limit: int | None = None) -> list[Tick]:
        async with self._lock:
            items = list(self._buffer)
        if limit is not None and limit > 0:
            items = items[-limit:]
        return items

    async def clear(self) -> None:
        async with self._lock:
            self._buffer.clear()

    async def size(self) -> int:
        async with self._lock:
            return len(self._buffer)