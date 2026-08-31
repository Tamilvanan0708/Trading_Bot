"""
Candidate ranking and promotion state machine.

Ranking uses a balanced, risk-adjusted score (NOT win rate primarily).
Promotion follows a strict state machine; no automatic production promotion
exists and human approval is mandatory for PROMOTED.
"""

from dataclasses import dataclass, field
from enum import Enum


class PromotionState(str, Enum):
    RESEARCH = "RESEARCH"
    OOS_VALIDATED = "OOS_VALIDATED"
    FORWARD_OBSERVATION = "FORWARD_OBSERVATION"
    FORWARD_VALIDATED = "FORWARD_VALIDATED"
    PAPER_VALIDATION = "PAPER_VALIDATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


# Allowed transitions; any other move is invalid (immutable history).
_TRANSITIONS = {
    PromotionState.RESEARCH: {PromotionState.OOS_VALIDATED, PromotionState.REJECTED, PromotionState.FORWARD_OBSERVATION},
    PromotionState.OOS_VALIDATED: {PromotionState.FORWARD_OBSERVATION, PromotionState.REJECTED},
    PromotionState.FORWARD_OBSERVATION: {PromotionState.FORWARD_VALIDATED, PromotionState.REJECTED, PromotionState.RESEARCH},
    PromotionState.FORWARD_VALIDATED: {PromotionState.PAPER_VALIDATION, PromotionState.REJECTED},
    PromotionState.PAPER_VALIDATION: {PromotionState.HUMAN_REVIEW, PromotionState.REJECTED},
    PromotionState.HUMAN_REVIEW: {PromotionState.PROMOTED, PromotionState.REJECTED},
    PromotionState.PROMOTED: set(),
    PromotionState.REJECTED: set(),
}


class PromotionStateMachine:
    """Strict promotion lifecycle.  Promotion is never automatic."""

    def __init__(self, version: str, initial: PromotionState = PromotionState.RESEARCH):
        self.version = version
        self.state = initial
        self.history: list[tuple[str, str]] = []

    def transition(self, to: PromotionState, reason: str) -> bool:
        """Attempt a state transition.  Returns False (no-op) if invalid."""
        if to not in _TRANSITIONS[self.state]:
            return False
        self.history.append((self.state.value, to.value))
        self.state = to
        self.history.append((f"REASON:{reason}", to.value))
        return True

    def can(self, to: PromotionState) -> bool:
        return to in _TRANSITIONS[self.state]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "state": self.state.value,
            "history": self.history,
        }


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "oos_expectancy": 0.25,
    "profit_factor": 0.20,
    "drawdown": 0.15,          # inverted (lower is better)
    "oos_window_consistency": 0.10,
    "robustness": 0.10,
    "bootstrap_ci": 0.10,
    "sample_size": 0.05,
    "monte_carlo": 0.05,
}

