"""
Market Structure Models and Data Structures.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.constants import MarketBias, StructureType, TimeFrame
from app.indicators.swings import SwingPoint


class StructureNode(BaseModel):
    """Represents a classified structure point (HH, HL, LH, LL)."""
    index: int
    timestamp: datetime
    price: float
    structure_type: StructureType
    swing_type: str  # "HIGH" or "LOW"
    confidence: float = 1.0


class MarketStructureAnalysis(BaseModel):
    """Overall market structure state for a specific timeframe."""
    timeframe: TimeFrame
    trend: MarketBias
    last_swing_high: SwingPoint | None = None
    last_swing_low: SwingPoint | None = None
    structure_points: list[StructureNode] = Field(default_factory=list)
    ema_trend: MarketBias | None = None
    atr: float = 0.0
    summary: str = ""
