"""
MetaTrader 5 (MT5) bridge: real-time ticks, spreads, open positions and
streaming of closed bars.

The ``MetaTrader5`` Python package is a Windows-only, blocking wrapper around
the running MT5 terminal.  This provider therefore:

  * Imports the package lazily so the rest of the codebase works without MT5.
  * Serializes all MT5 calls with a ``threading.Lock`` and offloads them to a
    worker thread via :func:`asyncio.to_thread` to keep the event loop free.
  * Converts MT5 numpy ``rates`` arrays into normalized :class:`Candle`
    models (UTC timestamps, server-timezone offset configurable).
  * Streams *closed* bars only (zero-lookahead): a bar is yielded only once
    its time window has fully elapsed.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from app.core.constants import SignalDirection, TimeFrame
from app.core.exceptions import DataProviderError
from app.core.logging import logger
from app.data.models import BrokerPosition, Candle, MultiTimeframeSnapshot, Tick
from app.data.provider import BrokerAccountProvider, LiveMarketDataProvider

# Timeframe enum -> MT5 constant name
_MT5_TIMEFRAME_ATTR = {
    TimeFrame.M15: "TIMEFRAME_M15",
    TimeFrame.M30: "TIMEFRAME_M30",
    TimeFrame.H1: "TIMEFRAME_H1",
    TimeFrame.H4: "TIMEFRAME_H4",
    TimeFrame.D1: "TIMEFRAME_D1",
}

# MT5 POSITION_TYPE_BUY=0 / POSITION_TYPE_SELL=1
_DIRECTION_MAP: dict[int, SignalDirection] = {
    0: SignalDirection.LONG,
    1: SignalDirection.SHORT,
}

_TF_DURATION_MINUTES = {
    TimeFrame.M15: 15,
    TimeFrame.M30: 30,
    TimeFrame.H1: 60,
    TimeFrame.H4: 240,
    TimeFrame.D1: 1440,
}


def _import_mt5() -> Any:
    """Import the MetaTrader5 module or raise a descriptive error."""
    try:
        import MetaTrader5 as mt5  # type: ignore
        return mt5
    except ImportError as exc:  # pragma: no cover - depends on platform
        raise DataProviderError(
            "MetaTrader5 package is not installed or unsupported on this platform. "
            "Install with: pip install MetaTrader5 (Windows + MT5 terminal required)."
        ) from exc


class MT5MarketDataProvider(LiveMarketDataProvider, BrokerAccountProvider):
    """Institutional MT5 bridge implementing live data + account interfaces."""

    def __init__(
        self,
        symbol: str = "XAUUSD",
        magic: int = 0,
        server: str = "",
        login: int = 0,
        password: str = "",
        timezone_offset_minutes: int = 0,
    ) -> None:
        self._symbol = symbol
        self._magic = magic
        self._timezone_offset_minutes = timezone_offset_minutes
        self._credentials = {"server": server, "login": login, "password": password}

        self._mt5: Any = _import_mt5()
        self._call_lock = threading.Lock()
        self._connected = False
        self._last_yielded_bar_time: dict[str, int] = {}
        self._last_yielded_tick_time: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, timeout_seconds: int = 30) -> bool:
        """Initialize the MT5 terminal connection (blocking; call via to_thread)."""
        with self._call_lock:
            if not self._mt5.initialize(timeout=timeout_seconds):
                raise DataProviderError(
                    f"MT5 initialize failed: {self._mt5.last_error()}"
                )
            if self._credentials["login"] or self._credentials["server"]:
                authorized = self._mt5.login(
                    login=int(self._credentials["login"]),
                    server=self._credentials["server"],
                    password=self._credentials["password"],
                )
                if not authorized:
                    self._mt5.shutdown()
                    raise DataProviderError(
                        f"MT5 login failed: {self._mt5.last_error()}"
                    )
            self._connected = True
            logger.info("MT5 provider connected (symbol=%s).", self._symbol)
            return True

    def disconnect(self) -> None:
        with self._call_lock:
            if self._connected:
                self._mt5.shutdown()
            self._connected = False
            logger.info("MT5 provider disconnected.")

    async def connect_async(self, timeout_seconds: int = 30) -> bool:
        return await asyncio.to_thread(self.connect, timeout_seconds)

    async def disconnect_async(self) -> None:
        await asyncio.to_thread(self.disconnect)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_mt5_sync(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        with self._call_lock:
            if not self._connected:
                raise DataProviderError("MT5 provider is not connected.")
            return fn(*args, **kwargs)

    async def _call_mt5(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._call_mt5_sync, fn, *args, **kwargs)

    def _to_mt5_timeframe(self, timeframe: TimeFrame) -> Any:
        attr = _MT5_TIMEFRAME_ATTR.get(timeframe)
        if attr is None:
            raise DataProviderError(f"Unsupported MT5 timeframe: {timeframe}")
        return getattr(self._mt5, attr)

    def _shift_time(self, ts: datetime) -> datetime:
        """Convert an MT5 server timestamp to UTC applying the configured offset."""
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc) + timedelta(minutes=self._timezone_offset_minutes)

    def _rates_to_candles(self, rates: np.ndarray) -> list[Candle]:
        candles: list[Candle] = []
        for row in rates:
            raw_time = int(row["time"])
            candles.append(
                Candle(
                    timestamp=self._shift_time(datetime.fromtimestamp(raw_time, tz=timezone.utc)),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["tick_volume"]),
                )
            )
        return candles

    # ------------------------------------------------------------------
    # MarketDataProvider implementation
    # ------------------------------------------------------------------

    async def get_latest_price(self, symbol: str) -> float:
        tick = await self.get_tick(symbol)
        if tick is None:
            raise DataProviderError(f"No tick available for {symbol}.")
        return tick.mid

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: TimeFrame,
        limit: int = 200,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        mt5_tf = self._to_mt5_timeframe(timeframe)
        if start_time is not None:
            end = end_time or datetime.now(timezone.utc)
            rates = await self._call_mt5(
                self._mt5.copy_rates_range,
                symbol,
                mt5_tf,
                start_time.replace(tzinfo=None),
                end.replace(tzinfo=None),
            )
        else:
            rates = await self._call_mt5(
                self._mt5.copy_rates_from_pos, symbol, mt5_tf, 0, limit
            )

        if rates is None or len(rates) == 0:
            logger.warning("MT5 returned no bars for %s %s.", symbol, timeframe)
            return []
        return self._rates_to_candles(rates)

    async def get_multi_timeframe_snapshot(
        self,
        symbol: str,
        as_of_time: datetime | None = None,
        m15_limit: int = 400,
    ) -> MultiTimeframeSnapshot:
        m15 = await self.get_ohlcv(symbol, TimeFrame.M15, limit=m15_limit)
        if not m15:
            raise DataProviderError(f"No M15 data available for {symbol}.")
        if as_of_time is not None:
            m15 = [c for c in m15 if c.timestamp <= as_of_time]
            if not m15:
                raise DataProviderError(f"No M15 data before {as_of_time} for {symbol}.")

        m30 = await self.get_ohlcv(symbol, TimeFrame.M30, limit=200)
        h1 = await self.get_ohlcv(symbol, TimeFrame.H1, limit=150)
        h4 = await self.get_ohlcv(symbol, TimeFrame.H4, limit=100)

        latest = m15[-1]
        current_price = (
            await self.get_latest_price(symbol) if as_of_time is None else latest.close
        )
        return MultiTimeframeSnapshot(
            symbol=symbol,
            timestamp=latest.timestamp,
            current_price=current_price,
            m15=m15,
            m30=m30,
            h1=h1,
            h4=h4,
        )

    # ------------------------------------------------------------------
    # LiveMarketDataProvider implementation
    # ------------------------------------------------------------------

    async def get_tick(self, symbol: str) -> Tick | None:
        info = await self._call_mt5(self._mt5.symbol_info_tick, symbol)
        if info is None:
            return None
        bid = float(info.bid)
        ask = float(info.ask)
        if bid <= 0 and ask <= 0:
            # MT5 reports 0.0/0.0 while the market is closed or symbol offline.
            return None
        return Tick(
            symbol=symbol,
            timestamp=self._shift_time(datetime.fromtimestamp(int(info.time), tz=timezone.utc)),
            bid=bid,
            ask=ask,
            last=float(info.last) if float(info.last) > 0 else None,
            volume=float(info.volume),
        )

    async def get_spread(self, symbol: str) -> float:
        tick = await self.get_tick(symbol)
        if tick is not None and tick.spread > 0:
            return tick.spread
        info = await self._call_mt5(self._mt5.symbol_info, symbol)
        if info is None:
            raise DataProviderError(f"MT5 symbol info unavailable for {symbol}.")
        point = 10.0 ** (-int(info.digits))
        return float(info.spread) * point

    async def stream_ticks(self, symbol: str, poll_interval: float = 1.0) -> AsyncIterator[Tick]:
        while True:
            tick = await self.get_tick(symbol)
            if tick is not None:
                tick_ts = tick.timestamp.timestamp()
                if tick_ts > self._last_yielded_tick_time.get(symbol, 0.0):
                    self._last_yielded_tick_time[symbol] = tick_ts
                    yield tick
            await asyncio.sleep(poll_interval)

    async def stream_closed_bars(
        self, symbol: str, timeframe: TimeFrame, poll_interval: float = 5.0
    ) -> AsyncIterator[Candle]:
        """Yield newly-closed bars only (no look-ahead on the forming bar)."""
        duration = _TF_DURATION_MINUTES[timeframe]
        cache_key = f"{symbol}:{timeframe.value}"
        last_seen = self._last_yielded_bar_time.get(cache_key, 0)

        while True:
            bars = await self.get_ohlcv(symbol, timeframe, limit=3)
            now = datetime.now(timezone.utc)
            for bar in bars:
                if bar.timestamp.timestamp() <= last_seen:
                    continue
                bar_close_ts = bar.timestamp + timedelta(minutes=duration)
                if bar_close_ts <= now:
                    last_seen = bar.timestamp.timestamp()
                    self._last_yielded_bar_time[cache_key] = last_seen
                    yield bar
            await asyncio.sleep(poll_interval)

    # ------------------------------------------------------------------
    # BrokerAccountProvider implementation
    # ------------------------------------------------------------------

    async def get_open_positions(self, symbol: str | None = None) -> list[BrokerPosition]:
        if symbol is None:
            positions = await self._call_mt5(self._mt5.positions_get)
        else:
            positions = await self._call_mt5(self._mt5.positions_get, symbol=symbol)
        if positions is None:
            return []

        result: list[BrokerPosition] = []
        for pos in positions:
            result.append(
                BrokerPosition(
                    ticket=str(pos.ticket),
                    symbol=pos.symbol,
                    direction=_DIRECTION_MAP.get(int(pos.type), SignalDirection.NO_TRADE),
                    volume=float(pos.volume),
                    open_price=float(pos.price_open),
                    stop_loss=float(pos.sl) if pos.sl else None,
                    take_profit=float(pos.tp) if pos.tp else None,
                    profit=float(pos.profit),
                    swap=float(pos.swap),
                    open_time=self._shift_time(datetime.fromtimestamp(int(pos.time), tz=timezone.utc)),
                    magic=int(pos.magic),
                    comment=str(pos.comment or ""),
                )
            )
        return result

    async def get_account_balance(self) -> float:
        info = await self._call_mt5(self._mt5.account_info)
        if info is None:
            raise DataProviderError("MT5 account info unavailable.")
        return float(info.balance)