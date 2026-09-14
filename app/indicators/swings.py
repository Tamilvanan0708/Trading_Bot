"""
Swing High and Swing Low Detection (Fractal & ZigZag algorithms).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.data.models import Candle


class SwingPoint(BaseModel):
    """Represents a validated Swing High or Swing Low."""
    index: int
    timestamp: datetime
    price: float
    point_type: Literal["HIGH", "LOW"]
    bar_range: float = 0.0
    confirmed: bool = True
    strength: float = 1.0  # Normalized relative to surrounding ATR


def detect_swings(
    candles: list[Candle],
    left_bars: int = 3,
    right_bars: int = 3,
) -> list[SwingPoint]:
    """
    Detects fractal swing highs and swing lows without future lookahead beyond right_bars.
    A swing high requires high[i] > high[i-left..i-1] and high[i] > high[i+1..i+right].
    A swing low requires low[i] < low[i-left..i-1] and low[i] < low[i+1..i+right].
    """
    if len(candles) < (left_bars + right_bars + 1):
        return []

    swings: list[SwingPoint] = []
    n = len(candles)

    for i in range(left_bars, n - right_bars):
        curr = candles[i]

        # 1. High check (support single peak or plateau up to 3 bars ending at i)
        is_swing_high = True
        for r in range(1, right_bars + 1):
            if candles[i + r].high >= curr.high:
                is_swing_high = False
                break

        if is_swing_high:
            k = 0
            while k < 3 and (i - k) >= 0 and candles[i - k].high == curr.high:
                k += 1
            plateau_start = i - k + 1
            if plateau_start < left_bars:
                is_swing_high = False
            else:
                for l in range(1, left_bars + 1):
                    if candles[plateau_start - l].high >= curr.high:
                        is_swing_high = False
                        break

        # 2. Low check (support single trough or plateau up to 3 bars ending at i)
        is_swing_low = True
        for r in range(1, right_bars + 1):
            if candles[i + r].low <= curr.low:
                is_swing_low = False
                break

        if is_swing_low:
            k = 0
            while k < 3 and (i - k) >= 0 and candles[i - k].low == curr.low:
                k += 1
            plateau_start = i - k + 1
            if plateau_start < left_bars:
                is_swing_low = False
            else:
                for l in range(1, left_bars + 1):
                    if candles[plateau_start - l].low <= curr.low:
                        is_swing_low = False
                        break

        if is_swing_high:
            swings.append(
                SwingPoint(
                    index=i,
                    timestamp=curr.timestamp,
                    price=curr.high,
                    point_type="HIGH",
                    bar_range=curr.total_range,
                    confirmed=True,
                )
            )

        if is_swing_low:
            swings.append(
                SwingPoint(
                    index=i,
                    timestamp=curr.timestamp,
                    price=curr.low,
                    point_type="LOW",
                    bar_range=curr.total_range,
                    confirmed=True,
                )
            )

    # Sort chronologically by index
    swings.sort(key=lambda s: s.index)
    return swings
