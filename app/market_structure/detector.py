"""
Market Structure Detector (HH, HL, LH, LL and Trend Analysis).
"""

from app.core.constants import MarketBias, StructureType, TimeFrame
from app.data.models import Candle
from app.indicators.atr import calculate_atr
from app.indicators.ema import calculate_ema
from app.indicators.swings import detect_swings
from app.market_structure.models import MarketStructureAnalysis, StructureNode


class MarketStructureDetector:
    """Deterministic Market Structure Detection Engine."""

    def __init__(self, left_bars: int = 3, right_bars: int = 3, atr_period: int = 14):
        self.left_bars = left_bars
        self.right_bars = right_bars
        self.atr_period = atr_period

    def analyze(self, candles: list[Candle], timeframe: TimeFrame) -> MarketStructureAnalysis:
        """Analyzes market structure for a given candle series and timeframe."""
        if len(candles) < 20:
            return MarketStructureAnalysis(
                timeframe=timeframe,
                trend=MarketBias.NEUTRAL,
                summary="Insufficient candle history for market structure analysis.",
            )

        swings = detect_swings(candles, left_bars=self.left_bars, right_bars=self.right_bars)
        atr_values = calculate_atr(candles, period=self.atr_period)
        latest_atr = atr_values[-1] if atr_values else 0.0

        ema_50 = calculate_ema(candles, period=50)
        ema_200 = calculate_ema(candles, period=200)

        # Classify EMA Trend
        ema_trend = MarketBias.NEUTRAL
        if len(candles) >= 50:
            latest_c = candles[-1].close
            e50 = ema_50[-1]
            if len(candles) >= 200:
                e200 = ema_200[-1]
                if latest_c > e50 > e200:
                    ema_trend = MarketBias.BULLISH
                elif latest_c < e50 < e200:
                    ema_trend = MarketBias.BEARISH
            else:
                ema_trend = MarketBias.BULLISH if latest_c > e50 else MarketBias.BEARISH

        if len(swings) < 2:
            return MarketStructureAnalysis(
                timeframe=timeframe,
                trend=ema_trend,
                atr=latest_atr,
                ema_trend=ema_trend,
                summary=f"Detected {len(swings)} swing points. Following EMA bias ({ema_trend.value}).",
            )

        # Classify Swing Nodes (HH, HL, LH, LL)
        highs = [s for s in swings if s.point_type == "HIGH"]
        lows = [s for s in swings if s.point_type == "LOW"]

        structure_nodes: list[StructureNode] = []

        # Analyze Highs
        for i in range(len(highs)):
            if i == 0:
                continue
            prev_h = highs[i - 1]
            curr_h = highs[i]
            stype = StructureType.HIGHER_HIGH if curr_h.price > prev_h.price else StructureType.LOWER_HIGH
            structure_nodes.append(
                StructureNode(
                    index=curr_h.index,
                    timestamp=curr_h.timestamp,
                    price=curr_h.price,
                    structure_type=stype,
                    swing_type="HIGH",
                    confidence=1.0,
                )
            )

        # Analyze Lows
        for i in range(len(lows)):
            if i == 0:
                continue
            prev_l = lows[i - 1]
            curr_l = lows[i]
            stype = StructureType.HIGHER_LOW if curr_l.price > prev_l.price else StructureType.LOWER_LOW
            structure_nodes.append(
                StructureNode(
                    index=curr_l.index,
                    timestamp=curr_l.timestamp,
                    price=curr_l.price,
                    structure_type=stype,
                    swing_type="LOW",
                    confidence=1.0,
                )
            )

        structure_nodes.sort(key=lambda n: n.index)

        last_high = highs[-1] if highs else None
        last_low = lows[-1] if lows else None

        # Determine Overall Trend
        recent_high_stypes = [n.structure_type for n in structure_nodes if n.swing_type == "HIGH"]
        recent_low_stypes = [n.structure_type for n in structure_nodes if n.swing_type == "LOW"]

        last_high_stype = recent_high_stypes[-1] if recent_high_stypes else None
        last_low_stype = recent_low_stypes[-1] if recent_low_stypes else None

        if last_high_stype == StructureType.HIGHER_HIGH and last_low_stype == StructureType.HIGHER_LOW:
            trend = MarketBias.BULLISH
        elif last_high_stype == StructureType.LOWER_HIGH and last_low_stype == StructureType.LOWER_LOW:
            trend = MarketBias.BEARISH
        elif last_high_stype == StructureType.HIGHER_HIGH and last_low_stype == StructureType.LOWER_LOW or last_high_stype == StructureType.LOWER_HIGH and last_low_stype == StructureType.HIGHER_LOW:
            trend = MarketBias.RANGING
        else:
            trend = ema_trend

        summary = f"{timeframe.value} Structure: {trend.value} (Last High: {last_high_stype.value if last_high_stype else 'N/A'}, Last Low: {last_low_stype.value if last_low_stype else 'N/A'}). ATR={latest_atr}."

        return MarketStructureAnalysis(
            timeframe=timeframe,
            trend=trend,
            last_swing_high=last_high,
            last_swing_low=last_low,
            structure_points=structure_nodes,
            ema_trend=ema_trend,
            atr=latest_atr,
            summary=summary,
        )
