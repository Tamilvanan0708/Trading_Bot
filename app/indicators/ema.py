"""
Exponential Moving Average (EMA) Indicator.
"""

import pandas as pd

from app.data.models import Candle


def calculate_ema(candles: list[Candle], period: int = 50) -> list[float]:
    """Calculates Exponential Moving Average of close prices."""
    if not candles:
        return []

    closes = pd.Series([c.close for c in candles])
    ema = closes.ewm(span=period, adjust=False).mean()
    return [round(float(v), 3) for v in ema.tolist()]


def calculate_multi_emas(candles: list[Candle], periods: list[int] | None = None) -> dict[int, list[float]]:
    """Calculates multiple EMAs for candle series."""
    if periods is None:
        periods = [20, 50, 200]
    return {period: calculate_ema(candles, period) for period in periods}
