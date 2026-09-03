"""
Liquidity Pool Detection & Sweep Tracking.
"""


from app.core.constants import LiquidityType, TimeFrame
from app.data.models import Candle
from app.indicators.swings import detect_swings
from app.smc.models import LiquidityPool


def detect_liquidity_pools(
    candles: list[Candle],
    timeframe: TimeFrame,
    left_bars: int = 3,
    right_bars: int = 3,
    tolerance: float = 0.6,  # $0.60 price proximity for Equal Highs / Lows on XAU/USD
) -> tuple[list[LiquidityPool], list[LiquidityPool]]:
    """
    Detects Liquidity pools (Equal Highs, Equal Lows, Swing Highs/Lows) and identifies sweeps.
    A sweep occurs when subsequent price wicks past the liquidity pool level but candle closes back inside.
    """
    if len(candles) < 20:
        return [], []

    swings = detect_swings(candles, left_bars=left_bars, right_bars=right_bars)
    if not swings:
        return [], []

    highs = [s for s in swings if s.point_type == "HIGH"]
    lows = [s for s in swings if s.point_type == "LOW"]

    pools: list[LiquidityPool] = []
    n = len(candles)

    # 1. Detect Equal Highs (Double/Triple Tops)
    for i in range(len(highs)):
        for j in range(i + 1, len(highs)):
            h1 = highs[i]
            h2 = highs[j]
            if abs(h1.price - h2.price) <= tolerance:
                pools.append(
                    LiquidityPool(
                        index=h2.index,
                        timestamp=h2.timestamp,
                        pool_type=LiquidityType.EQUAL_HIGHS,
                        price_level=round(max(h1.price, h2.price), 2),
                        tolerance=tolerance,
                        timeframe=timeframe,
                    )
                )

    # 2. Detect Equal Lows (Double/Triple Bottoms)
    for i in range(len(lows)):
        for j in range(i + 1, len(lows)):
            l1 = lows[i]
            l2 = lows[j]
            if abs(l1.price - l2.price) <= tolerance:
                pools.append(
                    LiquidityPool(
                        index=l2.index,
                        timestamp=l2.timestamp,
                        pool_type=LiquidityType.EQUAL_LOWS,
                        price_level=round(min(l1.price, l2.price), 2),
                        tolerance=tolerance,
                        timeframe=timeframe,
                    )
                )

    # 3. Add latest major Buy-side & Sell-side swing liquidity
    if highs:
        latest_h = highs[-1]
        pools.append(
            LiquidityPool(
                index=latest_h.index,
                timestamp=latest_h.timestamp,
                pool_type=LiquidityType.BUY_SIDE,
                price_level=latest_h.price,
                tolerance=tolerance,
                timeframe=timeframe,
            )
        )

    if lows:
        latest_l = lows[-1]
        pools.append(
            LiquidityPool(
                index=latest_l.index,
                timestamp=latest_l.timestamp,
                pool_type=LiquidityType.SELL_SIDE,
                price_level=latest_l.price,
                tolerance=tolerance,
                timeframe=timeframe,
            )
        )

    # Merge pools within tolerance into one cluster per level
    merged = []
    for pool in pools:
        if not merged or abs(pool.price_level - merged[-1].price_level) > tolerance:
            merged.append(pool)
        else:
            merged[-1] = pool  # keep the most recent (last)
    pools = merged[-10:]

    # 4. Check for Liquidity Sweeps
    swept_pools: list[LiquidityPool] = []
    for pool in pools:
        for k in range(pool.index + 1, n):
            c = candles[k]
            # Bullish Sweep of Sell-side liquidity (False breakdown: low < pool level, close > pool level)
            if pool.pool_type in [LiquidityType.SELL_SIDE, LiquidityType.EQUAL_LOWS]:
                if c.low < pool.price_level and c.close > pool.price_level:
                    pool.swept = True
                    pool.swept_at = c.timestamp
                    swept_pools.append(pool)
                    break
            # Bearish Sweep of Buy-side liquidity (False breakout: high > pool level, close < pool level)
            elif pool.pool_type in [LiquidityType.BUY_SIDE, LiquidityType.EQUAL_HIGHS]:
                if c.high > pool.price_level and c.close < pool.price_level:
                    pool.swept = True
                    pool.swept_at = c.timestamp
                    swept_pools.append(pool)
                    break

    return pools, swept_pools
