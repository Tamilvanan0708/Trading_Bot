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

DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h"]
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

    async def _snapshot(self) -> Any:
        try:
            service = get_live_service()
            snap = await service.get_multi_timeframe_snapshot(self.symbol, include_forming=False)
            self.live_price = snap.current_price
            self.data_status = "HEALTHY"
            return snap
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SMC-FIB-MULTI] snapshot error: %s", exc)
            self.data_status = "NO_DATA"
            return None

    async def advance(self, db) -> dict[str, Any]:
        async with self._lock:
            snap = await self._snapshot()
            raw_results: dict[str, Any] = {}

            # Step 1: Advance each slot with its own newly-closed candles
            for tf in self.timeframes:
                slot = self.slots[tf]
                candles = []
                if snap is not None:
                    candles = list(snap.get_series(TF_MAP[tf]))
                    slot.has_live_data = bool(candles)
                    slot.live_price = snap.current_price
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
            winner_tf = None
            # 2a. Priority: Find the timeframe with an ACTIVE TRADE (Entry Touched)
            for tf in self.timeframes:
                card = raw_results.get(tf) or {}
                if card.get("is_trade_active"):
                    winner_tf = tf
                    break

            # 2b. If no trade active yet, find the first timeframe WAITING FOR ENTRY
            if winner_tf is None:
                for tf in self.timeframes:
                    card = raw_results.get(tf) or {}
                    if card.get("is_entry_ready"):
                        winner_tf = tf
                        break

            # Step 3: PURGE NON-WINNERS & ENFORCE SINGLE-TRADE OUTPUT
            final_results: dict[str, Any] = {}
            for tf in self.timeframes:
                card = raw_results.get(tf) or {}
                is_winner = (winner_tf == tf)

                if is_winner:
                    card["is_locked_by_cascade"] = False
                    card["cascade_status"] = "ACTIVE"
                    final_results[tf] = card
                else:
                    # Reset the slot engine so other timeframes hold NO pending or old trade
                    if winner_tf is not None and card.get("is_trade_active"):
                        self.slots[tf].engine.reset()

                    lock_msg = f"STANDBY (Locked by {winner_tf.upper()})" if winner_tf else "STANDBY (Scanning in progress)"
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
                        "cascade_status": lock_msg,
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

            return final_results