def _norm(x, lo, hi):
    """Normalizes x into [0,1] across the observed range [lo, hi]."""
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def rank_candidates(
    candidates: list[dict],
    weights: dict | None = None,
    forward_weight_bonus: float = 0.0,
) -> list[dict]:
    """Ranks candidates by a balanced risk-adjusted score.

    Each candidate dict must include at least: ``expectancy_r``, ``profit_factor``,
    ``max_dd_pct``, ``positive_windows``/``total_windows`` (OOS consistency),
    ``trades`` (sample).  Optional: ``bootstrap`` (mean_r.ci or hi/lo),
    ``monte_carlo`` (probability_negative_return_pct), ``robustness_tp``,
    ``forward_expectancy_r``, ``forward_closed``.

    Returns the candidates sorted descending by score, each augmented with
    ``rank`` and ``rank_score``.  A candidate with a strong forward sample may
    receive a bonus when ``forward_weight_bonus > 0``.
    """
    if not candidates:
        return []
    w = {**(weights or DEFAULT_WEIGHTS)}

    exps = []
    for c in candidates:
        # Prefer cost-adjusted expectancy (realistic edge) when available.
        sweep = c.get("cost_sweep") or {}
        cae = (sweep.get("1x") or {}).get("expectancy_r") if sweep else None
        exps.append(cae if cae is not None else (c.get("expectancy_r", 0) or 0))
    pfs = [c.get("profit_factor", 0) or 0 for c in candidates]
    dds = [c.get("max_dd_pct", 0) or 0 for c in candidates]
    sizes = [c.get("trades", 0) or 0 for c in candidates]
    consistency = []
    for c in candidates:
        tot = c.get("total_windows", 0) or c.get("oos_total_windows", 0)
        pos = c.get("positive_windows", 0) or c.get("oos_windows_positive_count", 0)
        consistency.append(pos / tot if tot else 0.0)
    mc_pneg = [c.get("monte_carlo", {}).get("probability_negative_return_pct", 100) or 100 for c in candidates]
    boot_lo = []
    boot_hi = []
    for c in candidates:
        boot = c.get("bootstrap", {}) or {}
        mr = boot.get("mean_r", {}) or {}
        lo = mr.get("lo")
        hi = mr.get("hi")
        if lo is None or hi is None:
            ci = boot.get("ci") or boot.get("confidence_interval")
            if isinstance(ci, (list, tuple)) and len(ci) == 2:
                lo, hi = ci
        boot_lo.append(lo)
        boot_hi.append(hi)

    scored = []
    for i, c in enumerate(candidates):
        exp = exps[i]
        pf = pfs[i]
        dd = dds[i]
        # Risk-adjusted: penalty when CI crosses zero
        lo, hi = boot_lo[i], boot_hi[i]
        if lo is not None and hi is not None:
            if lo > 0:
                ci_positive = 1.0
            elif hi < 0:
                ci_positive = 0.0
            elif hi != lo:
                ci_positive = max(0.0, min(1.0, hi / (hi - lo)))
            else:
                ci_positive = 0.0
        else:
            ci_positive = 0.0
        mc_ok = 1.0 - _norm(mc_pneg[i], 0, 100)  # 100% Pneg -> 0, 0% -> 1
        cost_rob = cost_robustness_score(c)
        score = (
            w.get("oos_expectancy", 0.25) * _norm(exp, min(exps), max(exps))
            + w.get("profit_factor", 0.20) * _norm(pf, 0, max(pfs) if max(pfs) > 0 else 1)
            + w.get("drawdown", 0.15) * (1.0 - _norm(dd, min(dds), max(dds)))
            + w.get("oos_window_consistency", 0.10) * consistency[i]
            + w.get("bootstrap_ci", 0.10) * ci_positive
            + w.get("monte_carlo", 0.05) * mc_ok
            + w.get("sample_size", 0.05) * _norm(sizes[i], 0, max(sizes))
            + w.get("robustness", 0.10) * (0.5 * _robustness_score(c) + 0.5 * cost_rob)
        )
        fwd_exp = c.get("forward_expectancy_r")
        fwd_n = c.get("forward_closed", 0) or 0
        if forward_weight_bonus > 0 and fwd_exp is not None and fwd_n >= 10:
            score += forward_weight_bonus * _norm(fwd_exp, -1.0, 1.0)
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = []
    for rank, (score, c) in enumerate(scored, 1):
        out = dict(c)
        out["rank"] = rank
        out["rank_score"] = round(score, 4)
        result.append(out)
    return result


def _robustness_score(c: dict) -> float:
    """Scores parameter robustness (TP perturbation stability)."""
    pert = c.get("robustness_tp") or {}
    vals = []
    for key in ("tp-0.2", "tp+0.2", "tp0.0", "tp-0.1", "tp+0.1"):
        if key in pert:
            v = pert[key]
            if isinstance(v, dict):
                vals.append(v.get("expectancy_r") or 0)
            else:
                vals.append(v or 0)
    if not vals:
        return 0.5
    # All variants positive => robust; more negative => fragile
    frac_positive = sum(1 for v in vals if v > 0) / len(vals)
    return frac_positive


def cost_robustness_score(c: dict) -> float:
    """Scores execution-cost robustness from a cost sweep.

    Expects ``cost_sweep`` = {label: {expectancy_r: float}} at labels
    '0x', '1x', '1.5x', '2x', '3x'.  A candidate that remains positive at
    1x scores 0.5, at 2x scores 1.0, at 1x negative scores 0.0.
    """
    sweep = c.get("cost_sweep") or {}
    if not sweep:
        return 0.0
    try:
        exp_1x = sweep.get("1x", {}).get("expectancy_r")
        exp_2x = sweep.get("2x", {}).get("expectancy_r")
        if exp_1x is None:
            return 0.0
        if exp_1x <= 0:
            return 0.0
        if exp_2x is not None and exp_2x > 0:
            return 1.0
        return 0.5
    except Exception:  # noqa: BLE001
        return 0.0
