"""
Variant signal engine for candidate testing.

Wraps the deterministic base SignalEngine and applies candidate-specific
SL/TP geometry BEFORE execution.  Used to run controlled candidates through
the real, capital-constrained BacktestEngine so results are portfolio-honest.
"""


from app.core.constants import SignalDirection
from app.data.models import MultiTimeframeSnapshot
from app.signals.models import SignalPayload


class VariantSignalEngine:
    """Applies a candidate exit/SL design on top of the base signal engine."""

    def __init__(
        self,
        base_engine,
        tp_r: float | None = None,      # single TP at N x risk
        sl_atr: float | None = None,    # SL at N x ATR (else base SL)
        min_confidence: float | None = None,
        only_regime: str | None = None,
        regime_detector=None,
    ) -> None:
        self.base = base_engine
        self.tp_r = tp_r
        self.sl_atr = sl_atr
        self.min_confidence = min_confidence
        self.only_regime = only_regime
        self._regime_detector = regime_detector

    def generate_signal(self, snapshot: MultiTimeframeSnapshot) -> SignalPayload:
        sig = self.base.generate_signal(snapshot)
        if not sig.is_tradable or sig.direction == SignalDirection.NO_TRADE:
            return sig
        if self.min_confidence is not None and sig.confidence_score < self.min_confidence:
            return self._no_trade(sig)

        if self.only_regime and self._regime_detector is not None:
            from app.market_regime.detector import MarketRegimeDetector
            det = self._regime_detector or MarketRegimeDetector()
            regime = det.analyze(snapshot.m15).regime.value
            if regime != self.only_regime:
                return self._no_trade(sig)

        entry = sig.entry
        atr = self.base._compute_atr(snapshot)
        atr = max(atr, 0.01)

        if self.sl_atr is not None:
            if sig.direction == SignalDirection.LONG:
                sl = entry - self.sl_atr * atr
            else:
                sl = entry + self.sl_atr * atr
        else:
            sl = sig.stop_loss
        risk = max(0.01, abs(entry - sl))

        if self.tp_r is not None:
            if sig.direction == SignalDirection.LONG:
                tp = entry + self.tp_r * risk
            else:
                tp = entry - self.tp_r * risk
        else:
            tp = sig.take_profit_1

        # Clamp to valid ascending/descending geometry.
        if sig.direction == SignalDirection.LONG:
            sl = min(sl, entry)
            tp = max(tp, entry)
        else:
            sl = max(sl, entry)
            tp = min(tp, entry)

        return sig.model_copy(update={
            "stop_loss": round(sl, 2),
            "take_profit_1": round(tp, 2),
            "take_profit_2": round(tp, 2),
            "take_profit_3": round(tp, 2),
            "risk_reward": round(abs(tp - entry) / risk, 2) if risk > 0 else 0.0,
        })

    @staticmethod
    def _no_trade(sig: SignalPayload) -> SignalPayload:
        return sig.model_copy(update={
            "direction": SignalDirection.NO_TRADE,
            "signal_quality": "NO_TRADE",
        })