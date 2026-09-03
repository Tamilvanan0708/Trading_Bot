"""
Fibonacci Retracement & Extension Calculator.
"""


from app.core.constants import MarketBias, SignalDirection
from app.data.models import Candle
from app.fibonacci.models import FibonacciSetup
from app.indicators.swings import detect_swings

FIB_RATIOS = [0.0, 0.236, 0.382, 0.500, 0.618, 0.786, 1.000, 1.618]
GOLDEN_POCKET_RATIOS = [0.500, 0.618, 0.786]


class FibonacciEngine:
    """Deterministic Fibonacci Retracement & Extension Engine."""

    def __init__(self, left_bars: int = 3, right_bars: int = 3):
        self.left_bars = left_bars
        self.right_bars = right_bars

    def calculate_levels(
        self,
        swing_low: float,
        swing_high: float,
        is_bullish_impulse: bool,
    ) -> dict[float, float]:
        """
        Calculates price corresponding to each Fibonacci ratio.
        """
        price_range = swing_high - swing_low
        levels: dict[float, float] = {}

        for ratio in FIB_RATIOS:
            if is_bullish_impulse:
                # Bullish impulse (Upward move from low to high; retracing down)
                # 0.0 is the high, 1.0 is the low
                if ratio == 1.618:
                    # Extension target above the high
                    price = swing_high + 0.618 * price_range
                else:
                    price = swing_high - ratio * price_range
            else:
                # Bearish impulse (Downward move from high to low; retracing up)
                # 0.0 is the low, 1.0 is the high
                if ratio == 1.618:
                    # Extension target below the low
                    price = swing_low - 0.618 * price_range
                else:
                    price = swing_low + ratio * price_range

            levels[ratio] = round(price, 2)

        return levels

    def evaluate_setup(
        self,
        candles: list[Candle],
        trend_bias: MarketBias | None = None,
    ) -> FibonacciSetup | None:
        """
        Detects latest prominent swing high and swing low and evaluates current price retracement.
        """
        if len(candles) < 20:
            return None

        swings = detect_swings(candles, left_bars=self.left_bars, right_bars=self.right_bars)
        if len(swings) < 2:
            return None

        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]

        if not highs or not lows:
            return None

        last_high = highs[-1]
        last_low = lows[-1]

        current_price = candles[-1].close
        price_range = last_high.price - last_low.price
        if price_range <= 0.5:  # Filter out negligible noise swings
            return None

        # Determine impulse direction based on chronological sequence of swings
        is_bullish_impulse = last_high.index > last_low.index
        direction = SignalDirection.LONG if is_bullish_impulse else SignalDirection.SHORT

        # Check alignment with trend_bias if provided
        if trend_bias == MarketBias.BULLISH and not is_bullish_impulse:
            return None  # Counter-trend Fibonacci is not valid
        elif trend_bias == MarketBias.BEARISH and is_bullish_impulse:
            return None

        levels = self.calculate_levels(last_low.price, last_high.price, is_bullish_impulse)

        # Golden pocket definition (50% to 78.6% retracement)
        if is_bullish_impulse:
            entry_zone_max = levels[0.500]
            entry_zone_min = levels[0.786]
            in_golden_pocket = entry_zone_min <= current_price <= entry_zone_max
            suggested_entry = levels[0.618]
            suggested_sl = round(last_low.price - (price_range * 0.05), 2)  # SL just below swing low
            invalidation_price = last_low.price
            tp1 = levels[0.382]
            tp2 = last_high.price  # 0.0 level
            tp3 = levels[1.618]     # 1.618 extension target
            risk = abs(suggested_entry - suggested_sl)
            reward = abs(tp2 - suggested_entry)
            rr = round(reward / risk, 2) if risk > 0 else 0.0
            valid = current_price >= last_low.price and in_golden_pocket
            reason = f"Bullish Fibonacci pullback into Golden Zone ({entry_zone_min} - {entry_zone_max}). Target={tp2}."
        else:
            entry_zone_min = levels[0.500]
            entry_zone_max = levels[0.786]
            in_golden_pocket = entry_zone_min <= current_price <= entry_zone_max
            suggested_entry = levels[0.618]
            suggested_sl = round(last_high.price + (price_range * 0.05), 2)  # SL just above swing high
            invalidation_price = last_high.price
            tp1 = levels[0.382]
            tp2 = last_low.price   # 0.0 level
            tp3 = levels[1.618]     # 1.618 extension target
            risk = abs(suggested_sl - suggested_entry)
            reward = abs(suggested_entry - tp2)
            rr = round(reward / risk, 2) if risk > 0 else 0.0
            valid = current_price <= last_high.price and in_golden_pocket
            reason = f"Bearish Fibonacci pullback into Golden Zone ({entry_zone_min} - {entry_zone_max}). Target={tp2}."

        # Find closest active ratio
        active_ratio = None
        min_dist = float("inf")
        for ratio, lvl_p in levels.items():
            dist = abs(current_price - lvl_p)
            if dist < min_dist:
                min_dist = dist
                active_ratio = ratio

        return FibonacciSetup(
            direction=direction,
            swing_high=last_high,
            swing_low=last_low,
            price_range=round(price_range, 2),
            current_price=current_price,
            levels=levels,
            active_level_ratio=active_ratio,
            in_golden_pocket=in_golden_pocket,
            entry_zone_min=entry_zone_min,
            entry_zone_max=entry_zone_max,
            suggested_entry=suggested_entry,
            suggested_sl=suggested_sl,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            risk_reward=rr,
            valid=valid,
            invalidation_price=invalidation_price,
            reason=reason,
        )
