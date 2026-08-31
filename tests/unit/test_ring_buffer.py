"""
Unit tests for the asyncio-safe tick ring buffer.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from app.data.models import Tick
from app.data.ring_buffer import TickRingBuffer


def _tick(price: float) -> Tick:
    return Tick(
        symbol="XAUUSD",
        timestamp=datetime.now(timezone.utc),
        bid=price,
        ask=price + 0.1,
    )


def test_invalid_capacity_rejected():
    with pytest.raises(ValueError):
        TickRingBuffer(capacity=0)
    with pytest.raises(ValueError):
        TickRingBuffer(capacity=-5)


@pytest.mark.asyncio
async def test_push_latest_and_size():
    buf = TickRingBuffer(capacity=100)
    assert await buf.size() == 0

    t = _tick(2650.0)
    await buf.push(t)
    assert await buf.size() == 1
    latest = await buf.latest()
    assert latest is not None and latest.bid == 2650.0

    assert await buf.latest() == t


@pytest.mark.asyncio
async def test_ring_buffer_evicts_oldest_on_overflow():
    buf = TickRingBuffer(capacity=3)
    for price in (1.0, 2.0, 3.0, 4.0):
        await buf.push(_tick(price))

    assert await buf.size() == 3
    snap = await buf.snapshot()
    assert [t.bid for t in snap] == [2.0, 3.0, 4.0]


@pytest.mark.asyncio
async def test_snapshot_limit_returns_most_recent():
    buf = TickRingBuffer(capacity=10)
    for price in (1.0, 2.0, 3.0, 4.0, 5.0):
        await buf.push(_tick(price))

    snap = await buf.snapshot(limit=2)
    assert [t.bid for t in snap] == [4.0, 5.0]


@pytest.mark.asyncio
async def test_concurrent_pushes_do_not_lose_ticks():
    buf = TickRingBuffer(capacity=500)

    async def producer(start: int):
        for i in range(start, start + 100):
            await buf.push(_tick(float(i)))

    await asyncio.gather(producer(1), producer(101), producer(201), producer(301))
    assert await buf.size() == 400


@pytest.mark.asyncio
async def test_clear_resets_buffer():
    buf = TickRingBuffer(capacity=10)
    await buf.push(_tick(1.0))
    await buf.clear()
    assert await buf.size() == 0
    assert await buf.latest() is None
