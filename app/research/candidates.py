"""
Forward-observation candidate definitions and single-candle evaluator.

The three research candidates (A/B/C) are frozen configurations.  They are
evaluated INDEPENDENTLY and side-by-side in observation mode.  Their
parameters are FROZEN: TP, SL, confluence threshold, regime filter and
timeframes must not be changed while forward observation runs.  Improvements
must go through a new version (e.g. CANDIDATE_B_V2) validated historically
first.

Each candidate stores its own ``strategy_version`` so outcomes are never
mixed.
"""

from dataclasses import dataclass

from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.core.mtf_config import (
    MTF_4H_1H_15M_5M,
    MTF_4H_1H_30M_5M,
    PROD_4H_1H_30M_15M,
    MTFConfig,
)
from app.data.models import MultiTimeframeSnapshot
from app.signals.engine import SignalEngine


@dataclass(frozen=True)
class ForwardCandidate:
    """A frozen forward-observation candidate configuration."""

    name: str                       # "CANDIDATE_A"
    version: str                    # "CANDIDATE_A_V1"
    mtf: MTFConfig
    regime: str | None = "RANGING"  # regime filter (None = all)
    conf_threshold: float = 75.0    # confluence score threshold (incl. R:R)
    tp_r: float = 1.75              # target in R
    sl_atr: float = 1.5             # stop in ATR multiples

    @property
    def label(self) -> str:
        return f"{self.name} ({self.mtf.label}) TP{self.tp_r}R SL{self.sl_atr}ATR conf>={self.conf_threshold}"
# The three frozen candidates selected by MTF research (pooled OOS + bootstrap).
CANDIDATE_A = ForwardCandidate(
    name="CANDIDATE_A", version="CANDIDATE_A_V1",
    mtf=PROD_4H_1H_30M_15M,   # 4H -> 1H -> 30M -> 15M
    regime="RANGING", conf_threshold=75.0, tp_r=1.75, sl_atr=1.5,
)

CANDIDATE_B = ForwardCandidate(
    name="CANDIDATE_B", version="CANDIDATE_B_V1",
    mtf=MTF_4H_1H_30M_5M,     # 4H -> 1H -> 30M -> 5M
    regime="RANGING", conf_threshold=85.0, tp_r=1.75, sl_atr=1.5,
)

CANDIDATE_C = ForwardCandidate(
    name="CANDIDATE_C", version="CANDIDATE_C_V1",
    mtf=MTF_4H_1H_15M_5M,     # 4H -> 1H -> 15M -> 5M
    regime="RANGING", conf_threshold=85.0, tp_r=2.0, sl_atr=1.5,
)

ALL_FORWARD_CANDIDATES = [CANDIDATE_A, CANDIDATE_B, CANDIDATE_C]


def candidate_by_version(version: str) -> ForwardCandidate | None:
    for c in ALL_FORWARD_CANDIDATES:
        if c.version == version:
            return c
    return None


def resolve_candidate_status(version: str, forward_signals: int = 0) -> str:
    """Derives the promotion status of a candidate from available evidence.

    Priority: REJECTED (persisted) > FORWARD_OBSERVATION (has signals) >
    OOS_VALIDATED (OOS reference exists) > RESEARCH.

    This is the read-only status; the PromotionStateMachine tracks
    transitions and human decisions separately.
    """
    from app.research.ranking import PromotionState

    # Persisted rejection / manual decision overrides.
    try:
        import os
        path = "data/research/candidate_status.json"
        if os.path.exists(path):
            import json
            with open(path, encoding="utf-8") as f:
                status_map = json.load(f)
            if version in status_map and status_map[version].get("state"):
                state = status_map[version]["state"]
                if state in (PromotionState.REJECTED.value, PromotionState.PROMOTED.value,
                             PromotionState.HUMAN_REVIEW.value, PromotionState.PAPER_VALIDATION.value,
                             PromotionState.FORWARD_VALIDATED.value):
                    return state
    except Exception:  # noqa: BLE001
        pass

    if forward_signals > 0:
        return PromotionState.FORWARD_OBSERVATION.value
    return PromotionState.OOS_VALIDATED.value if _has_oos_reference(version) else PromotionState.RESEARCH.value


