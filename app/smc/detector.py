"""
Unified Smart Money Concepts (SMC) Analysis Engine.
"""


from app.core.constants import TimeFrame, ZoneType
from app.data.models import Candle
from app.smc.bos_choch import detect_bos_choch
from app.smc.fvg import detect_fvgs
from app.smc.liquidity import detect_liquidity_pools
from app.smc.models import SMCAnalysis
from app.smc.order_blocks import detect_order_blocks


class SMCEngine:
    """Deterministic Smart Money Concepts Engine."""

    def __init__(self, left_bars: int = 3, right_bars: int = 3):
        self.left_bars = left_bars
        self.right_bars = right_bars

    def analyze(self, candles: list[Candle], timeframe: TimeFrame) -> SMCAnalysis:
        """Runs full SMC analysis for a timeframe."""
        if len(candles) < 20:
            return SMCAnalysis(
                timeframe=timeframe,
                current_zone=ZoneType.EQUILIBRIUM,
                equilibrium_price=0.0,
                range_high=0.0,
                range_low=0.0,
                summary="Insufficient data for SMC analysis.",
            )

        # 1. Premium / Discount equilibrium
        recent_high = max(c.high for c in candles[-50:])
        recent_low = min(c.low for c in candles[-50:])
        eq_price = round((recent_high + recent_low) / 2.0, 2)
        curr_price = candles[-1].close

        if curr_price > eq_price + 0.5:
            current_zone = ZoneType.PREMIUM
        elif curr_price < eq_price - 0.5:
            current_zone = ZoneType.DISCOUNT
        else:
            current_zone = ZoneType.EQUILIBRIUM

        # 2. Breaks (BOS / CHoCH)
        breaks = detect_bos_choch(candles, timeframe, self.left_bars, self.right_bars)
        latest_break = breaks[-1] if breaks else None

        # 3. Fair Value Gaps
        all_fvgs = detect_fvgs(candles, timeframe)
        active_fvgs = [f for f in all_fvgs if not f.mitigated][-10:]

        # 4. Order Blocks
        all_obs = detect_order_blocks(candles, breaks, timeframe)
        active_obs = [ob for ob in all_obs if not ob.mitigated][-10:]

        # 5. Liquidity Pools & Sweeps
        pools, sweeps = detect_liquidity_pools(candles, timeframe, self.left_bars, self.right_bars)

        summary = (
            f"{timeframe.value} SMC: Zone={current_zone.value} (Eq: {eq_price}). "
            f"Latest Break: {latest_break.description if latest_break else 'None'}. "
            f"Active FVGs: {len(active_fvgs)}, Active OBs: {len(active_obs)}, Sweeps: {len(sweeps)}."
        )

        return SMCAnalysis(
            timeframe=timeframe,
            current_zone=current_zone,
            equilibrium_price=eq_price,
            range_high=round(recent_high, 2),
            range_low=round(recent_low, 2),
            latest_break=latest_break,
            active_fvgs=active_fvgs,
            active_order_blocks=active_obs,
            liquidity_pools=pools[-10:],
            recent_sweeps=sweeps[-5:],
            summary=summary,
        )
