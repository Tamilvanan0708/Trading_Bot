"""
Strategy A: Multi-Timeframe Fibonacci Retracement Strategy.
"""


from app.core.constants import MarketBias, SignalDirection, StrategyType, TimeFrame
from app.data.models import MultiTimeframeSnapshot
from app.fibonacci.calculator import FibonacciEngine
from app.market_structure.detector import MarketStructureDetector
from app.strategies.base import BaseStrategy, StrategyCandidate


class FibonacciRetracementStrategy(BaseStrategy):
    """
    Multi-timeframe Fibonacci Retracement Strategy:
    1. 4H / 1H: Macro trend direction (Bullish or Bearish).
    2. 30M: Price retracement into Golden Pocket zone (50.0% to 78.6%).
    3. 15M: Entry trigger & candle confirmation.
    """

    def __init__(self):
        self.ms_detector = MarketStructureDetector(left_bars=3, right_bars=3)
        self.fib_engine = FibonacciEngine(left_bars=3, right_bars=3)

    def evaluate(self, snapshot: MultiTimeframeSnapshot) -> StrategyCandidate | None:
        if not snapshot.h4 or not snapshot.h1 or not snapshot.m30 or not snapshot.m15:
            return None

        # 1. 4H Macro Bias
        h4_struct = self.ms_detector.analyze(snapshot.h4, TimeFrame.H4)
        h1_struct = self.ms_detector.analyze(snapshot.h1, TimeFrame.H1)

        # 2. 30M Fibonacci Setup
        fib_setup = self.fib_engine.evaluate_setup(snapshot.m30, trend_bias=h4_struct.trend)
        if not fib_setup or not fib_setup.valid or not fib_setup.in_golden_pocket:
            return None

        # 3. 15M Entry Confirmation
        latest_15m = snapshot.m15[-1]
        reasons = [
            f"4H Bias: {h4_struct.trend.value}",
            f"1H Structure: {h1_struct.trend.value}",
            fib_setup.reason,
        ]

        # Bullish setup check
        if fib_setup.direction == SignalDirection.LONG:
            if h4_struct.trend == MarketBias.BEARISH:
                return None  # Do not trade against 4H macro downtrend

            # Check 15m bullish rejection or green close
            if latest_15m.is_bullish or latest_15m.lower_wick > (latest_15m.total_range * 0.3):
                reasons.append("15M Bullish rejection candle at Fibonacci Golden Zone")
                confidence = 80.0 if h4_struct.trend == MarketBias.BULLISH and h1_struct.trend == MarketBias.BULLISH else 65.0
                return StrategyCandidate(
                    strategy_type=StrategyType.FIBONACCI,
                    direction=SignalDirection.LONG,
                    entry_price=snapshot.current_price,
                    stop_loss=fib_setup.suggested_sl,
                    tp1=fib_setup.tp1,
                    tp2=fib_setup.tp2,
                    tp3=fib_setup.tp3,
                    risk_reward=fib_setup.risk_reward,
                    confidence=confidence,
                    reasons=reasons,
                    invalidation_level=fib_setup.invalidation_price,
                    metadata={"fib_ratios": fib_setup.levels, "active_ratio": fib_setup.active_level_ratio},
                )

        # Bearish setup check
        elif fib_setup.direction == SignalDirection.SHORT:
            if h4_struct.trend == MarketBias.BULLISH:
                return None  # Do not trade against 4H macro uptrend

            if latest_15m.is_bearish or latest_15m.upper_wick > (latest_15m.total_range * 0.3):
                reasons.append("15M Bearish rejection candle at Fibonacci Golden Zone")
                confidence = 80.0 if h4_struct.trend == MarketBias.BEARISH and h1_struct.trend == MarketBias.BEARISH else 65.0
                return StrategyCandidate(
                    strategy_type=StrategyType.FIBONACCI,
                    direction=SignalDirection.SHORT,
                    entry_price=snapshot.current_price,
                    stop_loss=fib_setup.suggested_sl,
                    tp1=fib_setup.tp1,
                    tp2=fib_setup.tp2,
                    tp3=fib_setup.tp3,
                    risk_reward=fib_setup.risk_reward,
                    confidence=confidence,
                    reasons=reasons,
                    invalidation_level=fib_setup.invalidation_price,
                    metadata={"fib_ratios": fib_setup.levels, "active_ratio": fib_setup.active_level_ratio},
                )

        return None
