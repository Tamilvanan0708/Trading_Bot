"""
Abstract Market Data Provider Interface.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from app.core.constants import TimeFrame
from app.data.models import BrokerPosition, Candle, MultiTimeframeSnapshot, Tick


class MarketDataProvider(ABC):
    """Abstract interface for historical market data feeds."""

    @abstractmethod
    async def get_latest_price(self, symbol: str) -> float:
        """Fetch the most recent traded price for the symbol."""

    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: TimeFrame,
        limit: int = 200,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        """Fetch OHLCV historical candle bars."""

    @abstractmethod
    async def get_multi_timeframe_snapshot(
        self,
        symbol: str,
        as_of_time: datetime | None = None,
        m15_limit: int = 300,
    ) -> MultiTimeframeSnapshot:
        """Fetch synchronous multi-timeframe candle datasets for 15m, 30m, 1h, 4h."""


class LiveMarketDataProvider(MarketDataProvider):
    """Extends the base provider with real-time tick, spread and bar streaming."""

    @abstractmethod
    async def get_tick(self, symbol: str) -> Tick | None:
        """Fetch the latest quote tick (bid/ask/last)."""

    @abstractmethod
    async def get_spread(self, symbol: str) -> float:
        """Fetch the current spread in price units."""

    @abstractmethod
    async def stream_closed_bars(
        self, symbol: str, timeframe: TimeFrame, poll_interval: float = 5.0
    ) -> AsyncIterator[Candle]:
        """Async generator yielding newly-closed bars (zero-lookahead)."""

    @abstractmethod
    async def stream_ticks(
        self, symbol: str, poll_interval: float = 1.0
    ) -> AsyncIterator[Tick]:
        """Async generator yielding live ticks as they arrive."""


class BrokerAccountProvider(ABC):
    """Abstract interface for broker account information & position management."""

    @abstractmethod
    async def get_open_positions(
        self, symbol: str | None = None
    ) -> list[BrokerPosition]:
        """Fetch open positions, optionally filtered by symbol."""

    @abstractmethod
    async def get_account_balance(self) -> float:
        """Fetch the current account balance."""