"""
Multi-Timeframe Monitor for FIB GO WITH TREND (9 EMA & 21 EMA + Fibonacci Retracement).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TimeFrame
from app.data.live.service import get_live_service
from app.data.models import Candle
from app.data.timeframe_resampler import resample_candles
from app.retracement.fib_trend_engine import FibTrendEngine, FibTrendState

logger = logging.getLogger("xauusd_agent")

TF_MAP: dict[str, TimeFrame] = {
    "5m": TimeFrame.M5,
    "15m": TimeFrame.M15,
    "30m": TimeFrame.M30,
    "1h": TimeFrame.H1,
    "4h": TimeFrame.H4,
}

DEFAULT_TIMEFRAMES = ["5m"]

_fib_trend_instances: dict[str, FibTrendMultiTFMonitor] = {}


def get_fib_trend_multi_tf_service(symbol: str = "XAUUSD") -> FibTrendMultiTFMonitor:
    global _fib_trend_instances
    svc = _fib_trend_instances.get(symbol)
    if svc is None:
        svc = FibTrendMultiTFMonitor(symbol=symbol)
        _fib_trend_instances[symbol] = svc
    return svc


class _FibTrend_TFSlot:
    def __init__(self, symbol: str, timeframe: str) -> None:
        self.timeframe = timeframe
        self.engine = FibTrendEngine(symbol=symbol, timeframe=timeframe)
        self.last_processed_ts: datetime | None = None
        self.has_live_data: bool = False
        self.live_price: float | None = None

    def reset(self) -> None:
        self.engine.reset()
        self.last_processed_ts = None
        self.has_live_data = False
        self.live_price = None


class FibTrendMultiTFMonitor:
    """Multi-timeframe monitor for FIB GO WITH TREND."""

    def __init__(self, symbol: str = "XAUUSD", timeframes: list[str] | None = None) -> None:
        self.symbol = symbol
        self.timeframes = list(timeframes or DEFAULT_TIMEFRAMES)
        self.slots: dict[str, _FibTrend_TFSlot] = {
            tf: _FibTrend_TFSlot(symbol, tf) for tf in self.timeframes
        }
        self.live_price: float | None = None
        self.data_status: str = "NO_DATA"
        self._lock = asyncio.Lock()

    def reset(self) -> None:
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
            logger.debug("[FIB-TREND-MULTI] snapshot error: %s", exc)
            self.data_status = "HISTORICAL"
            return None

    async def _bootstrap_from_history(self) -> dict[str, list]:
        import time
        now = time.time()
        if getattr(self, "_cached_hist_candles", None) and (now - getattr(self, "_last_bootstrap_ts", 0) < 120.0):
            return self._cached_hist_candles

        try:
            from app.config.settings import get_settings
            from app.data.live.binance_history import BinanceHistoryProvider
            provider = BinanceHistoryProvider(get_settings())
            results: dict[str, list] = {}
            for tf_str, tf_enum in TF_MAP.items():
                if tf_str not in self.timeframes:
                    continue
                candles = await provider.get_ohlcv(self.symbol, tf_enum, limit=120)
                if candles:
                    results[tf_str] = candles
            if results:
                self.data_status = "HISTORICAL"
                self._cached_hist_candles = results
                self._last_bootstrap_ts = now
                logger.info(
                    "[FIB-TREND-MULTI] Bootstrapped %s with %d TFs",
                    self.symbol, len(results)
                )
            return results
        except Exception as exc:  # noqa: BLE001
            logger.warning("[FIB-TREND-MULTI] REST history bootstrap failed: %s", exc)
            return {}

    async def advance(self, db: AsyncSession | None = None) -> dict[str, FibTrendEngine]:
        async with self._lock:
            snap = await self._snapshot()

            engines_seeded = any(len(s.engine._candles) >= 50 for s in self.slots.values())
            needs_bootstrap = not engines_seeded

            hist_candles: dict[str, list] = {}
            if needs_bootstrap:
                hist_candles = await self._bootstrap_from_history()

            results: dict[str, FibTrendEngine] = {}

            for tf_str in self.timeframes:
                slot = self.slots.get(tf_str)
                if slot is None:
                    continue

                candles_to_feed: list[Candle] = []

                if snap is not None:
                    tf_enum = TF_MAP.get(tf_str)
                    if tf_enum is not None:
                        series = snap.get_series(tf_enum)
                        if series:
                            candles_to_feed = list(series)

                if len(candles_to_feed) < 50 and tf_str in hist_candles:
                    candles_to_feed = hist_candles[tf_str]

                if not candles_to_feed:
                    continue

                if len(slot.engine._candles) < 20:
                    for c in candles_to_feed[:-1]:
                        slot.engine.process_candle(c)
                    slot.last_processed_ts = candles_to_feed[-2].timestamp if len(candles_to_feed) >= 2 else None

                latest_c = candles_to_feed[-1]
                if slot.last_processed_ts is None or latest_c.timestamp > slot.last_processed_ts:
                    slot.engine.process_candle(latest_c)
                    slot.last_processed_ts = latest_c.timestamp

                slot.live_price = self.live_price or latest_c.close
                results[tf_str] = slot.engine

            return results
