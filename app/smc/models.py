"""
Smart Money Concepts (SMC) Pydantic Models.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.constants import LiquidityType, StructureType, TimeFrame, ZoneType


class FairValueGap(BaseModel):
    """Represents a 3-candle imbalance (FVG)."""
    index: int
    timestamp: datetime
    gap_type: Literal["BULLISH", "BEARISH"]
    top: float
    bottom: float
    midpoint: float
    mitigated: bool = False
    mitigated_at: datetime | None = None
    timeframe: TimeFrame


class OrderBlock(BaseModel):
    """Institutional Order Block or Breaker Block."""
    index: int
    timestamp: datetime
    ob_type: Literal["BULLISH", "BEARISH"]
    top: float
    bottom: float
    is_breaker: bool = False
    mitigated: bool = False
    mitigated_at: datetime | None = None
    volume: float = 0.0
    timeframe: TimeFrame


class MarketBreak(BaseModel):
    """Break of Structure (BOS) or Change of Character (CHoCH)."""
    index: int
    timestamp: datetime
    break_type: StructureType  # BOS_BULLISH, BOS_BEARISH, CHOCH_BULLISH, CHOCH_BEARISH
    broken_level: float
    break_price: float
    timeframe: TimeFrame
    description: str


class LiquidityPool(BaseModel):
    """Liquidity Highs, Liquidity Lows, Equal Highs/Lows, Sweeps."""
    index: int
    timestamp: datetime
    pool_type: LiquidityType
    price_level: float
    tolerance: float
    swept: bool = False
    swept_at: datetime | None = None
    timeframe: TimeFrame


class SMCAnalysis(BaseModel):
    """Aggregated SMC State for a specific timeframe."""
    timeframe: TimeFrame
    current_zone: ZoneType  # PREMIUM, DISCOUNT, EQUILIBRIUM
    equilibrium_price: float
    range_high: float
    range_low: float
    latest_break: MarketBreak | None = None
    active_fvgs: list[FairValueGap] = Field(default_factory=list)
    active_order_blocks: list[OrderBlock] = Field(default_factory=list)
    liquidity_pools: list[LiquidityPool] = Field(default_factory=list)
    recent_sweeps: list[LiquidityPool] = Field(default_factory=list)
    summary: str = ""
