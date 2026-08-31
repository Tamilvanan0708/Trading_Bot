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


def calculate_multi_emas(candles: list[Candle], periods: list[int] = [20, 50, 200]) -> dict[int, list[float]]:
    """Calculates multiple EMAs for candle series."""
    return {period: calculate_ema(candles, period) for period in periods}
