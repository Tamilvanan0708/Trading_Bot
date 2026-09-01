"""
RETRACEMENT_BOS_V1 — Multi-timeframe live monitor (15m / 30m / 1h).

The agent continuously monitors ALL THREE timeframes INDEPENDENTLY.  Each
timeframe runs its own ``RetracementBOSEngine`` instance with its own state
machine, setup lifecycle and persistence keyed by
``(symbol, strategy, timeframe)``.

Timetimeframe isolation is strict:

  * a setup detected on 15M never overwrites the 30M or 1H setup
  * a setup detected on 30M never overwrites the 15M or 1H setup
  * a setup detected on 1H never overwrites the 15M or 30M setup

All three states coexist simultaneously, e.g.::

    15M: WAITING_FOR_ENTRY
    30M: ENTRY_TOUCHED -> TP_LOCKED
    1H : BOS_CONFIRMED -> FIB_ACTIVE

``advance`` feeds each engine only the NEWLY closed candles for its own
timeframe, so a new valid high on 15M updates the 15M dynamic TP without
touching 30M/1H.  When a setup completes/invalidates on any timeframe, that
timeframe immediately keeps scanning for the next valid BOS + retracement.

This module NEVER fabricates prices or levels and NEVER changes the engine's
strategy rules — each engine remains 100% deterministic over real candles.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.core.constants import TimeFrame
from app.core.logging import logger
from app.data.live.service import get_live_service
from app.retracement.dual_engine import DualRetracementEngine
from app.retracement.models import RetracementSetup, RetracementState
from app.retracement.repository import RetracementRepository

# Timeframes monitored continuously: 5m → 15m → 30m → 1h (4H REMOVED per user request)
DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1h"]
TF_MAP: dict[str, TimeFrame] = {
    "5m": TimeFrame.M5,
    "15m": TimeFrame.M15,
    "30m": TimeFrame.M30,
    "1h": TimeFrame.H1,
}

_instances: dict[str, RetracementMultiTFMonitor] = {}


def get_retracement_multi_tf_service(symbol: str = "XAUUSD") -> RetracementMultiTFMonitor:
    """Returns the shared multi-timeframe monitor for a symbol (singleton)."""
    svc = _instances.get(symbol)
    if svc is None:
        svc = RetracementMultiTFMonitor(symbol=symbol)
        _instances[symbol] = svc
    return svc


def _same_setup(a: RetracementSetup, b: RetracementSetup) -> bool:
    """Two setups are the same logical setup when they share the same BOS,
    Point 1 and Point 2 anchors (levels may differ as TP tracks new highs)."""
    if a.bos_price is None or b.bos_price is None:
        return False
    if a.point_2_price is None or b.point_2_price is None:
        return False
    return (
        abs(a.bos_price - b.bos_price) < 1e-9
        and abs(a.point_2_price - b.point_2_price) < 1e-9
        and abs(a.point_1_price - b.point_1_price) < 1e-9
    )


class _TFSlot:
    """One independent timeframe slot: its own dual engine + tracking state."""

    def __init__(self, symbol: str, timeframe: str) -> None:
        self.timeframe = timeframe
        self.engine = DualRetracementEngine(symbol=symbol, timeframe=timeframe)
        self.last_processed_ts: datetime | None = None
        self.last_completed: RetracementSetup | None = None
        self.live_price: float | None = None
        self.data_status: str = "NO_DATA"
        self.has_live_data: bool = False

    def reset(self) -> None:
        self.engine.reset()
        self.last_processed_ts = None
        self.last_completed = None
        self.has_live_data = False


class RetracementMultiTFMonitor:
    """Monitors 15m / 30m / 1h retracement structures independently."""

    def __init__(self, symbol: str = "XAUUSD",
                 timeframes: list[str] | None = None) -> None:
        self.symbol = symbol
        self.timeframes = list(timeframes or DEFAULT_TIMEFRAMES)
        self.slots: dict[str, _TFSlot] = {
            tf: _TFSlot(symbol, tf) for tf in self.timeframes
        }
        self.live_price: float | None = None
        self.data_status: str = "NO_DATA"
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Live data (one snapshot feeds all three independent engines)
    # ------------------------------------------------------------------

    async def _snapshot(self) -> Any:
        """Latest CLOSED multi-timeframe snapshot from the live market service."""
        try:
            service = get_live_service()
            snap = await service.get_multi_timeframe_snapshot(
                self.symbol, include_forming=False, m15_limit=800,
            )
            self.live_price = snap.current_price
            self.data_status = "HEALTHY"
            return snap
        except Exception as exc:  # noqa: BLE001 - live feed unavailable
            logger.debug("[RETR-MULTI] live snapshot unavailable: %s", exc)
            self.data_status = "HISTORICAL"
            self.live_price = None
            return None

    async def _bootstrap_from_history(self) -> dict[str, list]:
        """Load real historical candles from live service as fallback when WebSocket feed is offline.

        Returns a dict of tf -> candle list for all monitored timeframes.
        Always uses real Binance candles — never fabricates data.
        """
        try:
            service = get_live_service()
            await service._load_historical_base()
            await service._load_5m_base()
            from app.core.constants import TimeFrame as TF
            snap = await service.get_multi_timeframe_snapshot(
                self.symbol, include_forming=False, m15_limit=800,
            )
            result = {}
            for tf in self.timeframes:
                result[tf] = list(snap.get_series(TF_MAP[tf]))
            if snap.current_price:
                self.live_price = snap.current_price
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RETR-MULTI] historical bootstrap failed: %s", exc)
            return {}


    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def advance(self, db) -> dict[str, RetracementSetup | None]:
        """Process newly-closed candles on all timeframes with Winner-Takes-All Policy:
        - If ANY timeframe triggers an entry touch, it instantly becomes the MASTER ACTIVE TRADE.
        - All other timeframe setups are immediately PURGED / RESET to prevent multi-trade clutter.
        - When the master active trade completes (TP/SL hit), scanning resumes across all timeframes.
        """
        async with self._lock:
            snap = await self._snapshot()
            raw_results: dict[str, RetracementSetup | None] = {}

            # Check if any engine has already been seeded
            engines_seeded = any(slot.last_processed_ts is not None for slot in self.slots.values())

            # Bootstrap from real Binance historical candles when:
            # 1. Engines have never been seeded (first startup), AND
            # 2. Either snap is None (feed offline) OR snap returns too few 15M candles for swing detection (DEGRADED feed)
            snap_15m_count = len(list(snap.get_series(TF_MAP["15m"]))) if snap is not None else 0
            needs_bootstrap = not engines_seeded and snap_15m_count < 50

            hist_candles: dict[str, list] = {}
            if needs_bootstrap:
                logger.info(
                    "[RETR-MULTI] Engines unseeded (15M candles from snap=%d < 50) — bootstrapping from historical Binance candles.",
                    snap_15m_count,
                )
                hist_candles = await self._bootstrap_from_history()

            for tf in self.timeframes:
                slot = self.slots[tf]
                candles: list = []
                # Priority 1: Use live snapshot candles if we have enough
                snap_candles = list(snap.get_series(TF_MAP[tf])) if snap is not None else []
                if snap_candles and (engines_seeded or len(snap_candles) >= 50):
                    candles = snap_candles
                    slot.live_price = snap.current_price
                    slot.data_status = "HEALTHY"
                    slot.has_live_data = True
                elif hist_candles.get(tf):
                    # Priority 2: Use bootstrapped historical candles
                    candles = hist_candles[tf]
                    # Merge any new snap candles on top of historical
                    if snap_candles:
                        last_hist_ts = candles[-1].timestamp if candles else None
                        new_live = [c for c in snap_candles if last_hist_ts is None or c.timestamp > last_hist_ts]
                        candles = candles + new_live
                    if snap is not None and snap.current_price:
                        slot.live_price = snap.current_price
                    slot.data_status = "HISTORICAL"
                    slot.has_live_data = True
                elif snap_candles:
                    # Priority 3: Use whatever snap candles exist (even if few)
                    candles = snap_candles
                    slot.live_price = snap.current_price if snap else None
                    slot.data_status = "HEALTHY"
                    slot.has_live_data = bool(candles)
                else:
                    slot.data_status = "NO_DATA"
                    slot.has_live_data = False
                raw_results[tf] = self._advance_slot(slot, candles)

            # --- WINNER-TAKES-ALL EXECUTION ENGINE ---
            # 1. Check if any timeframe has triggered an ACTIVE ENTRY TOUCH
            winner_tf: str | None = None
            earliest_touch_ts = None

            for tf in self.timeframes:
                setup = raw_results.get(tf)
                if setup and getattr(setup, "entry_touched", False) and not getattr(setup, "outcome", None):
                    # Found an active trade
                    touch_ts = getattr(setup, "entry_timestamp", None) or getattr(setup, "created_at", None)
                    if winner_tf is None or (touch_ts and earliest_touch_ts and touch_ts < earliest_touch_ts):
                        winner_tf = tf
                        earliest_touch_ts = touch_ts

            # 2. If a Winner Active Trade exists, PURGE / RESET all other timeframes immediately!
            results: dict[str, RetracementSetup | None] = {}
            if winner_tf is not None:
                for tf in self.timeframes:
                    if tf == winner_tf:
                        results[tf] = raw_results[tf]
                    else:
                        # Reset the non-winner slot engine so it holds NO active trade or pending setup
                        self.slots[tf].engine.reset()
                        results[tf] = None
            else:
                # No active trade running yet -> Keep scanning setups on all timeframes
                results = raw_results

            await self._persist(db)
            return results

    def _advance_slot(self, slot: _TFSlot, candles: list) -> RetracementSetup | None:
        """Advance ONE timeframe engine with its own newly-closed candles."""
        if not candles:
            return slot.engine.setup
        new_candles = [
            c for c in candles
            if slot.last_processed_ts is None or c.timestamp > slot.last_processed_ts
        ]
        if not new_candles:
            return slot.engine.setup
        new_candles.sort(key=lambda c: c.timestamp)
        completed: list[RetracementSetup] = []
        for candle in new_candles:
            slot.engine.process_candle(candle)
            archived = slot.engine.archive_completed()
            if archived is not None:
                completed.append(archived)
        slot.last_processed_ts = new_candles[-1].timestamp
        if completed:
            slot.last_completed = completed[-1]
        return slot.engine.setup

    def reset(self) -> None:
        """Drop all cached engine state (re-seed on next advance)."""
        for slot in self.slots.values():
            slot.reset()
        self.live_price = None
        self.data_status = "NO_DATA"

    def current_state(self) -> dict[str, RetracementSetup | None]:
        """Snapshot of the current active setup per timeframe (no I/O)."""
        return {
            tf: self.slots[tf].engine.setup for tf in self.timeframes
        }

    # ------------------------------------------------------------------
    # Persistence (idempotent, isolated per timeframe)
    # ------------------------------------------------------------------

    async def _persist(self, db) -> None:
        for tf in self.timeframes:
            slot = self.slots[tf]
            setup = slot.engine.setup
            repo = RetracementRepository(db)

            # 1) Finalize a persisted active setup this timeframe just completed.
            if setup is None and slot.last_completed is not None:
                await self._finalize_completed(repo, tf, slot.last_completed)

            # 2) Upsert the current active setup for THIS timeframe only.
            if setup is not None and setup.point_2_price is not None:
                persisted = await repo.load_latest_active(
                    self.symbol, strategy="RETRACEMENT_BOS_V1", timeframe=tf)
                inserted = False
                if persisted is not None:
                    if _same_setup(persisted, setup):
                        setup.setup_id = persisted.setup_id
                    else:
                        await self._finalize_stale(repo, tf, persisted)
                        inserted = True
                else:
                    inserted = True
                await repo.save_setup(setup)
                if inserted:
                    await self._save_events(repo, slot, setup.setup_id)

        await db.commit()

    async def _finalize_completed(self, repo, timeframe: str,
                                  completed: RetracementSetup) -> None:
        """Mark the persisted active row (same timeframe) as done."""
        persisted = await repo.load_latest_active(
            self.symbol, strategy="RETRACEMENT_BOS_V1", timeframe=timeframe)
        if persisted is None or not _same_setup(persisted, completed):
            return
        for field in ("state", "outcome", "entry_touched", "entry_timestamp",
                      "tp_locked", "locked_tp", "completion_reason",
                      "invalidation_reason"):
            setattr(persisted, field, getattr(completed, field))
        await repo.save_setup(persisted)

    async def _finalize_stale(self, repo, timeframe: str,
                              persisted: RetracementSetup) -> None:
        """Mark an old persisted active row (same timeframe) as superseded."""
        persisted.state = RetracementState.INVALIDATED
        persisted.invalidation_reason = (
            f"Superseded by a newer live retracement setup ({timeframe}).")
        await repo.save_setup(persisted)

    async def _save_events(self, repo, slot: _TFSlot, setup_id: str) -> None:
        """Persist events for a newly-created setup (deduped by existence)."""
        existing = await repo.load_events(setup_id, limit=1)
        if existing:
            return
        for event in slot.engine._events:
            if event.setup_id == setup_id:
                await repo.save_event(event)