def _has_oos_reference(version: str) -> bool:
    import os
    path = "data/research/mtf_report.json"
    if not os.path.exists(path):
        return False
    mapping = {
        "CANDIDATE_A_V1": ("PROD_4H_1H_30M_15M", "RANGING", 75),
        "CANDIDATE_B_V1": ("4H_1H_30M_5M", "RANGING", 85),
        "CANDIDATE_C_V1": ("4H_1H_15M_5M", "RANGING", 85),
    }
    try:
        import json
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        cfg, regime, conf = mapping[version]
        for cand in report.get("candidates", []):
            if cand.get("config") == cfg and cand.get("regime") == regime and cand.get("conf") == conf:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def evaluate_candidate(
    snapshot: MultiTimeframeSnapshot,
    candidate: ForwardCandidate,
    engine=None,
    regime_detector=None,
) -> object | None:
    """Evaluate one CLOSED candle snapshot for a candidate.

    Returns a ``SignalPayload`` (tradable LONG/SHORT) or ``None`` when the
    candidate has no setup on this candle.

    Mirrors the research fast-path exactly: confluence engine with the
    candidate's MTF roles, ATR-geometry SL/TP, R:R score added, confluence
    threshold applied, regime filter applied.  No future information enters.
    """
    from app.confluence.engine import ConfluenceEngine
    from app.market_regime.detector import MarketRegimeDetector
    from app.signals.models import SignalPayload

    engine = engine or SignalEngine()
    regime_detector = regime_detector or MarketRegimeDetector(atr_period=engine.settings.ATR_PERIOD)

    # Regime filter
    regime = regime_detector.analyze(snapshot.m15).regime.value
    if candidate.regime is not None and regime != candidate.regime:
        return None

    confluence = engine.confluence_engine.evaluate(snapshot, mtf=candidate.mtf)
    if confluence.direction == SignalDirection.NO_TRADE or len(confluence.conflicts) > 0:
        return None

    curr_p = snapshot.current_price
    trigger = snapshot.get_series(candidate.mtf.trigger)
    atr_value = _atr(trigger, engine.settings.ATR_PERIOD, engine.settings.ATR_FALLBACK)
    atr_value = max(atr_value, 0.01)
    risk = max(0.01, atr_value * candidate.sl_atr)

    if confluence.direction == SignalDirection.LONG:
        sl = round(curr_p - risk, 2)
        tp1 = round(curr_p + candidate.tp_r * risk, 2)
        tp2 = tp1
        tp3 = round(curr_p + 1.5 * candidate.tp_r * risk, 2)
    else:
        sl = round(curr_p + risk, 2)
        tp1 = round(curr_p - candidate.tp_r * risk, 2)
        tp2 = tp1
        tp3 = round(curr_p - 1.5 * candidate.tp_r * risk, 2)

    rr_pts, _ok, _det = ConfluenceEngine.compute_rr_score(
        entry=curr_p, stop_loss=sl, take_profit=tp1,
        min_rr=engine.settings.MIN_RISK_REWARD,
        max_points=float(engine.settings.WEIGHT_RISK_REWARD),
    )
    final_score = round(confluence.total_score + rr_pts, 1)
    if final_score < candidate.conf_threshold:
        return None
    quality = engine.confluence_engine.categorize_quality(final_score)
    if quality not in (SignalQuality.STRONG, SignalQuality.VERY_STRONG):
        return None

    payload = SignalPayload(
        instrument=snapshot.symbol,
        direction=confluence.direction,
        strategy=StrategyType.CONFLUENCE,
        timeframe=candidate.mtf.trigger.value,
        timestamp=snapshot.timestamp,
        entry=curr_p,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
        take_profit_3=tp3,
        risk_reward=round(candidate.tp_r, 2),
        confidence_score=final_score,
        signal_quality=quality,
        market_bias=MarketBias.BULLISH if confluence.direction.value == "LONG" else MarketBias.BEARISH,
        strategy_version=candidate.version,
        reasons=[f"{candidate.version} setup: {regime} regime, conf {final_score:.0f}/100",
                 "Confluence: " + (confluence.reasons[0] if confluence.reasons else "aligned")],
        invalidation_conditions=[f"Price closes beyond stop {sl}"],
        detected_structures={"score_breakdown": confluence.breakdown.model_dump()},
        fibonacci_levels={},
        liquidity_levels=[],
    )
    payload.explanation = payload.build_explanation()
    return payload


def _atr(candles, period: int, fallback: float) -> float:
    from app.indicators.atr import calculate_atr
    if len(candles) < 2:
        return fallback
    vals = [v for v in calculate_atr(candles, period=period) if v > 0.0]
    if not vals:
        return fallback
    return round(vals[-1], 2)
