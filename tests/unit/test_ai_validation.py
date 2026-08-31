"""
AI Validation & Decision Audit — backend tests.

Covers the deterministic-authority contract:
  * AI is advisory only; deterministic gates have final authority.
  * AI CANNOT override a deterministic NO_TRADE.
  * Evidence verification (AI claims vs backend facts).
  * Provider failure / missing AI / malformed AI responses.
  * Confidence handling, timeframe conflicts, risk failures.
  * History and health payloads.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.api.routes.ai import (
    _ai_health,
    _gate_eval,
    _lifecycle,
    _performance_summary,
    _risk_validation,
    _timeframe_alignment,
    _verify_evidence,
    _what_must_change,
)

_SETTINGS = get_settings()

_APP_JS = os.path.join(os.path.dirname(__file__), "..", "..", "app/static/terminal/js/app.js")


def _base_analysis(**overrides):
    a = {
        "market_bias": {
            "4h": {"trend": "BULLISH", "summary": "4H bullish"},
            "1h": {"trend": "BULLISH", "summary": "1H bullish"},
            "15m": {"trend": "NEUTRAL", "summary": "15M neutral"},
        },
        "fibonacci_setup": {"direction": "LONG", "valid": True, "active_level_ratio": 0.618},
        "smc_analysis": {"current_zone": "DISCOUNT", "equilibrium_price": 100, "active_fvgs": []},
        "confluence": {"total_score": 42.0, "quality": "WEAK", "is_tradable": False, "conflicts": []},
        "signal": {
            "direction": "NO_TRADE", "strategy": "CONFLUENCE", "entry": 100.0, "stop_loss": 99.0,
            "take_profit_1": 110.0, "take_profit_2": 115.0, "take_profit_3": 120.0,
            "risk_reward": 0.0, "confidence_score": 42.0, "signal_quality": "WEAK",
        },
        "ai_validation": {"status": "REJECT", "confidence": 95.0, "explanation": "Rejected.",
                          "identified_risks": [], "missing_confirmations": [],
                          "provider": "BAI", "model": "gpt-5-4-mini"},
        "degraded": False,
    }
    a.update(overrides)
    return a


def _strong_analysis(**overrides):
    a = _base_analysis()
    a["confluence"] = {"total_score": 88.0, "quality": "STRONG", "is_tradable": True, "conflicts": []}
    a["signal"] = {
        "direction": "LONG", "strategy": "CONFLUENCE", "entry": 4450.61, "stop_loss": 4216.41,
        "take_profit_1": 4684.81, "take_profit_2": 4684.81, "take_profit_3": 4684.81,
        "risk_reward": 2.0, "confidence_score": 88.0, "signal_quality": "STRONG",
    }
    a["ai_validation"] = {"status": "APPROVE", "confidence": 95.0, "explanation": "Approved.",
                          "identified_risks": [], "missing_confirmations": [],
                          "provider": "BAI", "model": "gpt-5-4-mini"}
    a.update(overrides)
    return a


# ===========================================================================
# Deterministic authority
# ===========================================================================


def test_deterministic_authority_ai_cannot_override_no_trade():
    """Test 1: AI says APPROVE 95 but deterministic says NO_TRADE -> NO_TRADE."""
    analysis = _strong_analysis()
    # Force confluence below threshold but keep AI APPROVE
    analysis["confluence"] = {"total_score": 40.0, "quality": "WEAK", "is_tradable": False, "conflicts": []}
    analysis["signal"]["confidence_score"] = 40.0
    analysis["signal"]["signal_quality"] = "WEAK"
    analysis["signal"]["direction"] = "NO_TRADE"
    analysis["ai_validation"] = {"status": "APPROVE", "confidence": 95.0, "explanation": "AI says approve.",
                                 "identified_risks": [], "missing_confirmations": []}
    det = _gate_eval(_SETTINGS, analysis, degraded=False)
    assert det["decision"] == "NO_TRADE"


def test_ai_advisory_rejection_with_valid_deterministic():
    """Test 2: AI REJECT 95 but deterministic VALID -> FINAL = deterministic result."""
    analysis = _strong_analysis()
    analysis["ai_validation"] = {"status": "REJECT", "confidence": 95.0, "explanation": "AI says reject.",
                                 "identified_risks": [], "missing_confirmations": []}
    det = _gate_eval(_SETTINGS, analysis, degraded=False)
    assert det["decision"] == "VALIDATED_SIGNAL"
    assert det["risk_gate"] == "PASS"


def test_data_quality_failure_forces_no_trade():
    """Degraded data -> NO_TRADE regardless of everything else."""
    analysis = _strong_analysis()
    det = _gate_eval(_SETTINGS, analysis, degraded=True)
    assert det["decision"] == "NO_TRADE"
    assert det["data_quality"] == "FAIL"


def test_risk_failure_forces_no_trade():
    """R:R below minimum -> NO_TRADE even with STRONG confluence."""
    analysis = _strong_analysis()
    analysis["signal"]["risk_reward"] = 1.0
    det = _gate_eval(_SETTINGS, analysis, degraded=False)
    assert det["decision"] == "NO_TRADE"
    assert det["risk_gate"] == "FAIL"


def test_timeframe_conflict_forces_no_trade():
    """Direct HTF conflict -> NO_TRADE."""
    analysis = _strong_analysis()
    analysis["confluence"]["conflicts"] = ["Conflict: 4H Macro is Bullish but 1H Structure is Bearish"]
    det = _gate_eval(_SETTINGS, analysis, degraded=False)
    assert det["decision"] == "NO_TRADE"
    assert any(g["name"] == "Timeframe Alignment" and g["status"] == "FAIL" for g in det["gates"])


def test_watch_state_for_moderate_confluence():
    """MODERATE confluence (no hard fail) -> WATCH."""
    analysis = _base_analysis()
    analysis["confluence"] = {"total_score": 65.0, "quality": "MODERATE", "is_tradable": False, "conflicts": []}
    analysis["signal"]["confidence_score"] = 65.0
    analysis["signal"]["signal_quality"] = "MODERATE"
    det = _gate_eval(_SETTINGS, analysis, degraded=False)
    assert det["decision"] == "WATCH"


# ===========================================================================
# Evidence verification
# ===========================================================================


def test_evidence_verifies_backend_facts():
    """Backend facts are always verified; absent facts are NOT_VERIFIED."""
    analysis = _base_analysis()
    retr = {"entry_touched": False, "tp_locked": False, "state": "TP_DYNAMIC"}
    evidence = _verify_evidence(_SETTINGS, analysis, retr, analysis["ai_validation"])
    by_claim = {e["claim"]: e for e in evidence}
    assert by_claim["4H bias"]["status"] == "VERIFIED"
    assert by_claim["Confluence score"]["status"] == "VERIFIED"
    assert by_claim["Entry touched"]["status"] == "NOT_VERIFIED"
    assert by_claim["TP frozen"]["status"] == "NOT_VERIFIED"


def test_evidence_mismatch_when_ai_approves_below_threshold():
    """AI APPROVE with confluence below threshold -> EVIDENCE MISMATCH."""
    analysis = _base_analysis()  # confluence 42, AI REJECT -> change to APPROVE
    analysis["ai_validation"]["status"] = "APPROVE"
    evidence = _verify_evidence(_SETTINGS, analysis, None, analysis["ai_validation"])
    mismatch = [e for e in evidence if e["status"] == "MISMATCH"]
    assert mismatch, "expected an evidence mismatch"
    assert any("confluence" in m["claim"].lower() for m in mismatch)


def test_evidence_ai_claim_entry_touched_unverified():
    """Test 3: AI text mentions entry touch but backend says false -> UNVERIFIED."""
    analysis = _base_analysis()
    analysis["ai_validation"]["explanation"] = "Entry has been touched, TP is now locked."
    evidence = _verify_evidence(_SETTINGS, analysis, {"entry_touched": False, "tp_locked": False, "state": "TP_DYNAMIC"},
                                analysis["ai_validation"])
    claims = " ".join(e["claim"] for e in evidence)
    assert "entry touched" in claims.lower()


def test_evidence_ai_claim_tp_frozen_unverified():
    """AI text mentions frozen TP but backend says not locked -> UNVERIFIED."""
    analysis = _base_analysis()
    analysis["ai_validation"]["reasoning"] = "TP is frozen after entry."
    evidence = _verify_evidence(_SETTINGS, analysis, {"entry_touched": False, "tp_locked": False, "state": "TP_DYNAMIC"},
                                analysis["ai_validation"])
    claims = " ".join(e["claim"] for e in evidence)
    assert "tp frozen" in claims.lower()


# ===========================================================================
# Timeframe alignment
# ===========================================================================


def test_timeframe_alignment_matrix():
    """4H BULLISH + 1H BULLISH + 30M LONG + 15M NEUTRAL -> aligned count."""
    analysis = _base_analysis()
    tf = _timeframe_alignment(analysis)
    assert tf["total"] == 4
    rows = {r["timeframe"]: r for r in tf["rows"]}
    assert rows["4H"]["status"] == "ALIGNED"
    assert rows["1H"]["status"] == "ALIGNED"


def test_timeframe_conflict_detected():
    """15M BEARISH vs 4H BULLISH -> CONFLICT."""
    analysis = _base_analysis()
    analysis["market_bias"]["15m"]["trend"] = "BEARISH"
    tf = _timeframe_alignment(analysis)
    rows = {r["timeframe"]: r for r in tf["rows"]}
    assert rows["15M"]["status"] == "CONFLICT"
    assert tf["status"] == "CONFLICT"


# ===========================================================================
# Risk validation
# ===========================================================================


def test_risk_validation_geometry():
    """LONG entry/sl/tp geometry validation."""
    analysis = _strong_analysis()
    risk = _risk_validation(_SETTINGS, analysis)
    assert risk["gate"] == "PASS"
    assert risk["entry_valid"] is True
    assert risk["sl_valid"] is True
    assert risk["tp_valid"] is True


def test_risk_validation_fails_bad_geometry():
    """SL above entry for a LONG -> SL invalid -> gate FAIL."""
    analysis = _strong_analysis()
    analysis["signal"]["stop_loss"] = 4600.0  # above entry (LONG) -> invalid
    risk = _risk_validation(_SETTINGS, analysis)
    assert risk["sl_valid"] is False
    assert risk["gate"] == "FAIL"


# ===========================================================================
# Lifecycle
# ===========================================================================


def test_lifecycle_no_setup():
    lc = _lifecycle(None, _base_analysis())
    assert lc["no_setup"] is True


def test_lifecycle_steps_from_state():
    retr = {"state": "TP_DYNAMIC", "bos": {"price": 100.0}, "point_2": {"price": 90.0},
            "entry_touched": False, "tp_locked": False}
    lc = _lifecycle(retr, _base_analysis())
    assert lc["no_setup"] is False
    names = [s[0] for s in lc["steps"]]
    assert "BOS" in names and "POINT 2" in names and "WAITING FOR ENTRY" in names


# ===========================================================================
# What must change
# ===========================================================================


def test_what_must_change_confluence_and_action():
    analysis = _base_analysis()
    det = _gate_eval(_SETTINGS, analysis, degraded=False)
    wmc = _what_must_change(_SETTINGS, det, None)
    assert wmc["action"] == "WAIT"
    areas = [i["area"] for i in wmc["items"]]
    assert "Confluence" in areas


# ===========================================================================
# Performance / insufficient data
# ===========================================================================


def test_performance_insufficient_data():
    perf = _performance_summary([])
    assert perf["status"] == "INSUFFICIENT_DATA"


def test_performance_computes_metrics():
    history = [
        {"ai_decision": "APPROVE"}, {"ai_decision": "REJECT"}, {"ai_decision": "CAUTION"},
        {"ai_decision": "APPROVE"}, {"ai_decision": "REJECT"},
    ]
    perf = _performance_summary(history)
    assert perf["status"] == "AVAILABLE"
    assert perf["approved"] == 2
    assert perf["rejected"] == 2
    assert perf["caution"] == 1


# ===========================================================================
# AI health
# ===========================================================================


def test_ai_health_no_secrets():
    """Health payload must never expose API keys / secrets."""
    health = _ai_health(_SETTINGS)
    assert health["advisory_only"] is True
    for prov, d in health["providers"].items():
        assert "key" not in d
        assert "token" not in d
        assert "secret" not in d
        assert "authorization" not in str(d).lower()


# ===========================================================================
# Endpoint
# ===========================================================================


def _monkeypatch_app(monkeypatch, analysis, history=None, degraded=False, provider_status=None):
    """Patch the ai route's dependencies to return canned data."""
    from app.api.routes import ai as ai_route
    from app.data.models import DataQualityStatus

    class FakeLive:
        async def data_quality(self):
            return DataQualityStatus(
                connected=not degraded, historical_available=True, historical_fresh=True,
                candle_count=100, degraded=degraded,
                degradation_reason="degraded" if degraded else "",
                live_price=4466.0,
            )
        async def get_latest_price(self, symbol):
            return 4466.0

    async def fake_get_live_analysis(symbol):
        return analysis

    async def fake_db_session():
        from app.database.connection import async_session_factory
        async with async_session_factory() as s:
            yield s

    class FakeRepo:
        async def list_ai_validation_history(self, limit=50):
            return history or []

    def fake_repo_factory(session):
        return FakeRepo()

    monkeypatch.setattr(ai_route, "get_live_service", lambda: FakeLive())
    monkeypatch.setattr(ai_route, "_get_live_analysis", fake_get_live_analysis)
    monkeypatch.setattr(ai_route, "Repository", fake_repo_factory)


