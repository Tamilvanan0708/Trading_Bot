"""
Strategy classification based on out-of-sample walk-forward results.

Classification is configurable and conservative.  It never changes the live
strategy — it only produces a recommendation for human review.
"""

from dataclasses import dataclass, field


@dataclass
class ClassificationConfig:
    min_positive_windows_pct: float = 60.0
    min_oos_expectancy_r: float = 0.1
    min_oos_profit_factor: float = 1.3
    max_oos_max_drawdown_pct: float = 30.0
    min_oos_trades: int = 10


@dataclass
class ClassificationResult:
    grade: str  # ROBUST | PROMISING | WEAK | FAILED | INCONCLUSIVE
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"grade": self.grade, "reasons": self.reasons}


def classify_strategy(
    window_results: list,
    config: ClassificationConfig = None,
    lookahead_detected: bool = False,
    data_quality_failed: bool = False,
) -> ClassificationResult:
    """Classify the strategy from walk-forward window results.

    Uses POOLED out-of-sample trades (all test windows combined) for the core
    expectancy / profit-factor / drawdown checks — more statistically robust
    than averaging tiny per-window estimates — plus the per-window positive
    ratio as a stability criterion.  Conservative by design: a strategy is not
    upgraded from FAILED without positive pooled OOS evidence.
    """
    cfg = config or ClassificationConfig()
    reasons: list[str] = []

    if lookahead_detected:
        return ClassificationResult("FAILED", ["Look-ahead bias detected — result invalid."])
    if data_quality_failed:
        return ClassificationResult("FAILED", ["Insufficient data quality — validation aborted."])

    if not window_results:
        return ClassificationResult("INCONCLUSIVE", ["No walk-forward windows produced."])

    # Pooled OOS trades across all test windows.
    pooled_trades = []
    for wr in window_results:
        if wr.test_result and wr.test_result.trades:
            pooled_trades.extend(wr.test_result.trades)

    total_windows = 0
    positive_windows = 0
    for wr in window_results:
        tm = wr.test_metrics
        if not tm or tm.get("error") or tm.get("total_trades", 0) == 0:
            continue
        total_windows += 1
        if tm.get("expectancy_r", 0.0) > 0:
            positive_windows += 1

    if total_windows == 0:
        return ClassificationResult("INCONCLUSIVE", ["No valid out-of-sample windows."])

    # Pooled metrics from real OOS trades (not averaged window estimates).
    if pooled_trades:
        from app.research.metrics import compute_extended_metrics
        pooled = compute_extended_metrics(pooled_trades)
        pooled_exp = pooled.get("expectancy_r", 0.0)
        pooled_pf = pooled.get("profit_factor", 0.0)
        pooled_dd = pooled.get("max_drawdown_pct", 0.0)
        total_oos_trades = pooled.get("total_trades", 0)
    else:
        pooled_exp = 0.0
        pooled_pf = 0.0
        pooled_dd = 0.0
        total_oos_trades = 0

    pos_pct = positive_windows / total_windows * 100.0
    reasons.append(f"Positive OOS windows: {positive_windows}/{total_windows} ({pos_pct:.0f}%)")
    reasons.append(f"Pooled OOS expectancy: {pooled_exp:+.2f} R")
    reasons.append(f"Pooled OOS profit factor: {pooled_pf:.2f}")
    reasons.append(f"Pooled OOS max drawdown: {pooled_dd:.1f}%")
    reasons.append(f"Total OOS trades: {total_oos_trades}")

    # FAILED
    if pooled_exp < 0:
        return ClassificationResult("FAILED", reasons + ["Negative pooled OOS expectancy."])
    if pooled_dd > cfg.max_oos_max_drawdown_pct:
        return ClassificationResult("FAILED", reasons + [f"OOS drawdown {pooled_dd:.1f}% too severe."])

    # ROBUST
    robust = (
        pos_pct >= cfg.min_positive_windows_pct
        and pooled_exp >= cfg.min_oos_expectancy_r
        and pooled_pf >= cfg.min_oos_profit_factor
        and total_oos_trades >= cfg.min_oos_trades
    )
    if robust:
        return ClassificationResult("ROBUST", reasons + ["All robustness criteria met."])

    # PROMISING
    if pooled_exp > 0 and pos_pct >= 40 and total_oos_trades >= cfg.min_oos_trades // 2:
        return ClassificationResult("PROMISING", reasons + ["Positive OOS but some unstable windows / sample size."])

    if pooled_exp > 0:
        return ClassificationResult("WEAK", reasons + ["Positive expectancy but inconsistent / low sample."])

    return ClassificationResult("INCONCLUSIVE", reasons)