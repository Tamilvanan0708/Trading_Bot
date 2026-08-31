"""
Average True Range (ATR) indicator calculation.
"""


import numpy as np
import pandas as pd

from app.data.models import Candle


def calculate_atr(candles: list[Candle], period: int = 14) -> list[float]:
    """
    Calculates Average True Range for a sequence of candles.
    Returns a list of float values of the same length (with np.nan for warmup periods).
    """
    if len(candles) == 1:
        c = candles[0]
        return [round(float(max(c.high - c.low, 0.0)), 3)]
    if len(candles) < 1:
        return []

    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    closes = np.array([c.close for c in candles])

    tr = np.zeros(len(candles))
    tr[0] = highs[0] - lows[0]

    for i in range(1, len(candles)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    # Wilder's Smoothing for ATR
    atr = pd.Series(tr).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().tolist()
    # Fill early NaNs with a simple expanding mean (avoids the sharp
    # discontinuity that raw TR values would produce).
    filled_atr = []
    for i, val in enumerate(atr):
        if not np.isnan(val):
            filled_atr.append(val)
        else:
            fill = sum(tr[:i + 1]) / (i + 1)
            filled_atr.append(fill)
    return [round(float(v), 3) for v in filled_atr]
