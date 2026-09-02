"""
SMC With Fib — Multi-Timeframe Cascading Monitor (5M -> 15M -> 30M -> 1H -> 4H).

Cascading & Single Active Trade Policy (User Rule):
  - Cascades from 5m -> 15m -> 30m -> 1h -> 4h.
  - If a lower timeframe has a valid setup or active trade (e.g. 5M), it LOCKS
    as the primary active timeframe.
  - ONLY ONE trade can be running at any given time.
  - Higher timeframes remain on STANDBY until the active lower-timeframe trade completes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.core.constants import TimeFrame
from app.core.logging import logger
from app.data.live.service import get_live_service
from app.retracement.smc_fib_engine import SMCFibEngine

# Focus mode: 5M ONLY (all other timeframes disabled per user request)
DEFAULT_TIMEFRAMES = ["5m"]
TF_MAP: dict[str, TimeFrame] = {
    "5m": TimeFrame.M5,
    "15m": TimeFrame.M15,
    "30m": TimeFrame.M30,
    "1h": TimeFrame.H1,
    "4h": TimeFrame.H4,
}

_smc_instances: dict[str, SMCFibMultiTFMonitor] = {}


def get_smc_fib_multi_tf_service(symbol: str = "XAUUSD") -> SMCFibMultiTFMonitor:
    svc = _smc_instances.get(symbol)
    if svc is None:
        svc = SMCFibMultiTFMonitor(symbol=symbol)
        _smc_instances[symbol] = svc
    return svc


class _SMC_TFSlot:
    def __init__(self, symbol: str, timeframe: str) -> None:
        self.timeframe = timeframe
        self.engine = SMCFibEngine(symbol=symbol, timeframe=timeframe)
        self.last_processed_ts: datetime | None = None
        self.has_live_data: bool = False
        self.live_price: float | None = None

    def reset(self) -> None:
        self.engine._reset_setup()
        self.engine._history = []
        self.last_processed_ts = None
        self.has_live_data = False
        self.live_price = None


class SMCFibMultiTFMonitor:
    """Cascading monitor for SMC With Fib across 5 timeframes."""

    def __init__(self, symbol: str = "XAUUSD", timeframes: list[str] | None = None) -> None:
        self.symbol = symbol
        self.timeframes = list(timeframes or DEFAULT_TIMEFRAMES)
        self.slots: dict[str, _SMC_TFSlot] = {
            tf: _SMC_TFSlot(symbol, tf) for tf in self.timeframes
        }
        self.live_price: float | None = None
        self.data_status: str = "NO_DATA"
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        """Clear all in-memory engine state so engines re-seed from live data."""
        for slot in self.slots.values():
            slot.reset()
        self.live_price = None
        self.data_status = "NO_DATA"

    async def _snapshot(self) -> Any:
        try:
            service = get_live_service()
            snap = await service.get_multi_timeframe_snapshot(self.symbol, include_forming=False)
            self.live_price = snap.current_price
            self.data_status = "HEALTHY"
            return snap
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SMC-FIB-MULTI] snapshot error: %s", exc)
            self.data_status = "HISTORICAL"
            return None

    async def _bootstrap_from_history(self) -> dict[str, list]:
        """Load historical Binance candles as fallback when live WebSocket feed is offline."""
        try:
            service = get_live_service()
            await service._load_historical_base()
            await service._load_5m_base()
            snap = await service.get_multi_timeframe_snapshot(self.symbol, include_forming=False)
            result = {}
            for tf in self.timeframes:
                result[tf] = list(snap.get_series(TF_MAP[tf]))
            if snap.current_price:
                self.live_price = snap.current_price
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SMC-FIB-MULTI] historical bootstrap failed: %s", exc)
            return {}

    async def advance(self, db) -> dict[str, Any]:
        async with self._lock:
            snap = await self._snapshot()
            raw_results: dict[str, Any] = {}

            # Check if any engine has already been seeded
            engines_seeded = any(slot.last_processed_ts is not None for slot in self.slots.values())

            # Bootstrap from real Binance historical candles when engines are unseeded AND
            # the snapshot has too few candles (DEGRADED state) or feed is offline
            snap_15m_count = len(list(snap.get_series(TF_MAP["15m"]))) if snap is not None else 0
            needs_bootstrap = not engines_seeded and snap_15m_count < 50

            hist_candles: dict[str, list] = {}
            if needs_bootstrap:
                logger.info(
                    "[SMC-FIB-MULTI] Engines unseeded (15M candles from snap=%d < 50) — bootstrapping from historical Binance candles.",
                    snap_15m_count,
                )
                hist_candles = await self._bootstrap_from_history()

            # Step 1: Advance each slot with its own newly-closed candles
            for tf in self.timeframes:
                slot = self.slots[tf]
                candles = []
                snap_candles = list(snap.get_series(TF_MAP[tf])) if snap is not None else []

                # Priority 1: Live snapshot candles (if enough or already seeded)
                if snap_candles and (engines_seeded or len(snap_candles) >= 50):
                    candles = snap_candles
                    slot.has_live_data = True
                    slot.live_price = snap.current_price
                elif hist_candles.get(tf):
                    # Priority 2: Historical bootstrap candles + merge any newer live candles
                    candles = hist_candles[tf]
                    if snap_candles:
                        last_hist_ts = candles[-1].timestamp if candles else None
                        new_live = [c for c in snap_candles if last_hist_ts is None or c.timestamp > last_hist_ts]
                        candles = candles + new_live
                    if snap is not None and snap.current_price:
                        slot.live_price = snap.current_price
                    slot.has_live_data = True
                elif snap_candles:
                    # Priority 3: Whatever live candles exist
                    candles = snap_candles
                    slot.has_live_data = bool(candles)
                    slot.live_price = snap.current_price if snap else None
                else:
                    slot.has_live_data = False

                if candles:
                    new_candles = [
                        c for c in candles
                        if slot.last_processed_ts is None or c.timestamp > slot.last_processed_ts
                    ]
                    if new_candles:
                        new_candles.sort(key=lambda c: c.timestamp)
                        for c in new_candles:
                            slot.engine.process_candle(c)
                        slot.last_processed_ts = new_candles[-1].timestamp

                raw_results[tf] = slot.engine.to_dict(self.live_price)

            # Step 2: WINNER-TAKES-ALL MASTER TRIGGER DETECTION
            active_trade_tf = None
            for tf in self.timeframes:
                card = raw_results.get(tf) or {}
                if card.get("is_trade_active"):
                    active_trade_tf = tf
                    break

            # Step 3: OUTPUT SELECTION
            final_results: dict[str, Any] = {}
            for tf in self.timeframes:
                card = raw_results.get(tf) or {}

                if active_trade_tf is not None:
                    # An active trade is running -> Only winner shows active, others standby
                    if tf == active_trade_tf:
                        card["is_locked_by_cascade"] = False
                        card["cascade_status"] = "ACTIVE"
                        final_results[tf] = card
                    else:
                        final_results[tf] = {
                            "strategy": "SMC_WITH_FIB",
                            "symbol": self.symbol,
                            "timeframe": tf,
                            "state": "NO_SETUP",
                            "direction": card.get("direction", "LONG"),
                            "has_live_data": card.get("has_live_data", False),
                            "is_entry_ready": False,
                            "is_entry_touched": False,
                            "is_trade_active": False,
                            "is_locked_by_cascade": True,
                            "cascade_status": f"STANDBY (Locked by {active_trade_tf.upper()})",
                            "entry": {"price": None, "touched": False},
                            "sl": {"price": None},
                            "tp": {"price": None, "dynamic": None, "locked": None, "is_locked": False},
                            "point_1": {"price": None},
                            "point_2": {"price": None},
                            "bos": {"price": None},
                            "levels": {},
                            "metrics": {
                                "total_range_pts": 0.0,
                                "entry_to_tp_pts": 0.0,
                                "entry_to_sl_pts": 0.0,
                                "rr_ratio": 2.83,
                                "current_movement_pts": 0.0,
                            },
                            "smc": card.get("smc", {}),
                        }
                else:
                    # No trade is active yet -> Show each timeframe's current setup & Fibonacci levels so user can see the setups!
                    card["is_locked_by_cascade"] = False
                    card["cascade_status"] = "SCANNING"
                    final_results[tf] = card

            return final_results