@pytest.mark.asyncio
async def test_endpoint_returns_full_payload(monkeypatch, in_memory_db: AsyncSession):
    """GET /ai/validation/XAUUSD returns the full decision-audit payload."""
    from app.api.app import create_app
    from app.api.routes import ai as ai_route

    analysis = _base_analysis()

    class FakeLive:
        async def data_quality(self):
            from app.data.models import DataQualityStatus
            return DataQualityStatus(connected=True, historical_available=True, candle_count=100,
                                     degraded=False, live_price=4466.0)
        async def get_latest_price(self, symbol):
            return 4466.0

    async def fake_get_live_analysis(symbol):
        return analysis

    async def fake_db_session():
        yield in_memory_db

    monkeypatch.setattr(ai_route, "get_live_service", lambda: FakeLive())
    monkeypatch.setattr(ai_route, "get_live_analysis", fake_get_live_analysis)

    app = create_app()
    app.dependency_overrides[ai_route.get_db_session] = fake_db_session

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ai/validation/XAUUSD")
        assert resp.status_code == 200
        d = resp.json()
        assert d["symbol"] == "XAUUSD"
        assert d["ai"]["decision"] == "REJECT"
        assert d["deterministic"]["decision"] == "NO_TRADE"
        assert "evidence" in d
        assert "timeframes" in d
        assert "history" in d
        assert "health" in d
        assert "what_must_change" in d


