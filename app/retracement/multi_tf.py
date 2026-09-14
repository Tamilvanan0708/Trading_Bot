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
import os
from datetime import datetime
from typing import Any

from app.core.constants import TimeFrame
from app.core.logging import logger
from app.data.live.service import get_live_service
from app.retracement.fib_retracement_engine import DualRetracementEngine, FibRetracementEngine
from app.retracement.models import RetracementSetup, RetracementState
from app.retracement.repository import RetracementRepository

DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1h"]
TF_MAP: dict[str, TimeFrame] = {
    "5m": TimeFrame.M5,
    "15m": TimeFrame.M15,
    "30m": TimeFrame.M30,
    "1h": TimeFrame.H1,
    "4h": TimeFrame.H4,
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
        from app.config.execution_settings import get_execution_settings
        mode = getattr(get_execution_settings(), "fib_engine_mode", "classic")
        self.engine = DualRetracementEngine(symbol=symbol, timeframe=timeframe, engine_mode=mode)
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
        """Load real historical candles via Binance REST provider in parallel."""
        if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("APP_ENV") == "test":
            return {}

        import time
        now = time.time()
        if getattr(self, "_cached_hist_candles", None) and (now - getattr(self, "_last_bootstrap_ts", 0) < 120.0):
            return self._cached_hist_candles

        try:
            from app.config.settings import get_settings
            from app.data.live.binance_history import BinanceHistoryProvider
            provider = BinanceHistoryProvider(get_settings())
            result = {}

            tf_limits = {
                "5m": 120,
                "15m": 100,
                "30m": 80,
                "1h": 60,
                "4h": 50,
            }

            async def _fetch(tf_name):
                tf_limit = tf_limits.get(tf_name, 100)
                try:
                    c = await asyncio.wait_for(provider.get_ohlcv(self.symbol, TF_MAP[tf_name], limit=tf_limit), timeout=8.0)
                    if c and len(c) >= 30:
                        return tf_name, c
                except Exception as exc:
                    logger.warning("[RETR-MULTI] bootstrap REST fetch for %s failed: %s", tf_name, exc)
                return tf_name, []

            fetched = await asyncio.gather(*[_fetch(tf) for tf in self.timeframes], return_exceptions=True)
            for item in fetched:
                if isinstance(item, tuple) and len(item) == 2 and item[1]:
                    result[item[0]] = item[1]
            if result:
                self._cached_hist_candles = result
                self._last_bootstrap_ts = now
            return result
        except Exception as exc:  # noqa: BLE001
            logger.error("[RETR-MULTI] historical bootstrap failed: %s", exc)
            return getattr(self, "_cached_hist_candles", {})


    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def advance(self, db, live_price: float | None = None) -> dict[str, RetracementSetup | None]:
        """Process newly-closed candles on all timeframes with Multi-Slot Parallel Execution:
        - Every timeframe (5m, 15m, 30m, 1h) maintains its own independent trading slot.
        - All timeframe engines co-exist and execute concurrently with zero cross-timeframe lockout.
        - Real-time live price ticks evaluate instant touch for Entry, TP, and SL without waiting for candle close.
        """
        async with self._lock:
            snap = await self._snapshot()
            if live_price is not None:
                self.live_price = live_price
            elif snap and snap.current_price:
                self.live_price = snap.current_price

            snap_key = (
                snap.timestamp if snap else None,
                len(snap.m15) if (snap and hasattr(snap, "m15")) else 0,
                live_price if live_price is not None else (snap.current_price if snap else None),
            )
            if snap is not None and getattr(self, "_last_snap_key", None) == snap_key and getattr(self, "_last_advance_results", None) is not None:
                return self._last_advance_results

            raw_results: dict[str, RetracementSetup | None] = {}

            # Check if any slot has fewer than 30 candles or has a stale gap
            slots_needing_candles = []
            now_utc = datetime.now(timezone.utc)
            for tf in self.timeframes:
                series = list(snap.get_series(TF_MAP[tf])) if snap is not None else []
                is_stale = False
                if not series or len(series) < 30:
                    is_stale = True
                elif len(series) >= 10:
                    last_c_ts = series[-1].timestamp
                    if last_c_ts.tzinfo is None:
                        last_c_ts = last_c_ts.replace(tzinfo=timezone.utc)
                    if (now_utc - last_c_ts).total_seconds() > 3600:
                        is_stale = True
                    else:
                        recent = series[-25:]
                        for i in range(len(recent) - 1):
                            t_prev = recent[i].timestamp.replace(tzinfo=timezone.utc) if recent[i].timestamp.tzinfo is None else recent[i].timestamp
                            t_next = recent[i+1].timestamp.replace(tzinfo=timezone.utc) if recent[i+1].timestamp.tzinfo is None else recent[i+1].timestamp
                            if (t_next - t_prev).total_seconds() > 14400:
                                is_stale = True
                                break
                if is_stale or self.slots[tf].last_processed_ts is None or len(self.slots[tf].engine._candles) < 30:
                    slots_needing_candles.append(tf)

            hist_candles: dict[str, list] = {}
            if slots_needing_candles:
                hist_candles = await self._bootstrap_from_history()

            for tf in self.timeframes:
                slot = self.slots[tf]
                snap_candles = list(snap.get_series(TF_MAP[tf])) if snap is not None else []
                candles: list = []

                has_large_gap = False
                if len(snap_candles) >= 10:
                    recent = snap_candles[-25:]
                    for i in range(len(recent) - 1):
                        t_prev = recent[i].timestamp.replace(tzinfo=timezone.utc) if recent[i].timestamp.tzinfo is None else recent[i].timestamp
                        t_next = recent[i+1].timestamp.replace(tzinfo=timezone.utc) if recent[i+1].timestamp.tzinfo is None else recent[i+1].timestamp
                        if (t_next - t_prev).total_seconds() > 14400:
                            has_large_gap = True
                            break

                if hist_candles.get(tf) and (has_large_gap or len(snap_candles) < 30):
                    candles = hist_candles[tf]
                    if snap_candles:
                        last_hist_ts = candles[-1].timestamp if candles else None
                        new_live = [c for c in snap_candles if last_hist_ts is None or c.timestamp > last_hist_ts]
                        candles = candles + new_live
                    slot.live_price = self.live_price
                    slot.data_status = "HEALTHY"
                    slot.has_live_data = True
                elif len(snap_candles) >= 30 and not has_large_gap:
                    candles = snap_candles
                    slot.live_price = self.live_price
                    slot.data_status = "HEALTHY"
                    slot.has_live_data = True
                elif hist_candles.get(tf):
                    candles = hist_candles[tf]
                    if snap_candles:
                        last_hist_ts = candles[-1].timestamp if candles else None
                        new_live = [c for c in snap_candles if last_hist_ts is None or c.timestamp > last_hist_ts]
                        candles = candles + new_live
                    slot.live_price = self.live_price
                    slot.data_status = "HEALTHY"
                    slot.has_live_data = True
                elif snap_candles:
                    candles = snap_candles
                    slot.live_price = self.live_price
                    slot.data_status = "HEALTHY"
                    slot.has_live_data = bool(candles)
                else:
                    slot.data_status = "NO_DATA"
                    slot.has_live_data = False
                eff_price = live_price if (live_price is not None and live_price > 0) else (self.live_price or getattr(slot, "live_price", None))
                raw_results[tf] = self._advance_slot(slot, candles, live_price=eff_price)

            # Option 1A: Multi-Slot Parallel Execution — each timeframe maintains its own active slot
            results = raw_results

            await self._persist(db)
            self._last_snap_key = snap_key
            self._last_advance_results = results
            return results

    def _advance_slot(self, slot: _TFSlot, candles: list, live_price: float | None = None) -> RetracementSetup | None:
        """Advance ONE timeframe engine with its own newly-closed candles and live price tick."""
        from app.config.execution_settings import get_execution_settings
        slot.engine.engine_mode = getattr(get_execution_settings(), "fib_engine_mode", "classic")
        completed: list[RetracementSetup] = []

        if candles:
            new_candles = [
                c for c in candles
                if slot.last_processed_ts is None or c.timestamp > slot.last_processed_ts
            ]
            if new_candles:
                new_candles.sort(key=lambda c: c.timestamp)
                for candle in new_candles:
                    slot.engine.process_candle(candle)
                    archived = slot.engine.archive_completed()
                    if archived is not None:
                        completed.append(archived)
                slot.last_processed_ts = new_candles[-1].timestamp

        # Instant Tick Touch Execution: Only evaluate real-time live price when an actual live tick is provided
        if live_price is not None and live_price > 0:
            slot.engine.evaluate_live_price(live_price)
            archived = slot.engine.archive_completed()
            if archived is not None:
                completed.append(archived)

        if completed:
            slot.last_completed = completed[-1]
        return slot.engine.setup

    def reset(self) -> None:
        """Drop all cached engine state (re-seed on next advance)."""
        for slot in self.slots.values():
            slot.reset()
        self.live_price = None
        self.data_status = "NO_DATA"
        self._cached_hist_candles = {}
        self._last_bootstrap_ts = 0
        self._last_snap_key = None
        self._last_advance_results = None

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

            # 1) Finalize a persisted active setup if this timeframe has no active setup in engine.
            if setup is None:
                persisted = await repo.load_latest_active(
                    self.symbol, strategy="RETRACEMENT_BOS_V1", timeframe=tf)
                if persisted is not None:
                    if slot.last_completed is not None and _same_setup(persisted, slot.last_completed):
                        await self._finalize_completed(repo, tf, slot.last_completed)
                    else:
                        await self._finalize_stale(repo, tf, persisted)

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
