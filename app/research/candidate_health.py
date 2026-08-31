"""
Candidate Health Monitor (Phase 16).

Assigns each forward-observation candidate a statistically defined health
state:

  GREEN  — sufficient sample AND forward expectancy statistically positive
           (bootstrap 95% CI lower bound > 0)
  YELLOW — insufficient data, CI crosses zero, or early degradation vs OOS
  RED    — statistically significant deterioration (upper CI bound < 0, or
           severe degradation vs the cost-adjusted OOS reference)

A single loss never marks RED.  Minimum sample sizes and confidence
intervals gate every decision.
"""

from dataclasses import dataclass

from app.research.bootstrap import bootstrap_confidence_intervals


@dataclass(frozen=True)
class HealthResult:
    version: str
    state: str                 # GREEN | YELLOW | RED
    closed_signals: int
    forward_expectancy: float | None
    ci_lo: float | None
    ci_hi: float | None
    oos_reference: float | None
    degradation_vs_oos: float | None
    notes: list[str]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "state": self.state,
            "closed_signals": self.closed_signals,
            "forward_expectancy": self.forward_expectancy,
            "ci_lo": self.ci_lo,
            "ci_hi": self.ci_hi,
            "oos_reference": self.oos_reference,
            "degradation_vs_oos": self.degradation_vs_oos,
            "notes": self.notes,
        }


def _forward_rs(version: str, signals) -> list[float]:
    """Collects final-R values for closed signals of a candidate version."""
    rs = []
    for s in signals:
        if s.strategy_version == version and s.outcome and s.outcome != "OPEN" and s.final_r is not None:
            rs.append(s.final_r)
    return rs


def _oos_reference(version: str) -> float | None:
    """Cost-adjusted OOS reference for a candidate (1x cost), if available."""
    try:
        import json
        import os
        path = "reports/cost_robustness.json"
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        cand = data.get("candidates", {}).get(version, {})
        sweep = cand.get("cost_sweep", {})
        exp_1x = (sweep.get("1x") or {}).get("expectancy_r")
        return exp_1x if exp_1x is not None else None
    except Exception:  # noqa: BLE001
        return None


MIN_SAMPLE_YELLOW = 10    # below this, health is informational only
MIN_SAMPLE_GREEN = 30     # preferred forward sample for GREEN


def assess_candidate_health(version: str, signals, oos_reference: float | None = None) -> HealthResult:
    """Assesses one candidate's forward health using bootstrap CIs.

    ``signals`` is an iterable of signal rows (must expose ``strategy_version``,
    ``outcome``, ``final_r``).
    """
    rs = _forward_rs(version, signals)
    n = len(rs)
    ref = oos_reference if oos_reference is not None else _oos_reference(version)
    notes: list[str] = []

    if n < MIN_SAMPLE_YELLOW:
        return HealthResult(
            version=version, state="YELLOW", closed_signals=n,
            forward_expectancy=sum(rs) / n if n else None,
            ci_lo=None, ci_hi=None, oos_reference=ref,
            degradation_vs_oos=None,
            notes=[f"Insufficient forward sample ({n} closed < {MIN_SAMPLE_YELLOW}); health is informational only."],
        )

    exp = sum(rs) / n
    try:
        boot = bootstrap_confidence_intervals(rs)
        mr = boot.get("mean_r", {})
        lo = mr.get("lo")
        hi = mr.get("hi")
    except Exception:  # noqa: BLE001
        lo = hi = None

    degradation = None
    if ref is not None and ref > 0:
        degradation = round(exp - ref, 3)
        if exp < 0 and ref > 0:
            notes.append(f"Forward expectancy {exp:+.3f}R vs cost-adjusted OOS {ref:+.3f}R — degradation detected.")

    # RED: statistically significant deterioration
    if hi is not None and hi < 0:
        return HealthResult(version, "RED", n, exp, lo, hi, ref, degradation,
                            notes + [f"Bootstrap 95% CI upper bound {hi:.3f} < 0 — statistically negative forward edge."])
    # GREEN: sufficient sample and CI strictly positive
    if lo is not None and lo > 0 and n >= MIN_SAMPLE_GREEN:
        return HealthResult(version, "GREEN", n, exp, lo, hi, ref, degradation,
                            notes + [f"Bootstrap 95% CI [{lo:.3f}, {hi:.3f}] strictly positive with {n} closed signals."])
    # YELLOW: everything else (insufficient data or CI crosses zero)
    if lo is not None and hi is not None:
        notes.append(f"Bootstrap 95% CI [{lo:.3f}, {hi:.3f}] crosses zero — evidence not conclusive.")
    return HealthResult(version, "YELLOW", n, exp, lo, hi, ref, degradation, notes)


def assess_all_candidates(candidate_versions, signals) -> dict[str, HealthResult]:
    """Assesses all candidate versions.  Returns {version: HealthResult}."""
    return {v: assess_candidate_health(v, signals) for v in candidate_versions}
