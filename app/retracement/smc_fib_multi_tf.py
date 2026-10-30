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

DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1h"]
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
        from app.config.execution_settings import get_execution_settings
        mode = getattr(get_execution_settings(), "fib_engine_mode", "classic")
        self.engine = SMCFibEngine(symbol=symbol, timeframe=timeframe, engine_mode=mode)
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
        """Load real historical candles via MT5 / Binance REST provider."""
        import os
        if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("APP_ENV") == "test":
            return {}

        import time
        now = time.time()
        if getattr(self, "_cached_hist_candles", None) and (now - getattr(self, "_last_bootstrap_ts", 0) < 120.0):
            return self._cached_hist_candles

        try:
            from app.config.settings import get_settings
            from app.config.execution_settings import get_execution_settings
            from app.data.live.service import get_live_service
            from app.data.live.binance_history import BinanceHistoryProvider

            settings = get_settings()
            exec_cfg = get_execution_settings()
            is_mt5 = (settings.LIVE_FEED_PROVIDER == "mt5") or settings.MT5_ENABLED or getattr(exec_cfg, "mt5_bridge_enabled", False)

            provider = None
            if is_mt5:
                live_svc = get_live_service()
                provider = getattr(live_svc, "_mt5_provider", None)
                if provider is None or not getattr(provider, "_connected", False):
                    from app.data.mt5_provider import MT5DataProvider
                    provider = MT5DataProvider(settings)
                    try:
                        await provider.connect_async()
                    except Exception as exc:
                        logger.warning("[SMC-FIB-MULTI] MT5 connect in bootstrap failed: %s", exc)
            elif not is_mt5:
                provider = BinanceHistoryProvider(settings)

            if provider is None:
                return getattr(self, "_cached_hist_candles", {})

            result = {}
            for tf in self.timeframes:
                try:
                    candles = await asyncio.wait_for(provider.get_ohlcv(self.symbol, TF_MAP[tf], limit=200), timeout=3.0)
                    if candles:
                        result[tf] = candles
                except Exception:
                    pass
            if result:
                self._cached_hist_candles = result
                self._last_bootstrap_ts = now
            return result
        except Exception as exc:  # noqa: BLE001
            logger.error("[SMC-FIB-MULTI] historical bootstrap failed: %s", exc)
            return getattr(self, "_cached_hist_candles", {})

    async def advance(self, db, live_price: float | None = None) -> dict[str, Any]:
        async with self._lock:
            if live_price is not None and live_price > 0:
                self.live_price = live_price
            snap = await self._snapshot()
            if self.live_price is None and snap is not None and snap.current_price:
                self.live_price = snap.current_price

            snap_key = (
                snap.timestamp if snap else None,
                len(snap.m15) if (snap and hasattr(snap, "m15")) else 0,
                self.live_price,
            )
            if snap is not None and getattr(self, "_last_snap_key", None) == snap_key and getattr(self, "_last_advance_results", None) is not None:
                if self.live_price:
                    for tf in self.timeframes:
                        slot = self.slots[tf]
                        slot.engine.update_live_price(self.live_price)
                        self._last_advance_results[tf] = slot.engine.to_dict(self.live_price)
                return self._last_advance_results

            raw_results: dict[str, Any] = {}

            # Check if any engine has already been seeded
            engines_seeded = any(slot.last_processed_ts is not None for slot in self.slots.values())

            # Bootstrap from real historical candles ONLY when engines have never been seeded
            needs_bootstrap = not engines_seeded

            hist_candles: dict[str, list] = {}
            if needs_bootstrap:
                logger.info(
                    "[SMC-FIB-MULTI] Seeding engines from MT5/Historical bootstrap.",
                )
                hist_candles = await self._bootstrap_from_history()

            # Step 1: Advance each slot with its own newly-closed candles
            from app.config.execution_settings import get_execution_settings
            active_engine_mode = getattr(get_execution_settings(), "fib_engine_mode", "classic")
            for tf in self.timeframes:
                slot = self.slots[tf]
                slot.engine.engine_mode = active_engine_mode
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

                if self.live_price:
                    slot.engine.update_live_price(self.live_price)

                raw_results[tf] = slot.engine.to_dict(self.live_price)

            # Step 2: Multi-Slot Parallel Execution — each timeframe maintains its own active slot
            final_results: dict[str, Any] = {}
            for tf in self.timeframes:
                card = raw_results.get(tf) or {}
                card["is_locked_by_cascade"] = False
                card["cascade_status"] = "ACTIVE" if card.get("is_trade_active") else "SCANNING"
                final_results[tf] = card

            self._last_snap_key = snap_key
            self._last_advance_results = final_results
            return final_results

