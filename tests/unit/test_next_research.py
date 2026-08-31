"""
Tests for the next research/validation layer:
- candidate health monitor (GREEN/YELLOW/RED, statistically gated)
- deterministic signal quality score
- new research endpoints (ranking / health / daily-opportunity)
"""

import pytest

from app.research.candidate_health import assess_candidate_health
from app.research.quality_score import classify, compute_quality_score


class _Sig:
    def __init__(self, version, outcome, final_r):
        self.strategy_version = version
        self.outcome = outcome
        self.final_r = final_r


# ---------------------------------------------------------------------------
# Candidate Health Monitor
# ---------------------------------------------------------------------------


def test_health_insufficient_sample_is_yellow():
    sigs = [_Sig("CANDIDATE_X_V1", "TP1", 1.0), _Sig("CANDIDATE_X_V1", "SL", -1.0)]
    h = assess_candidate_health("CANDIDATE_X_V1", sigs)
    assert h.state == "YELLOW"
    assert h.closed_signals == 2


def test_health_red_when_ci_negative():
    # 12 losses => strongly negative; must be RED (not just one loss)
    sigs = [_Sig("CANDIDATE_X_V1", "SL", -1.0) for _ in range(12)]
    h = assess_candidate_health("CANDIDATE_X_V1", sigs)
    assert h.state == "RED"
    assert h.forward_expectancy < 0


def test_health_not_red_on_single_loss():
    sigs = [_Sig("CANDIDATE_X_V1", "SL", -1.0)]
    h = assess_candidate_health("CANDIDATE_X_V1", sigs)
    assert h.state == "YELLOW"  # informational only


def test_health_green_with_positive_sample():
    # 30 wins of +1R => CI strictly positive with sample >= 30
    sigs = [_Sig("CANDIDATE_X_V1", "TP1", 1.0) for _ in range(32)]
    h = assess_candidate_health("CANDIDATE_X_V1", sigs)
    assert h.state == "GREEN"
    assert h.ci_lo is not None and h.ci_lo > 0


def test_health_to_dict_shape():
    sigs = [_Sig("CANDIDATE_X_V1", "SL", -1.0)]
    d = assess_candidate_health("CANDIDATE_X_V1", sigs).to_dict()
    for k in ("version", "state", "closed_signals", "notes"):
        assert k in d


# ---------------------------------------------------------------------------
# Signal Quality Score
# ---------------------------------------------------------------------------


def test_quality_score_strong():
    sig = {
        "direction": "LONG", "market_bias": "BULLISH", "confidence_score": 88,
        "risk_reward": 1.75, "signal_quality": "STRONG", "regime": "RANGING",
        "detected_structures": {"score_breakdown": {
            "smc_confirmation": {"points_awarded": 20, "max_points": 20},
            "fib_confirmation": {"points_awarded": 15, "max_points": 15},
        }},
    }
    r = compute_quality_score(sig)
    assert r.classification in ("STRONG", "GOOD")
    assert 60 <= r.score <= 100


def test_quality_score_weak_for_no_trade():
    sig = {"direction": "NO_TRADE", "confidence_score": 10, "signal_quality": "NO_TRADE"}
    r = compute_quality_score(sig)
    assert r.classification in ("WEAK", "INVALID")
    assert r.score < 40


def test_quality_score_missing_fields_graceful():
    r = compute_quality_score({})
    assert 0 <= r.score <= 100
    assert r.classification in ("STRONG", "GOOD", "NEUTRAL", "WEAK", "INVALID")


def test_quality_classify_bands():
    assert classify(85) == "STRONG"
    assert classify(70) == "GOOD"
    assert classify(55) == "NEUTRAL"
    assert classify(35) == "WEAK"
    assert classify(20) == "INVALID"


# ---------------------------------------------------------------------------
# Research endpoints
# ---------------------------------------------------------------------------


def test_research_ranking_endpoint():
    from fastapi.testclient import TestClient
    from app.api.app import create_app
    c = TestClient(create_app())
    r = c.get("/research/ranking")
    assert r.status_code == 200
    body = r.json()
    assert "ranked_candidates" in body
    # Ranking must include cost sweep data when available
    if body["ranked_candidates"]:
        assert "rank_score" in body["ranked_candidates"][0]


def test_research_health_endpoint():
    from fastapi.testclient import TestClient
    from app.api.app import create_app
    c = TestClient(create_app())
    r = c.get("/research/health")
    assert r.status_code == 200
    body = r.json()
    assert "candidates" in body
    # Each candidate has a health state
    for ver, h in body["candidates"].items():
        assert h["state"] in ("GREEN", "YELLOW", "RED")


def test_research_daily_opportunity_endpoint():
    from fastapi.testclient import TestClient
    from app.api.app import create_app
    c = TestClient(create_app())
    r = c.get("/research/daily-opportunity")
    assert r.status_code == 200
    body = r.json()
    assert "conclusion" in body
    assert body["conclusion"] in ("STATISTICALLY SUPPORTED", "NOT STATISTICALLY SUPPORTED")
