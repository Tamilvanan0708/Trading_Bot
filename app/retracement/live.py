"""
RETRACEMENT_BOS_V1 — Live engine bridge (backend source of truth).

The Retracement BOS page must reflect the REAL current backend state instead
of a static historical snapshot.  This module keeps the exact
RETRACEMENT_BOS_V1 engine fed with the latest CLOSED 15m candles from the
live market service so that:

  * a new valid high          -> the engine raises the dynamic TP (1.000)
  * price touches 0.618       -> ENTRY_TOUCHED and TP frozen at that moment
  * later highs (E/F/G)       -> TP NEVER moves (post-entry hard lock)

State changes are persisted to the database idempotently: the same logical
setup is updated in place (same setup_id), so history stays consistent and no
duplicate rows/events or duplicate polling loops are created.

This module NEVER fabricates prices or levels and NEVER changes the engine's
strategy rules — the engine remains 100% deterministic over real candles.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.logging import logger
from app.data.live.service import get_live_service
from app.retracement.engine import RetracementBOSEngine
from app.retracement.models import (
    RetracementState,
    RetracementSetup,
)
from app.retracement.repository import RetracementRepository

_instances: dict[str, "RetracementLiveService"] = {}


def get_retracement_live_service(symbol: str = "XAUUSD") -> "RetracementLiveService":
    """Returns the shared live engine for a symbol (singleton per process)."""
    svc = _instances.get(symbol)
    if svc is None:
        svc = RetracementLiveService(symbol=symbol)
        _instances[symbol] = svc
    return svc


def _same_setup(a: RetracementSetup, b: RetracementSetup) -> bool:
    """Two setups are the same logical setup when they share the same BOS and
    Point 2 anchors (levels may legitimately differ as TP tracks new highs)."""
    if a.bos_price is None or b.bos_price is None:
        return False
    if a.point_2_price is None or b.point_2_price is None:
        return False
    return (
        abs(a.bos_price - b.bos_price) < 1e-9
        and abs(a.point_2_price - b.point_2_price) < 1e-9
        and abs(a.point_1_price - b.point_1_price) < 1e-9
    )


class RetracementLiveService:
    """Feeds the exact engine with live closed candles and persists state."""

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m",
                 m15_limit: int = 800) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.m15_limit = m15_limit
        self.engine: RetracementBOSEngine | None = None
        self._last_processed_ts: datetime | None = None
        self._seeded = False
        self._last_completed: RetracementSetup | None = None
        self.live_price: float | None = None
        self.data_status: str = "NO_DATA"
        self._has_live_data: bool = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Live data
    # ------------------------------------------------------------------

    async def _closed_candles(self) -> list:
        """Latest CLOSED 15m candles from the live market service."""
        try:
            service = get_live_service()
            snap = await service.get_multi_timeframe_snapshot(
                self.symbol, include_forming=False, m15_limit=self.m15_limit,
            )
            self.live_price = snap.current_price
            self.data_status = "HEALTHY"
            self._has_live_data = True
            return list(snap.m15)
        except Exception as exc:  # noqa: BLE001 - live feed unavailable
            logger.debug("[RETR-LIVE] closed candles unavailable: %s", exc)
            self.data_status = "NO_DATA"
            self._has_live_data = False
            return []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def advance(self, db) -> RetracementSetup | None:
        """Process any newly-closed live candles and persist state changes.

        Returns the current active setup (or None when none is active).
        Cheap when no new candle has closed (no reprocessing happens).
        """
        async with self._lock:
            candles = await self._closed_candles()
            if not candles:
                return self.engine.setup if self.engine is not None else None

            if self.engine is None:
                self.engine = RetracementBOSEngine(symbol=self.symbol, timeframe=self.timeframe)

            new_candles = [
                c for c in candles
                if self._last_processed_ts is None or c.timestamp > self._last_processed_ts
            ]
            if not new_candles:
                return self.engine.setup

            new_candles.sort(key=lambda c: c.timestamp)
            completed: list[RetracementSetup] = []
            for candle in new_candles:
                self.engine.process_candle(candle)
                archived = self.engine.archive_completed()
                if archived is not None:
                    completed.append(archived)

            self._last_processed_ts = new_candles[-1].timestamp
            if completed:
                # Remember the most recent completion so the DB active row can
                # be finalized (keeps exactly one active setup row).
                self._last_completed = completed[-1]

            await self._persist(db)
            return self.engine.setup

    def reset(self) -> None:
        """Drop cached engine state (used after a manual historical re-run so
        the next GET re-seeds from the live candles)."""
        self.engine = None
        self._last_processed_ts = None
        self._seeded = False
        self._last_completed = None
        self._has_live_data = False

    # ------------------------------------------------------------------
    # Persistence (idempotent)
    # ------------------------------------------------------------------

    async def _persist(self, db) -> None:
        repo = RetracementRepository(db)
        setup = self.engine.setup if self.engine is not None else None

        # 1) Finalize a persisted active setup that the engine just completed.
        if setup is None and self._last_completed is not None:
            await self._finalize_completed(repo, self._last_completed)

        # 2) Upsert the current active setup.
        if setup is not None and setup.point_2_price is not None:
            persisted = await repo.load_latest_active(self.symbol, strategy="RETRACEMENT_BOS_V1")
            inserted = False
            if persisted is not None:
                if _same_setup(persisted, setup):
                    # Same logical setup -> update the existing row in place.
                    setup.setup_id = persisted.setup_id
                else:
                    # Engine moved to a different setup -> finalize stale row.
                    await self._finalize_stale(repo, persisted)
                    inserted = True
            else:
                inserted = True
            await repo.save_setup(setup)
            if inserted:
                await self._save_events(repo, setup.setup_id)

        await db.commit()

    async def _finalize_completed(self, repo, completed: RetracementSetup) -> None:
        """Mark the persisted active row that corresponds to ``completed`` as done."""
        persisted = await repo.load_latest_active(self.symbol, strategy="RETRACEMENT_BOS_V1")
        if persisted is None or not _same_setup(persisted, completed):
            return
        for field in ("state", "outcome", "entry_touched", "entry_timestamp",
                      "tp_locked", "locked_tp", "completion_reason",
                      "invalidation_reason"):
            setattr(persisted, field, getattr(completed, field))
        await repo.save_setup(persisted)

    async def _finalize_stale(self, repo, persisted: RetracementSetup) -> None:
        """Mark an old persisted active setup as superseded (keeps one active row)."""
        persisted.state = RetracementState.INVALIDATED
        persisted.invalidation_reason = "Superseded by a newer live retracement setup."
        await repo.save_setup(persisted)

    async def _save_events(self, repo, setup_id: str) -> None:
        """Persist events for a newly-created setup (deduped by setup existence)."""
        if self.engine is None:
            return
        existing = await repo.load_events(setup_id, limit=1)
        if existing:
            return
        for event in self.engine._events:
            if event.setup_id == setup_id:
                await repo.save_event(event)
