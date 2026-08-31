"""
Live market data feed providers.

All feeds push normalized :class:`Tick` models into per-symbol
:class:`TickRingBuffer` instances managed by a shared :class:`FeedRegistry`.
"""

from app.data.live.registry import FeedRegistry

__all__ = ["FeedRegistry"]