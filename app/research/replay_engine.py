"""
Research replay engine.

Collects every deterministic tradable signal ONCE (expensive signal-engine pass),
then replays controlled SL/TP / entry-filter variants over the recorded signals
(cheap).  This decouples signal generation from exit design, making multi-variant
research tractable and keeping the production strategy untouched.

All research uses REAL candles only; no synthetic data is used for conclusions.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.core.constants import SignalDirection, TimeFrame
from app.data.models import Candle, MultiTimeframeSnapshot
from app.data.timeframe_resampler import IncrementalResampler, resample_candles
from app.market_regime.detector import MarketRegimeDetector
from app.research.session import _session_for
from app.signals.engine import SignalEngine


@dataclass
class SignalRecord:
    """A deterministic tradable signal (entry conditions only, no exit yet)."""
    timestamp: datetime
    direction: SignalDirection
    entry: float
    base_sl: float
    base_risk: float
    atr: float
    confidence: float
    market_bias: str
    regime: str
    session: str
    reasons: list[str] = field(default_factory=list)
    # Feature flags parsed from reasons/metadata
    has_bos: bool = False
    has_choch: bool = False
    has_fvg: bool = False
    has_sweep: bool = False
    has_fib: bool = False

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction.value,
            "entry": self.entry,
            "base_sl": self.base_sl,
            "base_risk": self.base_risk,
            "atr": self.atr,
            "confidence": self.confidence,
            "market_bias": self.market_bias,
            "regime": self.regime,
            "session": self.session,
            "has_bos": self.has_bos,
            "has_choch": self.has_choch,
            "has_fvg": self.has_fvg,
            "has_sweep": self.has_sweep,
            "has_fib": self.has_fib,
        }


@dataclass
class TradeOutcome:
    """Replayed exit outcome for one signal under one variant."""
    signal_index: int
    direction: SignalDirection
    entry: float
    exit_price: float
    exit_time: datetime
    exit_reason: str  # STOP_LOSS_HIT | TAKE_PROFIT_HIT | END
    pnl_r: float
    mae_r: float
    mfe_r: float
    holding_hours: float


def _signal_features(reasons: list[str]) -> dict[str, bool]:
    joined = " ".join(reasons).upper()
    return {
        "has_bos": "BOS" in joined,
        "has_choch": "CHOCH" in joined,
        "has_fvg": "FVG" in joined,
        "has_sweep": "SWEPT" in joined or "LIQUIDITY" in joined,
        "has_fib": "FIBONACCI" in joined or "GOLDEN" in joined,
    }


def collect_signals(
    candles: list[Candle],
    engine: SignalEngine | None = None,
    warmup_bars: int = 150,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_signals: int | None = None,
) -> list[SignalRecord]:
    """Walk the candles chronologically and record every tradable signal.

    Only closed candles are used (the snapshot includes bar `i` which has just
    closed).  No future information enters.

    Uses the incremental MTF resampler (near-linear time).  Its output is
    verified against :func:`collect_signals_reference` by unit tests.
    """
    return collect_signals_incremental(
        candles, engine=engine, warmup_bars=warmup_bars,
        start_time=start_time, end_time=end_time, max_signals=max_signals,
    )


def collect_signals_reference(
    candles: list[Candle],
    engine: SignalEngine | None = None,
    warmup_bars: int = 150,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_signals: int | None = None,
) -> list[SignalRecord]:
    """Reference O(n^2) signal collector (kept for determinism testing)."""
    engine = engine or SignalEngine()
    regime_detector = MarketRegimeDetector(atr_period=engine.settings.ATR_PERIOD)
    signals: list[SignalRecord] = []
    n = len(candles)

    for i in range(warmup_bars, n):
        curr_bar = candles[i]
        if start_time is not None and curr_bar.timestamp < start_time:
            continue
        if end_time is not None and curr_bar.timestamp > end_time:
            break

        history_slice = candles[: i + 1]
        m30 = resample_candles(history_slice, TimeFrame.M30)[-100:]
        h1 = resample_candles(history_slice, TimeFrame.H1)[-80:]
        h4 = resample_candles(history_slice, TimeFrame.H4)[-50:]
        snap = MultiTimeframeSnapshot(
            symbol="XAUUSD",
            timestamp=curr_bar.timestamp,
            current_price=curr_bar.close,
            m15=history_slice[-150:],
            m30=m30,
            h1=h1,
            h4=h4,
        )

        signal = engine.generate_signal(snap)
        if not signal.is_tradable or signal.direction == SignalDirection.NO_TRADE:
            continue

        # ATR at this bar (from the signal engine's own computation)
        atr = engine._compute_atr(snap)
        atr = max(atr, 0.01)
        base_risk = max(0.01, abs(signal.entry - signal.stop_loss))

        regime = regime_detector.analyze(snap.m15).regime.value
        features = _signal_features(signal.reasons)

        signals.append(
            SignalRecord(
                timestamp=signal.timestamp or curr_bar.timestamp,
                direction=signal.direction,
                entry=signal.entry,
                base_sl=signal.stop_loss,
                base_risk=base_risk,
                atr=atr,
                confidence=signal.confidence_score,
                market_bias=signal.market_bias.value,
                regime=regime,
                session=_session_for((signal.timestamp or curr_bar.timestamp).hour),
                reasons=list(signal.reasons),
                **features,
            )
        )
        if max_signals and len(signals) >= max_signals:
            break

    return signals


def collect_signals_incremental(
    candles: list[Candle],
    engine: SignalEngine | None = None,
    warmup_bars: int = 150,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_signals: int | None = None,
) -> list[SignalRecord]:
    """O(n) signal collection using incremental MTF resampling.

    Logically identical to ``collect_signals`` (closed candles only, no future
    information); used by research scripts to make full-history scans tractable.
    """
    engine = engine or SignalEngine()
    regime_detector = MarketRegimeDetector(atr_period=engine.settings.ATR_PERIOD)
    signals: list[SignalRecord] = []
    resampler = IncrementalResampler()
    n = len(candles)

    for i in range(n):
        curr_bar = candles[i]
        resampler.add(curr_bar)
        if i < warmup_bars:
            continue
        if start_time is not None and curr_bar.timestamp < start_time:
            continue
        if end_time is not None and curr_bar.timestamp > end_time:
            break

        history_slice = candles[: i + 1]
        m30 = resampler.series(TimeFrame.M30, 100)
        h1 = resampler.series(TimeFrame.H1, 80)
        h4 = resampler.series(TimeFrame.H4, 50)
        snap = MultiTimeframeSnapshot(
            symbol="XAUUSD",
            timestamp=curr_bar.timestamp,
            current_price=curr_bar.close,
            m15=history_slice[-150:],
            m30=m30,
            h1=h1,
            h4=h4,
        )

        signal = engine.generate_signal(snap)
        if not signal.is_tradable or signal.direction == SignalDirection.NO_TRADE:
            continue

        # ATR at this bar (from the signal engine's own computation)
        atr = engine._compute_atr(snap)
        atr = max(atr, 0.01)
        base_risk = max(0.01, abs(signal.entry - signal.stop_loss))

        regime = regime_detector.analyze(snap.m15).regime.value
        features = _signal_features(signal.reasons)

        signals.append(
            SignalRecord(
                timestamp=signal.timestamp or curr_bar.timestamp,
                direction=signal.direction,
                entry=signal.entry,
                base_sl=signal.stop_loss,
                base_risk=base_risk,
                atr=atr,
                confidence=signal.confidence_score,
                market_bias=signal.market_bias.value,
                regime=regime,
                session=_session_for((signal.timestamp or curr_bar.timestamp).hour),
                reasons=list(signal.reasons),
                **features,
            )
        )
        if max_signals and len(signals) >= max_signals:
            break

    return signals


def collect_signals_mtf(
    candles: list[Candle],
    mtf,
    engine: SignalEngine | None = None,
    warmup_bars: int = 300,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_signals: int | None = None,
    fast: bool = True,
) -> list[SignalRecord]:
    """Collect deterministic tradable signals under an arbitrary MTF config.

    ``candles`` must be the BASE-timeframe series for ``mtf.base`` (5M for
    the 5M-trigger configs, 15M for the production config).  Every candle is
    resampled incrementally into the full 5M/15M/30M/1H/4H stack and the
    signal engine is evaluated on each CLOSED base candle with the given MTF
    roles.  Closed candles only — no future information enters.

    ``fast=True`` (research default) uses only the confluence engine with
    ATR-geometry SL/TP, skipping the strategy candidates.  This isolates the
    MTF/exit question being researched; exit geometry is tested separately by
    ``replay_variant``.  ``fast=False`` uses the full production SignalEngine.

    Each recorded SignalRecord carries the config name in its reasons so
    signals from different configs are never confused.
    """
    from app.core.mtf_config import resolve_mtf
    mtf = resolve_mtf(mtf)
    engine = engine or SignalEngine()
    regime_detector = MarketRegimeDetector(atr_period=engine.settings.ATR_PERIOD)
    settings = engine.settings
    signals: list[SignalRecord] = []
    resampler = IncrementalResampler()
    n = len(candles)

    for i in range(n):
        curr_bar = candles[i]
        resampler.add(curr_bar)
        if i < warmup_bars:
            continue
        if start_time is not None and curr_bar.timestamp < start_time:
            continue
        if end_time is not None and curr_bar.timestamp > end_time:
            break

        snap = MultiTimeframeSnapshot(
            symbol="XAUUSD",
            timestamp=curr_bar.timestamp,
            current_price=curr_bar.close,
            m5=resampler.series(TimeFrame.M5, 120),
            m15=resampler.series(TimeFrame.M15, 150),
            m30=resampler.series(TimeFrame.M30, 100),
            h1=resampler.series(TimeFrame.H1, 80),
            h4=resampler.series(TimeFrame.H4, 50),
        )

        if fast:
            signal = _fast_signal(engine, snap, mtf)
        else:
            signal = engine.generate_signal(snap, mtf=mtf)
        if signal is None or not signal.is_tradable or signal.direction == SignalDirection.NO_TRADE:
            continue

        atr = engine._compute_atr(snap, mtf=mtf)
        atr = max(atr, 0.01)
        base_risk = max(0.01, abs(signal.entry - signal.stop_loss))

        regime = regime_detector.analyze(snap.m15).regime.value
        features = _signal_features(signal.reasons)

        signals.append(
            SignalRecord(
                timestamp=signal.timestamp or curr_bar.timestamp,
                direction=signal.direction,
                entry=signal.entry,
                base_sl=signal.stop_loss,
                base_risk=base_risk,
                atr=atr,
                confidence=signal.confidence_score,
                market_bias=signal.market_bias.value,
                regime=regime,
                session=_session_for((signal.timestamp or curr_bar.timestamp).hour),
                reasons=list(signal.reasons) + [f"MTF:{mtf.name}"],
                **features,
            )
        )
        if max_signals and len(signals) >= max_signals:
            break

    return signals


def _fast_signal(engine, snap: MultiTimeframeSnapshot, mtf):
    """Confluence-only research signal (ATR-geometry SL/TP, no strategy candidates).

    Returns a SignalPayload or None.  Uses the same tradability gate and
    R:R scoring as the production engine but skips the strategy candidates.
    """
    from app.confluence.engine import ConfluenceEngine
    from app.core.constants import SignalQuality

    confluence = engine.confluence_engine.evaluate(snap, mtf=mtf)
    curr_p = snap.current_price
    if confluence.direction == SignalDirection.NO_TRADE or len(confluence.conflicts) > 0:
        return None

    atr_value = engine._compute_atr(snap, mtf=mtf)
    atr_value = max(atr_value, 0.01)
    if confluence.direction.value == "LONG":
        sl = round(curr_p - (atr_value * 2.0), 2)
        tp2 = round(curr_p + (atr_value * 5.0), 2)
    else:
        sl = round(curr_p + (atr_value * 2.0), 2)
        tp2 = round(curr_p - (atr_value * 5.0), 2)
    risk = abs(curr_p - sl)
    rr = round(abs(tp2 - curr_p) / risk, 2) if risk > 0 else 2.0

    rr_pts, _rr_ok, _rr_det = ConfluenceEngine.compute_rr_score(
        entry=curr_p, stop_loss=sl, take_profit=tp2,
        min_rr=engine.settings.MIN_RISK_REWARD,
        max_points=float(engine.settings.WEIGHT_RISK_REWARD),
    )
    final_score = round(confluence.total_score + rr_pts, 1)
    quality = engine.confluence_engine.categorize_quality(final_score)
    if quality not in (SignalQuality.STRONG, SignalQuality.VERY_STRONG):
        return None

    from app.config.strategy_version import derive_strategy_version
    from app.core.constants import MarketBias, StrategyType
    from app.signals.models import SignalPayload

    return SignalPayload(
        instrument=snap.symbol,
        direction=confluence.direction,
        strategy=StrategyType.CONFLUENCE,
        timeframe=mtf.trigger.value,
        timestamp=snap.timestamp,
        entry=curr_p,
        stop_loss=sl,
        take_profit_1=round(curr_p + (atr_value * 3.0), 2) if confluence.direction.value == "LONG" else round(curr_p - (atr_value * 3.0), 2),
        take_profit_2=tp2,
        take_profit_3=round(curr_p + (atr_value * 8.0), 2) if confluence.direction.value == "LONG" else round(curr_p - (atr_value * 8.0), 2),
        risk_reward=rr,
        confidence_score=final_score,
        signal_quality=quality,
        market_bias=MarketBias.BULLISH if confluence.direction.value == "LONG" else MarketBias.BEARISH,
        strategy_version=derive_strategy_version(engine.settings),
        reasons=list(confluence.reasons) + [f"ATR-fallback SL/TP ({atr_value} ATR)"],
        invalidation_conditions=[f"Price closes beyond stop {sl}"],
        detected_structures={"score_breakdown": confluence.breakdown.model_dump()},
        fibonacci_levels={},
        liquidity_levels=[],
    )


def replay_variant(
    signals: list[SignalRecord],
    candles: list[Candle],
    sl_atr: float | None = None,   # if set, SL = entry ± sl_atr*ATR
    tp_r: float | None = None,     # if set, TP = entry ± tp_r*risk
    break_even_after_tp: bool = False,
    trail_atr: float | None = None,
    max_holding_bars: int = 96,       # holding window in base bars
    cost_points: float = 0.0,         # spread+slippage+commission in price points
) -> list[TradeOutcome]:
    """Replay every recorded signal under one exit/SL variant.

    SL priority (same candle): SL is checked before TP (conservative).
    Risk is defined by the variant SL (or base SL when sl_atr is None).
    Callers set ``max_holding_bars`` relative to the base timeframe
    (e.g. 24h = 96 x 15M bars = 288 x 5M bars).

    ``cost_points`` models ROUND-TRIP execution cost in price points
    (spread + slippage + commission).  Half is paid at entry, half at exit:
    the effective entry is ``entry ± cost/2``, SL/TP fills are degraded by
    ``cost/2``, and effective risk becomes ``risk + cost``.  An SL exit is
    always -1R by definition (risk includes both half-costs); TP wins shrink
    with cost.  ``cost_points=0`` is byte-identical to the original
    no-cost behaviour (verified by tests).
    """
    outcomes: list[TradeOutcome] = []
    import bisect
    timestamps = [c.timestamp for c in candles]
    cost = max(0.0, cost_points or 0.0)
    half = cost / 2.0

    for si, sig in enumerate(signals):
        # Use bisect for fuzzy timestamp lookup so signals whose timestamp
        # does not exactly match a candle timestamp are not silently dropped.
        idx = bisect.bisect_left(timestamps, sig.timestamp)
        if idx >= len(timestamps):
            continue
        if timestamps[idx] != sig.timestamp:
            idx = bisect.bisect_right(timestamps, sig.timestamp) - 1
        if idx < 0:
            continue
        start_idx = idx + 1  # first candle strictly after signal

        if sl_atr is not None:
            sl = (sig.entry - sl_atr * sig.atr) if sig.direction == SignalDirection.LONG else (sig.entry + sl_atr * sig.atr)
            risk = abs(sig.entry - sl)
        else:
            sl = sig.base_sl
            risk = sig.base_risk

        risk = max(0.01, risk)

        # Single target (TP1-level) for clean research semantics
        target_r = tp_r if tp_r is not None else 2.5
        tp = (sig.entry + target_r * risk) if sig.direction == SignalDirection.LONG else (sig.entry - target_r * risk)

        # Cost-adjusted execution geometry (adverse for the trader).
        if cost > 0:
            entry_eff = sig.entry + half if sig.direction == SignalDirection.LONG else sig.entry - half
            risk_eff = risk + cost
            exit_adj = half  # fills degraded by half the round-trip cost
        else:
            entry_eff = sig.entry
            risk_eff = risk
            exit_adj = 0.0

        mae = 0.0
        mfe = 0.0
        exit_reason = "END"
        exit_price = entry_eff
        exit_time = sig.timestamp
        peak = sig.entry
        sl_after_be = sl

        for j in range(start_idx, min(start_idx + max_holding_bars, len(candles))):
            c = candles[j]
            low, high = c.low, c.high

            # Same-candle SL priority (conservative)
            sl_hit = low <= sl_after_be if sig.direction == SignalDirection.LONG else high >= sl_after_be
            tp_hit = high >= tp if sig.direction == SignalDirection.LONG else low <= tp

            if sig.direction == SignalDirection.LONG:
                fav = high - sig.entry
                adv = sig.entry - low
            else:
                fav = sig.entry - low
                adv = high - sig.entry
            mfe = max(mfe, fav)
            mae = max(mae, adv)
            if sig.direction == SignalDirection.LONG:
                peak = max(peak, high)
            else:
                peak = min(peak, low)

            if sl_hit:
                exit_reason = "STOP_LOSS_HIT"
                exit_price = sl_after_be - exit_adj if sig.direction == SignalDirection.LONG else sl_after_be + exit_adj
                exit_time = c.timestamp
                break
            if tp_hit:
                exit_reason = "TAKE_PROFIT_HIT"
                exit_price = tp - exit_adj if sig.direction == SignalDirection.LONG else tp + exit_adj
                exit_time = c.timestamp
                break

            # Break-even: move SL to entry after reaching 1R
            if break_even_after_tp and mfe >= risk and sl_after_be != sig.entry:
                sl_after_be = sig.entry

            # Trailing stop
            if trail_atr is not None:
                trail_dist = trail_atr * sig.atr
                if sig.direction == SignalDirection.LONG:
                    sl_after_be = max(sl_after_be, peak - trail_dist)
                else:
                    sl_after_be = min(sl_after_be, peak + trail_dist)
        else:
            # exhausted bars without exit — close at last close
            last_close = candles[min(start_idx + max_holding_bars, len(candles)) - 1].close
            exit_price = last_close - exit_adj if sig.direction == SignalDirection.LONG else last_close + exit_adj
            exit_time = candles[min(start_idx + max_holding_bars, len(candles)) - 1].timestamp
            exit_reason = "END"

        if sig.direction == SignalDirection.LONG:
            pnl_r = (exit_price - entry_eff) / risk_eff
        else:
            pnl_r = (entry_eff - exit_price) / risk_eff

        outcomes.append(
            TradeOutcome(
                signal_index=si,
                direction=sig.direction,
                entry=sig.entry,
                exit_price=exit_price,
                exit_time=exit_time,
                exit_reason=exit_reason,
                pnl_r=round(pnl_r, 3),
                mae_r=round(mae / risk_eff, 3),
                mfe_r=round(mfe / risk_eff, 3),
                holding_hours=round((exit_time - sig.timestamp).total_seconds() / 3600.0, 2),
            )
        )

    return outcomes


def outcomes_metrics(outcomes: list[TradeOutcome], initial_balance: float = 10000.0) -> dict:
    """Aggregate replayed outcomes into a compact performance summary (in R)."""
    n = len(outcomes)
    if n == 0:
        return {"trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "expectancy_r": 0.0,
                "avg_r": 0.0, "median_r": 0.0, "max_dd_pct": 0.0, "avg_mfe_r": 0.0, "avg_mae_r": 0.0}

    rs = [o.pnl_r for o in outcomes]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    # Approximate max drawdown % assuming ~1% risk per trade compounded
    balance = initial_balance
    peak = initial_balance
    dd = 0.0
    for r in rs:
        balance *= (1.0 + 0.01 * r)
        peak = max(peak, balance)
        dd = max(dd, (peak - balance) / peak * 100.0)

    sorted_rs = sorted(rs)
    return {
        "trades": n,
        "win_rate_pct": round(len(wins) / n * 100.0, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (round(gross_win, 2) if gross_win > 0 else 0.0),
        "expectancy_r": round(sum(rs) / n, 3),
        "avg_r": round(sum(rs) / n, 3),
        "median_r": round(sorted_rs[n // 2], 3),
        "max_dd_pct": round(dd, 2),
        "avg_mfe_r": round(sum(o.mfe_r for o in outcomes) / n, 3),
        "avg_mae_r": round(sum(o.mae_r for o in outcomes) / n, 3),
        "sl_hit_pct": round(sum(1 for o in outcomes if o.exit_reason == "STOP_LOSS_HIT") / n * 100.0, 2),
        "tp_hit_pct": round(sum(1 for o in outcomes if o.exit_reason == "TAKE_PROFIT_HIT") / n * 100.0, 2),
    }


def daily_opportunity(signals: list[SignalRecord], candles: list[Candle],
                      tp_r: float | None = None, sl_atr: float | None = None,
                      hold_bars: int = 96) -> dict:
    """Daily opportunity report for a candidate signal set.

    Reports signal frequency and the realistic point opportunity per day
    (best winner points per day).  ``points`` are raw price points on the
    signal's instrument.  This is a research metric, NOT a trading target.
    """
    from collections import defaultdict

    if not signals:
        return {}
    outs = replay_variant(signals, candles, tp_r=tp_r, sl_atr=sl_atr, max_holding_bars=hold_bars)
    by_day = defaultdict(list)
    for sig, out in zip(signals, outs):
        by_day[sig.timestamp.date()].append(out)

    days = sorted(by_day)
    if not days:
        return {}
    span_days = max(1, (days[-1] - days[0]).days)
    day_opp_points = []
    for d in days:
        outs_d = by_day[d]
        wins_d = [o for o in outs_d if o.pnl_r > 0 and o.exit_price is not None]
        if wins_d:
            day_opp_points.append(max(abs(o.exit_price - o.entry) for o in wins_d))
        else:
            day_opp_points.append(0.0)
    n = len(day_opp_points)
    return {
        "days_with_signals": len(days),
        "avg_signals_per_day": round(len(signals) / len(days), 2),
        "pct_days_with_setup": round(len(days) / span_days * 100.0, 1),
        "avg_best_day_points": round(sum(day_opp_points) / n, 1),
        "pct_days_ge_30pts": round(sum(1 for p in day_opp_points if p >= 30.0) / n * 100.0, 1),
        "pct_days_ge_50pts": round(sum(1 for p in day_opp_points if p >= 50.0) / n * 100.0, 1),
    }