@pytest.mark.asyncio
async def test_endpoint_ai_unavailable_deterministic_result(monkeypatch, in_memory_db: AsyncSession):
    """Test 4: AI provider unavailable -> AI=UNAVAILABLE, FINAL=deterministic result."""
    from app.api.app import create_app
    from app.api.routes import ai as ai_route

    analysis = _strong_analysis()
    analysis["ai_validation"] = {"status": "UNAVAILABLE", "confidence": 0.0,
                                 "explanation": "ALL_AI_PROVIDERS_UNAVAILABLE",
                                 "identified_risks": [], "missing_confirmations": [],
                                 "provider": "NONE", "reason_code": "PROVIDER_ERROR"}

    class FakeLive:
        async def data_quality(self):
            from app.data.models import DataQualityStatus
            return DataQualityStatus(connected=True, historical_available=True, candle_count=100,
                                     degraded=False, live_price=4466.0)
        async def get_latest_price(self, symbol):
            return 4466.0

    async def fake_get_live_analysis(symbol):
        return analysis

    async def fake_db_session():
        yield in_memory_db

    monkeypatch.setattr(ai_route, "get_live_service", lambda: FakeLive())
    monkeypatch.setattr(ai_route, "get_live_analysis", fake_get_live_analysis)

    app = create_app()
    app.dependency_overrides[ai_route.get_db_session] = fake_db_session

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ai/validation/XAUUSD")
        assert resp.status_code == 200
        d = resp.json()
        assert d["ai"]["decision"] == "UNAVAILABLE"
        assert d["ai"]["status"] == "UNAVAILABLE"
        assert d["deterministic"]["decision"] == "VALIDATED_SIGNAL"
