"""
Strategy B: Multi-Timeframe Smart Money Concepts (SMC) Strategy.
"""


from app.core.constants import (
    LiquidityType,
    SignalDirection,
    StrategyType,
    StructureType,
    TimeFrame,
    ZoneType,
)
from app.data.models import MultiTimeframeSnapshot
from app.market_structure.detector import MarketStructureDetector
from app.smc.detector import SMCEngine
from app.strategies.base import BaseStrategy, StrategyCandidate


class SMCStrategy(BaseStrategy):
    """
    Multi-timeframe Smart Money Concepts Strategy:
    1. 4H: Macro trend & equilibrium (Premium vs Discount).
    2. 1H: Structural BOS / CHoCH confirmation & Key Order Blocks.
    3. 30M: Fair Value Gap (FVG) or Liquidity Sweep.
    4. 15M: Lower-timeframe CHoCH & execution trigger.
    """

    def __init__(self):
        self.ms_detector = MarketStructureDetector(left_bars=3, right_bars=3)
        self.smc_engine = SMCEngine(left_bars=3, right_bars=3)

    def evaluate(self, snapshot: MultiTimeframeSnapshot) -> StrategyCandidate | None:
        if not snapshot.h4 or not snapshot.h1 or not snapshot.m30 or not snapshot.m15:
            return None

        h4_smc = self.smc_engine.analyze(snapshot.h4, TimeFrame.H4)
        h1_smc = self.smc_engine.analyze(snapshot.h1, TimeFrame.H1)
        m30_smc = self.smc_engine.analyze(snapshot.m30, TimeFrame.M30)
        m15_smc = self.smc_engine.analyze(snapshot.m15, TimeFrame.M15)

        curr_p = snapshot.current_price
        reasons = []

        # Check Bullish SMC Setup
        # Condition: 4H Discount zone or 1H Bullish BOS/CHoCH + 30M Bullish FVG/OB + 15M Bullish CHoCH or candle confirmation
        is_bullish_aligned = (
            h1_smc.latest_break and h1_smc.latest_break.break_type in [StructureType.BOS_BULLISH, StructureType.CHOCH_BULLISH]
        ) or (
            h4_smc.current_zone == ZoneType.DISCOUNT
            and any(sweep.pool_type in (LiquidityType.SELL_SIDE, LiquidityType.EQUAL_LOWS)
                    for sweep in m30_smc.recent_sweeps)
        )

        if is_bullish_aligned:
            # Look for active unmitigated 30m/15m bullish OB or FVG near current price
            bullish_obs = [ob for ob in m30_smc.active_order_blocks if ob.ob_type == "BULLISH"]
            bullish_fvgs = [f for f in m30_smc.active_fvgs if f.gap_type == "BULLISH"]

            active_support = None
            if bullish_obs:
                active_support = bullish_obs[-1].bottom
            elif bullish_fvgs:
                active_support = bullish_fvgs[-1].bottom
            else:
                active_support = snapshot.m30[-1].low - 2.0

            sl = round(active_support - 1.5, 2)
            # Geometry sanity: a LONG must never have its stop at or above entry.
            if sl >= curr_p:
                return None
            risk = curr_p - sl
            tp1 = round(curr_p + (risk * 1.5), 2)
            tp2 = round(curr_p + (risk * 2.5), 2)
            tp3 = round(curr_p + (risk * 4.0), 2)
            rr = round((tp2 - curr_p) / risk, 2)

            reasons.append(f"1H Break: {h1_smc.latest_break.description if h1_smc.latest_break else 'Discount Zone Accumulation'}")
            if bullish_fvgs:
                reasons.append(f"30M Bullish FVG tap at {bullish_fvgs[-1].bottom}-{bullish_fvgs[-1].top}")
            if m30_smc.recent_sweeps:
                reasons.append(f"Sell-side Liquidity swept at {m30_smc.recent_sweeps[-1].price_level}")

            confidence = 75.0 + (10.0 if h4_smc.current_zone == ZoneType.DISCOUNT else 0.0)

            return StrategyCandidate(
                strategy_type=StrategyType.SMC,
                direction=SignalDirection.LONG,
                entry_price=curr_p,
                stop_loss=sl,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                risk_reward=rr,
                confidence=confidence,
                reasons=reasons,
                invalidation_level=sl,
                metadata={"zone": h4_smc.current_zone.value, "eq": h4_smc.equilibrium_price},
            )

        # Check Bearish SMC Setup
        is_bearish_aligned = (
            h1_smc.latest_break and h1_smc.latest_break.break_type in [StructureType.BOS_BEARISH, StructureType.CHOCH_BEARISH]
        ) or (
            h4_smc.current_zone == ZoneType.PREMIUM
            and any(sweep.pool_type in (LiquidityType.BUY_SIDE, LiquidityType.EQUAL_HIGHS)
                    for sweep in m30_smc.recent_sweeps)
        )

        if is_bearish_aligned:
            bearish_obs = [ob for ob in m30_smc.active_order_blocks if ob.ob_type == "BEARISH"]
            bearish_fvgs = [f for f in m30_smc.active_fvgs if f.gap_type == "BEARISH"]

            active_resistance = None
            if bearish_obs:
                active_resistance = bearish_obs[-1].top
            elif bearish_fvgs:
                active_resistance = bearish_fvgs[-1].top
            else:
                active_resistance = snapshot.m30[-1].high + 2.0

            sl = round(active_resistance + 1.5, 2)
            # Geometry sanity: a SHORT must never have its stop at or below entry.
            if sl <= curr_p:
                return None
            risk = sl - curr_p
            tp1 = round(curr_p - (risk * 1.5), 2)
            tp2 = round(curr_p - (risk * 2.5), 2)
            tp3 = round(curr_p - (risk * 4.0), 2)
            rr = round((curr_p - tp2) / risk, 2)

            reasons.append(f"1H Break: {h1_smc.latest_break.description if h1_smc.latest_break else 'Premium Zone Distribution'}")
            if bearish_fvgs:
                reasons.append(f"30M Bearish FVG tap at {bearish_fvgs[-1].bottom}-{bearish_fvgs[-1].top}")
            if m30_smc.recent_sweeps:
                reasons.append(f"Buy-side Liquidity swept at {m30_smc.recent_sweeps[-1].price_level}")

            confidence = 75.0 + (10.0 if h4_smc.current_zone == ZoneType.PREMIUM else 0.0)

            return StrategyCandidate(
                strategy_type=StrategyType.SMC,
                direction=SignalDirection.SHORT,
                entry_price=curr_p,
                stop_loss=sl,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                risk_reward=rr,
                confidence=confidence,
                reasons=reasons,
                invalidation_level=sl,
                metadata={"zone": h4_smc.current_zone.value, "eq": h4_smc.equilibrium_price},
            )

        return None
