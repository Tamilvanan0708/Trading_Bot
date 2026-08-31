"""
Fair Value Gap (FVG) Detection & Mitigation Tracking.
"""


from app.core.constants import TimeFrame
from app.data.models import Candle
from app.smc.models import FairValueGap


def detect_fvgs(
    candles: list[Candle],
    timeframe: TimeFrame,
    min_gap_size: float = 0.5,
) -> list[FairValueGap]:
    """
    Detects 3-candle Fair Value Gaps without future lookahead.
    Bullish FVG: Candle[i-2].high < Candle[i].low (Gap is between candle[i-2].high and candle[i].low)
    Bearish FVG: Candle[i-2].low > Candle[i].high (Gap is between candle[i].high and candle[i-2].low)
    Then checks subsequent candles (i+1 .. N) for mitigation.
    """
    if len(candles) < 3:
        return []

    fvgs: list[FairValueGap] = []
    n = len(candles)

    for i in range(2, n):
        c1 = candles[i - 2]
        c2 = candles[i - 1]  # The impulse candle
        c3 = candles[i]

        # Bullish FVG
        if c3.low > c1.high:
            gap_size = c3.low - c1.high
            if gap_size >= min_gap_size:
                top = c3.low
                bottom = c1.high
                midpoint = round((top + bottom) / 2.0, 2)

                # Check if mitigated in candles after i
                mitigated = False
                mitigated_at = None
                for k in range(i + 1, n):
                    if candles[k].low <= bottom:
                        mitigated = True
                        mitigated_at = candles[k].timestamp
                        break

                fvgs.append(
                    FairValueGap(
                        index=i - 1,
                        timestamp=c2.timestamp,
                        gap_type="BULLISH",
                        top=round(top, 2),
                        bottom=round(bottom, 2),
                        midpoint=midpoint,
                        mitigated=mitigated,
                        mitigated_at=mitigated_at,
                        timeframe=timeframe,
                    )
                )

        # Bearish FVG
        elif c1.low > c3.high:
            gap_size = c1.low - c3.high
            if gap_size >= min_gap_size:
                top = c1.low
                bottom = c3.high
                midpoint = round((top + bottom) / 2.0, 2)

                mitigated = False
                mitigated_at = None
                for k in range(i + 1, n):
                    if candles[k].high >= top:
                        mitigated = True
                        mitigated_at = candles[k].timestamp
                        break

                fvgs.append(
                    FairValueGap(
                        index=i - 1,
                        timestamp=c2.timestamp,
                        gap_type="BEARISH",
                        top=round(top, 2),
                        bottom=round(bottom, 2),
                        midpoint=midpoint,
                        mitigated=mitigated,
                        mitigated_at=mitigated_at,
                        timeframe=timeframe,
                    )
                )

    return fvgs
