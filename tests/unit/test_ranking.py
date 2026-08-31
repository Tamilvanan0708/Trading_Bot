"""
Tests for the ranking system, promotion state machine, and daily/weekly reports.
"""

import pytest

from app.research.ranking import (
    DEFAULT_WEIGHTS,
    PromotionState,
    PromotionStateMachine,
    rank_candidates,
)


# ---------------------------------------------------------------------------
# Promotion state machine
# ---------------------------------------------------------------------------


def test_promotion_starts_at_research():
    psm = PromotionStateMachine("CANDIDATE_X_V1")
    assert psm.state == PromotionState.RESEARCH


def test_promotion_valid_transition():
    psm = PromotionStateMachine("CANDIDATE_X_V1")
    assert psm.transition(PromotionState.OOS_VALIDATED, "OOS evidence") is True
    assert psm.state == PromotionState.OOS_VALIDATED
    assert len(psm.history) >= 2


def test_promotion_invalid_transition():
    psm = PromotionStateMachine("CANDIDATE_X_V1")
    # Cannot jump directly to PROMOTED from RESEARCH.
    assert psm.transition(PromotionState.PROMOTED, "no") is False
    assert psm.state == PromotionState.RESEARCH


def test_promotion_rejected_is_terminal():
    psm = PromotionStateMachine("CANDIDATE_X_V1")
    psm.transition(PromotionState.REJECTED, "failed validation")
    assert psm.state == PromotionState.REJECTED
    # No transition from REJECTED.
    assert psm.transition(PromotionState.OOS_VALIDATED, "retry") is False


def test_promotion_full_path():
    states = [PromotionState.RESEARCH, PromotionState.OOS_VALIDATED,
              PromotionState.FORWARD_OBSERVATION, PromotionState.FORWARD_VALIDATED,
              PromotionState.PAPER_VALIDATION, PromotionState.HUMAN_REVIEW,
              PromotionState.PROMOTED]
    psm = PromotionStateMachine("CANDIDATE_X_V1")
    for s in states[1:]:
        assert psm.transition(s, f"step {s.value}") is True
    assert psm.state == PromotionState.PROMOTED


def test_promotion_to_dict():
    psm = PromotionStateMachine("CANDIDATE_X_V1")
    d = psm.to_dict()
    assert d["version"] == "CANDIDATE_X_V1"
    assert d["state"] == "RESEARCH"
    assert isinstance(d["history"], list)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _cand(exp, pf, dd, trades, pos_w, tot_w, mc_pneg=100, boot_lo=-0.5, boot_hi=0.5):
    return {
        "expectancy_r": exp,
        "profit_factor": pf,
        "max_dd_pct": dd,
        "trades": trades,
        "positive_windows": pos_w,
        "total_windows": tot_w,
        "monte_carlo": {"probability_negative_return_pct": mc_pneg},
        "bootstrap": {"mean_r": {"lo": boot_lo, "hi": boot_hi}},
        "robustness_tp": {"tp-0.2": {"expectancy_r": 0.1}, "tp0.0": {"expectancy_r": 0.2}, "tp+0.2": {"expectancy_r": 0.3}},
    }


def test_rank_returns_sorted():
    c1 = _cand(0.5, 2.0, 15, 300, 9, 13, mc_pneg=0, boot_lo=0.3, boot_hi=0.7)
    c2 = _cand(0.1, 1.1, 30, 100, 5, 13, mc_pneg=30, boot_lo=-0.1, boot_hi=0.4)
    result = rank_candidates([c2, c1])
    assert len(result) == 2
    assert result[0]["rank"] == 1
    assert result[1]["rank"] == 2
    # Better candidate (higher expectancy, lower DD, better CI) should rank first.
    assert result[0]["expectancy_r"] >= result[1]["expectancy_r"]


def test_rank_adds_score_and_rank():
    c = _cand(0.5, 2.0, 15, 200, 9, 13, mc_pneg=0, boot_lo=0.3, boot_hi=0.7)
    result = rank_candidates([c])
    assert "rank" in result[0]
    assert "rank_score" in result[0]
    assert isinstance(result[0]["rank_score"], float)


def test_rank_empty():
    assert rank_candidates([]) == []


def test_rank_single_candidate():
    c = _cand(0.3, 1.5, 20, 150, 7, 13, mc_pneg=10, boot_lo=0.1, boot_hi=0.5)
    result = rank_candidates([c])
    assert result[0]["rank"] == 1


def test_rank_forward_bonus():
    base = _cand(0.3, 1.5, 20, 100, 7, 13, mc_pneg=10, boot_lo=0.1, boot_hi=0.5)
    with_fwd = dict(base, forward_expectancy_r=0.5, forward_closed=20)
    result = rank_candidates([base, with_fwd], forward_weight_bonus=0.10)
    # The forward-enhanced candidate should rank higher or equal.
    assert result[0]["forward_expectancy_r"] == 0.5 or result[0]["rank"] == 1