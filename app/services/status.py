"""
System status tracker for observability endpoints.

Tracks the last feed tick, last closed candle, last analysis, last signal,
and last paper trade timestamps in-memory (mirrored to system_state where
useful) so /system-status can explain the current state of the agent.
"""

import asyncio
from datetime import datetime, timezone


class SystemStatus:
    """In-memory status tracker (single instance shared across the app)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state: dict[str, str | None] = {
            "started_at": None,
            "last_tick_at": None,
            "last_tick_price": None,
            "last_closed_candle_ts": None,
            "last_analysis_at": None,
            "last_analysis_score": None,
            "last_analysis_direction": None,
            "last_signal_at": None,
            "last_signal_id": None,
            "last_paper_trade_at": None,
            "last_paper_trade_id": None,
            "last_error": None,
            "next_analysis_at": None,
            "feed_connected": "unknown",
            "analysis_runs": "0",
            "trades_opened": "0",
        }

    async def _set(self, key: str, value: str) -> None:
        async with self._lock:
            self._state[key] = value

    async def _get(self, key: str) -> str | None:
        async with self._lock:
            return self._state.get(key)

    # --- updaters ---

    async def mark_started(self) -> None:
        await self._set("started_at", datetime.now(timezone.utc).isoformat())

    async def mark_tick(self, price: float) -> None:
        await self._set("last_tick_at", datetime.now(timezone.utc).isoformat())
        await self._set("last_tick_price", f"{price:.2f}")

    async def mark_candle(self, candle_ts: datetime) -> None:
        await self._set("last_closed_candle_ts", candle_ts.isoformat())

    async def mark_analysis(self, score: float, direction: str) -> None:
        await self._set("last_analysis_at", datetime.now(timezone.utc).isoformat())
        await self._set("last_analysis_score", f"{score:.1f}")
        await self._set("last_analysis_direction", direction)
        runs = int(await self._get("analysis_runs") or "0") + 1
        await self._set("analysis_runs", str(runs))

    async def mark_signal(self, signal_id: str) -> None:
        await self._set("last_signal_at", datetime.now(timezone.utc).isoformat())
        await self._set("last_signal_id", signal_id)

    async def mark_paper_trade(self, trade_id: str) -> None:
        await self._set("last_paper_trade_at", datetime.now(timezone.utc).isoformat())
        await self._set("last_paper_trade_id", trade_id)
        opened = int(await self._get("trades_opened") or "0") + 1
        await self._set("trades_opened", str(opened))

    async def mark_feed(self, connected: bool) -> None:
        await self._set("feed_connected", "connected" if connected else "disconnected")

    async def mark_error(self, message: str) -> None:
        await self._set("last_error", message[:500])

    async def set_next_analysis(self, ts: datetime) -> None:
        await self._set("next_analysis_at", ts.isoformat())

    # --- snapshot ---

    async def snapshot(self) -> dict:
        async with self._lock:
            return dict(self._state)


_status_instance: SystemStatus | None = None


def get_status() -> SystemStatus:
    global _status_instance
    if _status_instance is None:
        _status_instance = SystemStatus()
    return _status_instance