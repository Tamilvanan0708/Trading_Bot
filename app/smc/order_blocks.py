"""
Institutional Order Block & Breaker Block Detection.
"""


from app.core.constants import StructureType, TimeFrame
from app.data.models import Candle
from app.smc.models import MarketBreak, OrderBlock


def detect_order_blocks(
    candles: list[Candle],
    breaks: list[MarketBreak],
    timeframe: TimeFrame,
) -> list[OrderBlock]:
    """
    Identifies order blocks originating from validated BOS / CHoCH breaks:
    - Bullish OB: The last down-close candle prior to a bullish break impulse.
    - Bearish OB: The last up-close candle prior to a bearish break impulse.
    - Breaker Block: When price breaches through an existing opposite OB.
    """
    if len(candles) < 5 or not breaks:
        return []

    order_blocks: list[OrderBlock] = []
    n = len(candles)

    for brk in breaks:
        break_idx = brk.index

        if brk.break_type in [StructureType.BOS_BULLISH, StructureType.CHOCH_BULLISH]:
            # Look backwards from break_idx for the lowest down candle in the prior 10 bars
            search_start = max(0, break_idx - 10)
            candidate_idx = None
            lowest_val = float("inf")

            for j in range(break_idx - 1, search_start - 1, -1):
                c = candles[j]
                if c.is_bearish and c.low < lowest_val:
                    lowest_val = c.low
                    candidate_idx = j

            if candidate_idx is not None:
                ob_candle = candles[candidate_idx]
                top = ob_candle.high
                bottom = ob_candle.low

                # Check if mitigated after break_idx
                mitigated = False
                mitigated_at = None
                is_breaker = False

                for k in range(break_idx, n):
                    if candles[k].low < bottom:
                        is_breaker = True  # Violated through the bottom
                    if candles[k].low <= top and not mitigated:
                        mitigated = True
                        mitigated_at = candles[k].timestamp

                order_blocks.append(
                    OrderBlock(
                        index=candidate_idx,
                        timestamp=ob_candle.timestamp,
                        ob_type="BULLISH",
                        top=round(top, 2),
                        bottom=round(bottom, 2),
                        is_breaker=is_breaker,
                        mitigated=mitigated,
                        mitigated_at=mitigated_at,
                        volume=ob_candle.volume,
                        timeframe=timeframe,
                    )
                )

        elif brk.break_type in [StructureType.BOS_BEARISH, StructureType.CHOCH_BEARISH]:
            # Look backwards for the highest up candle in prior 10 bars
            search_start = max(0, break_idx - 10)
            candidate_idx = None
            highest_val = float("-inf")

            for j in range(break_idx - 1, search_start - 1, -1):
                c = candles[j]
                if c.is_bullish and c.high > highest_val:
                    highest_val = c.high
                    candidate_idx = j

            if candidate_idx is not None:
                ob_candle = candles[candidate_idx]
                top = ob_candle.high
                bottom = ob_candle.low

                mitigated = False
                mitigated_at = None
                is_breaker = False

                for k in range(break_idx, n):
                    if candles[k].high > top:
                        is_breaker = True
                    if candles[k].high >= bottom and not mitigated:
                        mitigated = True
                        mitigated_at = candles[k].timestamp

                order_blocks.append(
                    OrderBlock(
                        index=candidate_idx,
                        timestamp=ob_candle.timestamp,
                        ob_type="BEARISH",
                        top=round(top, 2),
                        bottom=round(bottom, 2),
                        is_breaker=is_breaker,
                        mitigated=mitigated,
                        mitigated_at=mitigated_at,
                        volume=ob_candle.volume,
                        timeframe=timeframe,
                    )
                )

    return order_blocks
