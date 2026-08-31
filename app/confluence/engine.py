"""
Multi-Timeframe Confluence Scoring Engine.
"""

from app.config.settings import Settings, get_settings
from app.confluence.models import ConfluenceBreakdown, ConfluenceItem, ConfluenceScore
from app.core.constants import (
    MarketBias,
    SignalDirection,
    SignalQuality,
    ZoneType,
)
from app.data.models import MultiTimeframeSnapshot
from app.fibonacci.calculator import FibonacciEngine
from app.market_structure.detector import MarketStructureDetector
from app.smc.detector import SMCEngine


class ConfluenceEngine:
    """
    Evaluates multi-timeframe confluence and computes a deterministic 0-100 score.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.ms_detector = MarketStructureDetector(left_bars=3, right_bars=3, atr_period=self.settings.ATR_PERIOD)
        self.fib_engine = FibonacciEngine(left_bars=3, right_bars=3)
        self.smc_engine = SMCEngine(left_bars=3, right_bars=3)

    def evaluate(self, snapshot: MultiTimeframeSnapshot, entry: float | None = None,
                 stop_loss: float | None = None, take_profit: float | None = None,
                 mtf=None) -> ConfluenceScore:
        """Calculates multi-timeframe confluence score and directional alignment.

        ``mtf`` selects which snapshot series fills each analytical ROLE
        (htf / structure / setup / trigger).  ``None`` uses the production
        config (4H -> 1H -> 30M -> 15M).

        Optionally accepts entry / stop_loss / take_profit to compute the
        actual Risk/Reward component score.  If omitted the R:R component
        defaults to 0 (unchanged threshold check).
        """
        from app.core.mtf_config import resolve_mtf
        mtf = resolve_mtf(mtf)
        htf_series = snapshot.get_series(mtf.htf)
        structure_series = snapshot.get_series(mtf.structure)
        setup_series = snapshot.get_series(mtf.setup)
        trigger_series = snapshot.get_series(mtf.trigger)
        if not htf_series or not structure_series or not setup_series or not trigger_series:
            return self._empty_score("Insufficient multi-timeframe data.")

        # Multi-timeframe analyses (roles configured by ``mtf``)
        h4_struct = self.ms_detector.analyze(htf_series, mtf.htf)
        h1_struct = self.ms_detector.analyze(structure_series, mtf.structure)
        m30_smc = self.smc_engine.analyze(setup_series, mtf.setup)
        h4_smc = self.smc_engine.analyze(htf_series, mtf.htf)
        m30_fib = self.fib_engine.evaluate_setup(setup_series, trend_bias=h4_struct.trend)
        latest_trigger = trigger_series[-1]

        # Determine Primary Direction candidate
        bullish_votes = 0
        bearish_votes = 0

        if h4_struct.trend == MarketBias.BULLISH:
            bullish_votes += 2
        elif h4_struct.trend == MarketBias.BEARISH:
            bearish_votes += 2

        if h1_struct.trend == MarketBias.BULLISH:
            bullish_votes += 1
        elif h1_struct.trend == MarketBias.BEARISH:
            bearish_votes += 1

        if m30_fib and m30_fib.valid:
            if m30_fib.direction == SignalDirection.LONG:
                bullish_votes += 1
            elif m30_fib.direction == SignalDirection.SHORT:
                bearish_votes += 1

        # Check for direct conflicts
        conflicts = []
        if h4_struct.trend == MarketBias.BULLISH and h1_struct.trend == MarketBias.BEARISH:
            conflicts.append(f"Conflict: {mtf.htf.value.upper()} Macro is Bullish but {mtf.structure.value.upper()} Structure is Bearish")
        elif h4_struct.trend == MarketBias.BEARISH and h1_struct.trend == MarketBias.BULLISH:
            conflicts.append(f"Conflict: {mtf.htf.value.upper()} Macro is Bearish but {mtf.structure.value.upper()} Structure is Bullish")

        if bullish_votes > bearish_votes:
            direction = SignalDirection.LONG
        elif bearish_votes > bullish_votes:
            direction = SignalDirection.SHORT
        else:
            direction = SignalDirection.NO_TRADE

        # Calculate Individual Component Scores
        reasons = []

        # 1. Higher Timeframe Bias (Max 20)
        w_htf = self.settings.WEIGHT_HTF_BIAS
        htf_passed = False
        htf_pts = 0.0
        htf_detail = f"{mtf.htf.value.upper()} Trend={h4_struct.trend.value}"
        if direction == SignalDirection.LONG and h4_struct.trend == MarketBias.BULLISH:
            htf_pts = w_htf
            htf_passed = True
            reasons.append(f"{mtf.htf.value.upper()} Macro Trend is strongly Bullish (+20)")
        elif direction == SignalDirection.SHORT and h4_struct.trend == MarketBias.BEARISH:
            htf_pts = w_htf
            htf_passed = True
            reasons.append(f"{mtf.htf.value.upper()} Macro Trend is strongly Bearish (+20)")
        elif h4_struct.trend == MarketBias.RANGING:
            htf_pts = w_htf * 0.5
            htf_detail += " (Ranging - partial score)"

        # 2. Market Structure (Max 20)
        w_ms = self.settings.WEIGHT_MARKET_STRUCTURE
        ms_passed = False
        ms_pts = 0.0
        ms_detail = f"{mtf.structure.value.upper()} Trend={h1_struct.trend.value}"
        if direction == SignalDirection.LONG and h1_struct.trend == MarketBias.BULLISH:
            ms_pts = w_ms
            ms_passed = True
            reasons.append(f"{mtf.structure.value.upper()} Market Structure aligned Bullish (+20)")
        elif direction == SignalDirection.SHORT and h1_struct.trend == MarketBias.BEARISH:
            ms_pts = w_ms
            ms_passed = True
            reasons.append(f"{mtf.structure.value.upper()} Market Structure aligned Bearish (+20)")
        elif h1_struct.trend == MarketBias.RANGING:
            ms_pts = w_ms * 0.4

        # 3. SMC Confirmation (Max 20)
        w_smc = self.settings.WEIGHT_SMC_CONFIRMATION
        smc_passed = False
        smc_pts = 0.0
        smc_detail = f"SMC Zone={h4_smc.current_zone.value}"
        if direction == SignalDirection.LONG:
            if h4_smc.current_zone == ZoneType.DISCOUNT:
                smc_pts += w_smc * 0.5
                smc_detail += " (Discount zone accumulation)"
            if any(f.gap_type == "BULLISH" for f in m30_smc.active_fvgs):
                smc_pts += w_smc * 0.5
                reasons.append(f"{mtf.setup.value.upper()} Bullish Fair Value Gap present (+10)")
            smc_pts = min(w_smc, smc_pts)
            smc_passed = smc_pts >= (w_smc * 0.5)
        elif direction == SignalDirection.SHORT:
            if h4_smc.current_zone == ZoneType.PREMIUM:
                smc_pts += w_smc * 0.5
                smc_detail += " (Premium zone distribution)"
            if any(f.gap_type == "BEARISH" for f in m30_smc.active_fvgs):
                smc_pts += w_smc * 0.5
                reasons.append(f"{mtf.setup.value.upper()} Bearish Fair Value Gap present (+10)")
            smc_pts = min(w_smc, smc_pts)
            smc_passed = smc_pts >= (w_smc * 0.5)

        # 4. Fibonacci Confirmation (Max 15)
        w_fib = self.settings.WEIGHT_FIB_CONFIRMATION
        fib_passed = False
        fib_pts = 0.0
        fib_detail = "No active Golden Pocket retracement"
        if m30_fib and m30_fib.valid and m30_fib.direction == direction:
            fib_detail = f"Pullback at {m30_fib.active_level_ratio} Fib Level"
            if m30_fib.in_golden_pocket:
                fib_pts = w_fib
                fib_passed = True
                reasons.append(f"Price active inside {mtf.setup.value.upper()} Fibonacci Golden Zone 50-78.6% (+{w_fib})")
            else:
                fib_pts = w_fib * 0.5
                fib_passed = True

        # 5. Liquidity Confirmation (Max 10)
        w_liq = self.settings.WEIGHT_LIQUIDITY
        liq_passed = False
        liq_pts = 0.0
        liq_detail = "No recent sweeps detected"
        if len(m30_smc.recent_sweeps) > 0:
            sweep = m30_smc.recent_sweeps[-1]
            if (direction == SignalDirection.LONG and "SELL_SIDE" in sweep.pool_type.value) or \
               (direction == SignalDirection.SHORT and "BUY_SIDE" in sweep.pool_type.value) or \
               ("EQUAL" in sweep.pool_type.value):
                liq_pts = w_liq
                liq_passed = True
                liq_detail = f"Liquidity swept at {sweep.price_level}"
                reasons.append(f"Institutional liquidity sweep confirmed ({sweep.pool_type.value}) (+10)")

        # 6. Entry Trigger Confirmation (Max 10)
        w_entry = self.settings.WEIGHT_ENTRY_CONFIRMATION
        entry_passed = False
        entry_pts = 0.0
        entry_detail = "Awaiting candle confirmation"
        if direction == SignalDirection.LONG:
            if latest_trigger.is_bullish or latest_trigger.lower_wick > (latest_trigger.total_range * 0.35):
                entry_pts = w_entry
                entry_passed = True
                entry_detail = f"{mtf.trigger.value.upper()} Bullish rejection/trigger candle"
                reasons.append(f"{mtf.trigger.value.upper()} Bullish rejection candle confirmed entry trigger (+10)")
        elif direction == SignalDirection.SHORT:
            if latest_trigger.is_bearish or latest_trigger.upper_wick > (latest_trigger.total_range * 0.35):
                entry_pts = w_entry
                entry_passed = True
                entry_detail = f"{mtf.trigger.value.upper()} Bearish rejection/trigger candle"
                reasons.append(f"{mtf.trigger.value.upper()} Bearish rejection candle confirmed entry trigger (+10)")

        # 7. Risk / Reward Ratio (Max 5) — computed from actual SL/TP if provided
        w_rr = self.settings.WEIGHT_RISK_REWARD
        rr_pts = 0.0
        rr_passed = False
        rr_detail = "R:R not computed (no SL/TP provided)"
        if entry is not None and stop_loss is not None and take_profit is not None:
            sl_dist = abs(entry - stop_loss)
            tp_dist = abs(take_profit - entry)
            if sl_dist > 0.0:
                actual_rr = round(tp_dist / sl_dist, 2)
                min_rr = self.settings.MIN_RISK_REWARD
                if actual_rr >= min_rr:
                    rr_pts = w_rr
                    rr_passed = True
                    rr_detail = f"Projected R:R 1:{actual_rr} >= {min_rr} (+{w_rr})"
                    reasons.append(f"Risk/Reward ratio 1:{actual_rr} meets minimum 1:{min_rr} (+{w_rr})")
                else:
                    rr_detail = f"Projected R:R 1:{actual_rr} < minimum 1:{min_rr} (no points)"
            else:
                rr_detail = "Stop loss distance is zero (cannot compute R:R)"

        total_score = round(htf_pts + ms_pts + smc_pts + fib_pts + liq_pts + entry_pts + rr_pts, 1)

        # Categorize Signal Quality
        quality = self.categorize_quality(total_score)

        # Hard guardrail: If quality is WEAK or NO_TRADE, direction must be NO_TRADE
        if quality in [SignalQuality.WEAK, SignalQuality.NO_TRADE] or len(conflicts) > 0:
            if len(conflicts) > 0:
                direction = SignalDirection.NO_TRADE

        breakdown = ConfluenceBreakdown(
            htf_bias=ConfluenceItem(category=f"{mtf.htf.value.upper()} Macro Bias", points_awarded=htf_pts, max_points=w_htf, passed=htf_passed, details=htf_detail),
            market_structure=ConfluenceItem(category=f"{mtf.structure.value.upper()} Market Structure", points_awarded=ms_pts, max_points=w_ms, passed=ms_passed, details=ms_detail),
            smc_confirmation=ConfluenceItem(category="SMC Patterns (FVG/OB/Zone)", points_awarded=smc_pts, max_points=w_smc, passed=smc_passed, details=smc_detail),
            fib_confirmation=ConfluenceItem(category="Fibonacci Golden Zone", points_awarded=fib_pts, max_points=w_fib, passed=fib_passed, details=fib_detail),
            liquidity_confirmation=ConfluenceItem(category="Liquidity Sweeps", points_awarded=liq_pts, max_points=w_liq, passed=liq_passed, details=liq_detail),
            entry_confirmation=ConfluenceItem(category=f"{mtf.trigger.value.upper()} Entry Rejection", points_awarded=entry_pts, max_points=w_entry, passed=entry_passed, details=entry_detail),
            risk_reward=ConfluenceItem(category="Risk / Reward >= 1.5", points_awarded=rr_pts, max_points=w_rr, passed=rr_passed, details=rr_detail),
        )

        return ConfluenceScore(
            total_score=total_score,
            quality=quality,
            direction=direction,
            is_tradable=quality in [SignalQuality.STRONG, SignalQuality.VERY_STRONG] and direction != SignalDirection.NO_TRADE,
            breakdown=breakdown,
            reasons=reasons,
            conflicts=conflicts,
        )

    def _empty_score(self, reason: str) -> ConfluenceScore:
        empty_item = ConfluenceItem(category="", points_awarded=0, max_points=0, passed=False, details=reason)
        return ConfluenceScore(
            total_score=0.0,
            quality=SignalQuality.NO_TRADE,
            direction=SignalDirection.NO_TRADE,
            is_tradable=False,
            breakdown=ConfluenceBreakdown(
                htf_bias=empty_item,
                market_structure=empty_item,
                smc_confirmation=empty_item,
                fib_confirmation=empty_item,
                liquidity_confirmation=empty_item,
                entry_confirmation=empty_item,
                risk_reward=empty_item,
            ),
            reasons=[],
            conflicts=[reason],
        )

    def categorize_quality(self, total_score: float) -> SignalQuality:
        if total_score >= self.settings.THRESHOLD_VERY_STRONG:
            return SignalQuality.VERY_STRONG
        if total_score >= self.settings.THRESHOLD_STRONG:
            return SignalQuality.STRONG
        if total_score >= self.settings.THRESHOLD_MODERATE:
            return SignalQuality.MODERATE
        if total_score >= self.settings.THRESHOLD_WEAK:
            return SignalQuality.WEAK
        return SignalQuality.NO_TRADE

    @staticmethod
    def compute_rr_score(
        entry: float,
        stop_loss: float,
        take_profit: float,
        min_rr: float,
        max_points: float = 5.0,
    ) -> tuple[float, bool, str]:
        """Computes the Risk/Reward component score from actual SL/TP geometry.

        Returns ``(points, passed, detail)`` where points equal ``max_points``
        only when the projected R:R meets the configured minimum.
        """
        sl_dist = abs(entry - stop_loss)
        tp_dist = abs(take_profit - entry)
        if sl_dist <= 0.0:
            return 0.0, False, "Stop loss distance is zero (cannot compute R:R)"
        actual_rr = round(tp_dist / sl_dist, 2)
        if actual_rr >= min_rr:
            return max_points, True, f"Projected R:R 1:{actual_rr} >= 1:{min_rr} (+{max_points})"
        return 0.0, False, f"Projected R:R 1:{actual_rr} < minimum 1:{min_rr} (no points)"
