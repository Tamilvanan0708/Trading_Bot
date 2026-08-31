"""
Break of Structure (BOS) & Change of Character (CHoCH) Detector.
"""


from app.core.constants import MarketBias, StructureType, TimeFrame
from app.data.models import Candle
from app.indicators.swings import detect_swings
from app.smc.models import MarketBreak


def detect_bos_choch(
    candles: list[Candle],
    timeframe: TimeFrame,
    left_bars: int = 3,
    right_bars: int = 3,
) -> list[MarketBreak]:
    """
    Identifies institutional BOS and CHoCH events by tracking swing violations.
    - If price closes above previous swing high in an established uptrend -> BOS Bullish
    - If price closes below previous swing low in an established downtrend -> BOS Bearish
    - If price closes below previous swing low while in an uptrend -> CHoCH Bearish (Reversal)
    - If price closes above previous swing high while in a downtrend -> CHoCH Bullish (Reversal)
    """
    if len(candles) < 30:
        return []

    swings = detect_swings(candles, left_bars=left_bars, right_bars=right_bars)
    if len(swings) < 3:
        return []

    breaks: list[MarketBreak] = []
    current_trend = MarketBias.NEUTRAL

    # Inspect candle closes chronologically for breaks
    for i, candle in enumerate(candles):
        # Only evaluate after at least some swings are found
        active_highs = [s for s in swings if s.index < i and s.point_type == "HIGH"]
        active_lows = [s for s in swings if s.index < i and s.point_type == "LOW"]

        if not active_highs or not active_lows:
            continue

        ref_high = active_highs[-1]
        ref_low = active_lows[-1]

        # Check Bullish Break (Close > previous swing high)
        if candle.close > ref_high.price and (i == 0 or candles[i - 1].close <= ref_high.price):
            if current_trend == MarketBias.BEARISH:
                # Bearish to Bullish transition
                btype = StructureType.CHOCH_BULLISH
                desc = f"Bullish CHoCH: Closed above swing high at {ref_high.price}"
                current_trend = MarketBias.BULLISH
            else:
                btype = StructureType.BOS_BULLISH
                desc = f"Bullish BOS: Continued above swing high at {ref_high.price}"
                current_trend = MarketBias.BULLISH

            breaks.append(
                MarketBreak(
                    index=i,
                    timestamp=candle.timestamp,
                    break_type=btype,
                    broken_level=ref_high.price,
                    break_price=candle.close,
                    timeframe=timeframe,
                    description=desc,
                )
            )

        # Check Bearish Break (Close < previous swing low)
        elif candle.close < ref_low.price and (i == 0 or candles[i - 1].close >= ref_low.price):
            if current_trend == MarketBias.BULLISH:
                # Bullish to Bearish transition
                btype = StructureType.CHOCH_BEARISH
                desc = f"Bearish CHoCH: Closed below swing low at {ref_low.price}"
                current_trend = MarketBias.BEARISH
            else:
                btype = StructureType.BOS_BEARISH
                desc = f"Bearish BOS: Continued below swing low at {ref_low.price}"
                current_trend = MarketBias.BEARISH

            breaks.append(
                MarketBreak(
                    index=i,
                    timestamp=candle.timestamp,
                    break_type=btype,
                    broken_level=ref_low.price,
                    break_price=candle.close,
                    timeframe=timeframe,
                    description=desc,
                )
            )

    return breaks
