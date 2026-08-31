"""
Market Data Pydantic Models & Data Structures.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.constants import SignalDirection, TimeFrame


class Candle(BaseModel):
    """Normalized OHLCV Bar representation."""
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0, default=0.0)

    @field_validator("high")
    @classmethod
    def validate_high(cls, v: float, info) -> float:
        values = info.data
        if "open" in values and v < values["open"]:
            raise ValueError(f"High ({v}) cannot be lower than Open ({values['open']})")
        return v

    @field_validator("low")
    @classmethod
    def validate_low(cls, v: float, info) -> float:
        values = info.data
        if "open" in values and v > values["open"]:
            raise ValueError(f"Low ({v}) cannot be higher than Open ({values['open']})")
        return v

    @model_validator(mode="after")
    def validate_high_low_relation(self) -> "Candle":
        if self.high < self.low:
            raise ValueError(f"High ({self.high}) cannot be lower than Low ({self.low})")
        return self

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def total_range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low


class TimeframeSeries(BaseModel):
    """Collection of candles for a specific timeframe."""
    symbol: str
    timeframe: TimeFrame
    candles: list[Candle]

    def __len__(self) -> int:
        return len(self.candles)

    def latest(self) -> Candle | None:
        return self.candles[-1] if self.candles else None


class Tick(BaseModel):
    """Normalized real-time quote or trade tick.

    At least one of `bid`, `ask` or `last` must be present. Feeds that only
    publish a trade price (e.g. Binance aggTrade) use `last`; full book feeds
    publish `bid`/`ask` so the spread can be derived.
    """
    symbol: str
    timestamp: datetime
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    last: float | None = Field(default=None, gt=0)
    volume: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_price_presence(self) -> "Tick":
        if self.bid is None and self.ask is None and self.last is None:
            raise ValueError("Tick must contain at least one of bid, ask or last price.")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError(f"Ask ({self.ask}) cannot be lower than Bid ({self.bid}).")
        return self

    @property
    def mid(self) -> float:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        if self.last is not None:
            return self.last
        return float(self.bid or self.ask)

    @property
    def spread(self) -> float:
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return 0.0


class BrokerPosition(BaseModel):
    """Normalized open position as reported by a broker account."""
    ticket: str
    symbol: str
    direction: SignalDirection
    volume: float = Field(gt=0)
    open_price: float = Field(gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    profit: float = 0.0
    swap: float = 0.0
    open_time: datetime
    magic: int = 0
    comment: str = ""


class MultiTimeframeSnapshot(BaseModel):
    """Unified snapshot holding synchronous candle series across 5m/15m/30m/1h/4h."""
    symbol: str
    timestamp: datetime
    current_price: float
    m5: list[Candle] = Field(default_factory=list)
    m15: list[Candle] = Field(default_factory=list)
    m30: list[Candle] = Field(default_factory=list)
    h1: list[Candle] = Field(default_factory=list)
    h4: list[Candle] = Field(default_factory=list)

    def get_series(self, tf: TimeFrame) -> list[Candle]:
        """Returns the candle series for a timeframe (empty if not loaded)."""
        if tf == TimeFrame.M5:
            return self.m5
        if tf == TimeFrame.M15:
            return self.m15
        if tf == TimeFrame.M30:
            return self.m30
        if tf == TimeFrame.H1:
            return self.h1
        if tf == TimeFrame.H4:
            return self.h4
        return []


class FeedHealth(BaseModel):
    """Health snapshot of a live market feed."""
    provider: str
    symbol: str
    connected: bool
    running: bool
    ticks_cached: int
    buffer_capacity: int
    latest_tick: Tick | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataQualityStatus(BaseModel):
    """Complete data-quality state for the live analysis pipeline."""
    provider: str = "none"
    connected: bool = False
    historical_available: bool = False
    historical_fresh: bool = False
    candle_count: int = 0
    oldest_candle: datetime | None = None
    newest_candle: datetime | None = None
    gap_count: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    last_error: str | None = None
    degraded: bool = True
    degradation_reason: str = "No live data available."
    live_price: float | None = None
    last_tick_at: datetime | None = None
    last_history_refresh_at: datetime | None = None
    last_history_refresh_status: str = "NONE"  # NONE | RUNNING | SUCCESS | FAILED
    last_history_refresh_error: str | None = None
    next_history_refresh_at: datetime | None = None
