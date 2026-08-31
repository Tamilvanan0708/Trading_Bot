"""
Fibonacci Models and Level Definitions.
"""

from pydantic import BaseModel

from app.core.constants import SignalDirection
from app.indicators.swings import SwingPoint


class FibonacciLevel(BaseModel):
    """Single Fibonacci ratio level."""
    ratio: float
    price: float
    name: str
    is_golden_zone: bool = False


class FibonacciSetup(BaseModel):
    """Complete Fibonacci Retracement Setup."""
    direction: SignalDirection  # LONG (bullish retracement) or SHORT (bearish retracement)
    swing_high: SwingPoint
    swing_low: SwingPoint
    price_range: float
    current_price: float
    levels: dict[float, float]  # ratio -> price
    active_level_ratio: float | None = None
    in_golden_pocket: bool = False
    entry_zone_min: float
    entry_zone_max: float
    suggested_entry: float
    suggested_sl: float
    tp1: float
    tp2: float
    tp3: float
    risk_reward: float
    valid: bool = False
    invalidation_price: float
    reason: str = ""
