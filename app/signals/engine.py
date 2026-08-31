"""
Central Signal Engine.
"""


from app.config.settings import Settings, get_settings
from app.config.strategy_version import derive_strategy_version
from app.confluence.engine import ConfluenceEngine
from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.data.models import MultiTimeframeSnapshot
from app.indicators.atr import calculate_atr
from app.signals.models import SignalPayload
from app.strategies.fibonacci_strategy import FibonacciRetracementStrategy
from app.strategies.smc_strategy import SMCStrategy


class SignalEngine:
    """
    Centralized deterministic Signal Engine coordinating strategies, confluence scoring,
    and invalidation parameterization.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.confluence_engine = ConfluenceEngine(self.settings)
        self.fib_strategy = FibonacciRetracementStrategy()
        self.smc_strategy = SMCStrategy()

    def generate_signal(self, snapshot: MultiTimeframeSnapshot, mtf=None) -> SignalPayload:
        """
        Evaluates market snapshot across confluence and strategies, producing a validated SignalPayload.

        ``mtf`` selects the multi-timeframe configuration (None = production
        4H->1H->30M->15M).  The payload ``timeframe`` reflects the trigger
        timeframe of the active config.
        """
        from app.core.mtf_config import resolve_mtf
        mtf = resolve_mtf(mtf)
        confluence = self.confluence_engine.evaluate(snapshot, mtf=mtf)
        curr_p = snapshot.current_price
        ts = snapshot.timestamp

        # Hard rejection on conflicts or NO_TRADE direction
        if confluence.direction == SignalDirection.NO_TRADE or len(confluence.conflicts) > 0:
            return self._no_trade_payload(snapshot, confluence, mtf=mtf)

        # Evaluate strategy candidates
        fib_cand = self.fib_strategy.evaluate(snapshot)
        smc_cand = self.smc_strategy.evaluate(snapshot)

        # Pick best strategy candidate matching confluence direction
        chosen_candidate = None
        strategy_used = StrategyType.CONFLUENCE

        if fib_cand and fib_cand.direction == confluence.direction and smc_cand and smc_cand.direction == confluence.direction:
            chosen_candidate = fib_cand if fib_cand.risk_reward >= smc_cand.risk_reward else smc_cand
            strategy_used = StrategyType.CONFLUENCE
        elif fib_cand and fib_cand.direction == confluence.direction:
            chosen_candidate = fib_cand
            strategy_used = StrategyType.FIBONACCI
        elif smc_cand and smc_cand.direction == confluence.direction:
            chosen_candidate = smc_cand
            strategy_used = StrategyType.SMC

        if chosen_candidate:
            sl = chosen_candidate.stop_loss
            tp1 = chosen_candidate.tp1
            tp2 = chosen_candidate.tp2
            tp3 = chosen_candidate.tp3
            rr = chosen_candidate.risk_reward
            invalidation = [f"Price closes beyond invalidation level {chosen_candidate.invalidation_level}"]
            reasons = list(set(confluence.reasons + chosen_candidate.reasons))
        else:
            # Fallback to ATR-based multi-TF confluence boundaries
            atr_value = self._compute_atr(snapshot, mtf=mtf)
            if confluence.direction == SignalDirection.LONG:
                sl = round(curr_p - (atr_value * 2.0), 2)
                tp1 = round(curr_p + (atr_value * 3.0), 2)
                tp2 = round(curr_p + (atr_value * 5.0), 2)
                tp3 = round(curr_p + (atr_value * 8.0), 2)
                invalidation = [f"Price closes below structural support {sl}"]
            else:
                sl = round(curr_p + (atr_value * 2.0), 2)
                tp1 = round(curr_p - (atr_value * 3.0), 2)
                tp2 = round(curr_p - (atr_value * 5.0), 2)
                tp3 = round(curr_p - (atr_value * 8.0), 2)
                invalidation = [f"Price closes above structural resistance {sl}"]

            risk = abs(curr_p - sl)
            reward = abs(tp2 - curr_p)
            rr = round(reward / risk, 2) if risk > 0 else 2.0
            reasons = confluence.reasons

        # Compute R:R component and adjust final score
        rr_pts, rr_passed, rr_detail = ConfluenceEngine.compute_rr_score(
            entry=curr_p,
            stop_loss=sl,
            take_profit=tp2,
            min_rr=self.settings.MIN_RISK_REWARD,
            max_points=float(self.settings.WEIGHT_RISK_REWARD),
        )
        final_score = round(confluence.total_score + rr_pts, 1)
        final_quality = self.confluence_engine.categorize_quality(final_score)

        # Tradability gate — single source of truth
        if final_quality not in (SignalQuality.STRONG, SignalQuality.VERY_STRONG):
            return self._no_trade_payload(
                snapshot, confluence,
                reason=f"Final score {final_score} ({final_quality.value}) below STRONG threshold.",
            )

        mbias = MarketBias.BULLISH if confluence.direction == SignalDirection.LONG else MarketBias.BEARISH

        # Build the fib level dict for reporting
        fib_levels = {}
        if fib_cand and "fib_ratios" in fib_cand.metadata:
            fib_levels = {str(k): float(v) for k, v in fib_cand.metadata["fib_ratios"].items()}

        payload = SignalPayload(
            instrument=snapshot.symbol,
            direction=confluence.direction,
            strategy=strategy_used,
            timeframe=mtf.trigger.value,
            timestamp=ts,
            entry=curr_p,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            risk_reward=rr,
            confidence_score=final_score,
            signal_quality=final_quality,
            market_bias=mbias,
            strategy_version=derive_strategy_version(self.settings, mtf_name=mtf.name),
            reasons=reasons,
            invalidation_conditions=invalidation,
            detected_structures={"score_breakdown": confluence.breakdown.model_dump()},
            fibonacci_levels=fib_levels,
            liquidity_levels=[],
        )
        payload.explanation = payload.build_explanation()
        return payload

    def _no_trade_payload(self, snapshot: MultiTimeframeSnapshot, confluence, reason: str = "", mtf=None) -> SignalPayload:
        from app.core.mtf_config import resolve_mtf
        mtf = resolve_mtf(mtf)
        curr_p = snapshot.current_price
        payload = SignalPayload(
            instrument=snapshot.symbol,
            direction=SignalDirection.NO_TRADE,
            strategy=StrategyType.CONFLUENCE,
            timeframe=mtf.trigger.value,
            timestamp=snapshot.timestamp,
            entry=curr_p,
            stop_loss=curr_p,
            take_profit_1=curr_p,
            take_profit_2=curr_p,
            take_profit_3=curr_p,
            risk_reward=0.0,
            confidence_score=confluence.total_score,
            signal_quality=confluence.quality,
            market_bias=MarketBias.NEUTRAL,
            strategy_version=derive_strategy_version(self.settings, mtf_name=mtf.name),
            reasons=confluence.conflicts if confluence.conflicts else [reason or "Insufficient confluence score for execution."],
            invalidation_conditions=[],
            detected_structures={"score_breakdown": confluence.breakdown.model_dump()},
            fibonacci_levels={},
            liquidity_levels=[],
        )
        payload.explanation = payload.build_explanation()
        return payload

    def _compute_atr(self, snapshot: MultiTimeframeSnapshot, mtf=None) -> float:
        """Compute ATR from the trigger timeframe candle slice, falling back to defaults."""
        from app.core.mtf_config import resolve_mtf
        mtf = resolve_mtf(mtf)
        candles = snapshot.get_series(mtf.trigger)
        if len(candles) < 2:
            return self.settings.ATR_FALLBACK

        atr_values = calculate_atr(candles, period=self.settings.ATR_PERIOD)
        recent = [v for v in atr_values if v > 0.0]
        if not recent:
            return self.settings.ATR_FALLBACK

        raw = recent[-1]
        if raw <= 0.0:
            return self.settings.ATR_FALLBACK
        return round(raw, 2)