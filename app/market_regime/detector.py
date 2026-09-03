"""
Market regime classifier using existing indicators only.

Classification is deterministic and explainable:
  - TRENDING   : 50 EMA aligned with price slope and structure trend
  - RANGING    : EMA flattish + structure alternates HH/LH or HL/LL
  - HIGH_VOL   : ATR percentile elevated relative to history
  - LOW_VOL    : ATR percentile depressed relative to history
  - UNCERTAIN  : insufficient data to classify
"""

from enum import Enum

from app.core.constants import MarketBias
from app.data.models import Candle
from app.indicators.atr import calculate_atr
from app.indicators.ema import calculate_ema


class MarketRegime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNCERTAIN = "UNCERTAIN"


class RegimeAnalysis:
    def __init__(self, regime: MarketRegime, trend: MarketBias, details: str, volatility_pct: float = 0.0):
        self.regime = regime
        self.trend = trend
        self.details = details
        self.volatility_pct = volatility_pct

    def model_dump(self) -> dict:
        return {
            "regime": self.regime.value,
            "trend": self.trend.value,
            "details": self.details,
            "volatility_pct": round(self.volatility_pct, 2),
        }


class MarketRegimeDetector:
    """Deterministic market-regime classifier."""

    def __init__(self, atr_period: int = 14, ema_period: int = 50, high_vol_threshold: float = 75.0, low_vol_threshold: float = 25.0):
        self.atr_period = atr_period
        self.ema_period = ema_period
        self.high_vol_threshold = high_vol_threshold
        self.low_vol_threshold = low_vol_threshold

    def analyze(self, candles: list[Candle], trend: MarketBias | None = None) -> RegimeAnalysis:
        """Classify the current market regime from a candle series."""
        if len(candles) < self.ema_period + 2:
            return RegimeAnalysis(MarketRegime.UNCERTAIN, MarketBias.NEUTRAL, "Insufficient data.")

        closes = [c.close for c in candles]
        ema = calculate_ema(candles, self.ema_period)
        atr = calculate_atr(candles, self.atr_period)

        # Volatility percentile relative to the window (count of values <= last,
        # not first-occurrence index, so duplicates are not under-ranked).
        atr_recent = [a for a in atr if a > 0]
        vol = 0.0
        if atr_recent:
            last_atr = atr_recent[-1]
            sorted_atr = sorted(atr_recent)
            count_le = sum(1 for v in sorted_atr[:-1] if v < last_atr)
            vol = (count_le / max(1, len(sorted_atr))) * 100.0

        # EMA slope over the last 20 bars
        ema_recent = [e for e in ema if e > 0]
        slope = 0.0
        if len(ema_recent) >= 20:
            slope = (ema_recent[-1] - ema_recent[-20]) / ema_recent[-20] * 100.0

        # Range detection: recent swing structure via simple rolling high/low
        lookback = min(len(candles), 50)
        recent_high = max(c.high for c in candles[-lookback:])
        recent_low = min(c.low for c in candles[-lookback:])
        range_pct = (recent_high - recent_low) / recent_low * 100.0

        # Classify volatility tier first
        if vol >= self.high_vol_threshold:
            regime = MarketRegime.HIGH_VOLATILITY
            details = f"ATR percentile {vol:.0f}% (elevated)."
        elif vol <= self.low_vol_threshold:
            regime = MarketRegime.LOW_VOLATILITY
            details = f"ATR percentile {vol:.0f}% (depressed)."
        else:
            # Trend vs range classification
            if abs(slope) >= 0.05 and range_pct >= 1.0:
                regime = MarketRegime.TRENDING
                details = f"EMA slope {slope:+.3f}%, range {range_pct:.1f}%."
            elif abs(slope) < 0.05:
                regime = MarketRegime.RANGING
                details = f"EMA slope {slope:+.3f}%, range {range_pct:.1f}% (flattish)."
            else:
                regime = MarketRegime.UNCERTAIN
                details = f"EMA slope {slope:+.3f}%, range {range_pct:.1f}%."

        bias = trend or MarketBias.NEUTRAL
        return RegimeAnalysis(regime, bias, details, vol)