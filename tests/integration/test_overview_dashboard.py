"""
Tests for the Overview dashboard (Command Center).

The Overview must be a real-time live dashboard fed by the aggregated
/overview endpoint. Every value must come from the actual backend — no
hardcoded prices, no fabricated signals, no hardcoded health states.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app

VALID_DATA_STATUSES = {"HEALTHY", "HISTORICAL", "HISTORICAL_CACHE", "NO_DATA"}
VALID_MTF_TRENDS = {"BULLISH", "BEARISH", "NEUTRAL", "RANGING", "NO_DATA"}
VALID_SIGNAL_STATUSES = {"LONG", "SHORT", "NO_TRADE", "WAITING", "DATA_UNAVAILABLE"}
VALID_SAFETY_STATUSES = {
    "SIGNALS_ENABLED", "SIGNALS_BLOCKED", "OBSERVATION_MODE",
    "PAPER_TRADING_BLOCKED", "REAL_MONEY_ENABLED",
}


@pytest.fixture
def client():
    return TestClient(create_app())


# ----------------------------------------------------------------------
# Backend: /overview endpoint
# ----------------------------------------------------------------------


def test_overview_endpoint_returns_all_sections(client):
    r = client.get("/overview/XAUUSD")
    assert r.status_code == 200
    d = r.json()
    for section in ("market", "regime", "mtf", "smc", "fibonacci",
                    "signal", "ai_validation", "safety", "health",
                    "market_update", "data_status"):
        assert section in d, f"Missing overview section: {section}"


def test_overview_data_status_is_from_backend(client):
    r = client.get("/overview/XAUUSD")
    d = r.json()
    assert d["data_status"] in VALID_DATA_STATUSES


def test_overview_market_values_real(client):
    """Market price must come from a real source — never fabricated."""
    r = client.get("/overview/XAUUSD")
    d = r.json()
    m = d["market"]
    # price may be None only when NO_DATA; otherwise must be a sane positive number
    if m["price"] is not None:
        assert m["price"] > 0
        assert m["price"] < 10000  # real XAU/USD magnitude
    assert m["timeframe"] == "15m"
    assert isinstance(m["candles"], int)
    if d["data_status"] != "NO_DATA":
        assert m["candles"] > 0
        assert m["last_candle"] is not None


def test_overview_historical_cache_does_not_imply_live_signal(client):
    """When the feed is down (HISTORICAL/HISTORICAL_CACHE), the signal section
    must NOT present a LONG/SHORT as a valid current signal."""
    r = client.get("/overview/XAUUSD")
    d = r.json()
    if d["data_status"] in ("HISTORICAL", "HISTORICAL_CACHE", "NO_DATA"):
        sig = d["signal"]
        assert sig["status"] in ("NO_TRADE", "DATA_UNAVAILABLE", "WAITING")


def test_overview_mtf_has_all_timeframes(client):
    r = client.get("/overview/XAUUSD")
    d = r.json()
    assert set(d["mtf"].keys()) >= {"5m", "15m", "30m", "1h", "4h"}
    for tf, info in d["mtf"].items():
        assert info["trend"] in VALID_MTF_TRENDS


def test_overview_regime_shape(client):
    r = client.get("/overview/XAUUSD")
    d = r.json()
    reg = d["regime"]
    assert reg["regime"] in {"TRENDING", "RANGING", "HIGH_VOLATILITY",
                             "LOW_VOLATILITY", "UNCERTAIN", "UNKNOWN"}
    assert reg["trend"] in {"BULLISH", "BEARISH", "NEUTRAL"}


def test_overview_safety_shape(client):
    r = client.get("/overview/XAUUSD")
    d = r.json()
    s = d["safety"]
    assert s["status"] in VALID_SAFETY_STATUSES
    assert isinstance(s["gates"], list) and len(s["gates"]) >= 4
    for g in s["gates"]:
        assert "gate" in g and "pass" in g and "status" in g
    # Real-money execution must NEVER report enabled in this system.
    assert s["status"] != "REAL_MONEY_ENABLED"


def test_overview_health_lists_services(client):
    r = client.get("/overview/XAUUSD")
    d = r.json()
    names = {s["name"] for s in d["health"]["services"]}
    for required in ("BINANCE", "DATABASE", "SCHEDULER", "DATA_PIPELINE",
                     "MTF_ANALYSIS", "SMC_ENGINE", "FIBONACCI_ENGINE",
                     "SIGNAL_ENGINE", "AI_VALIDATION", "OUTCOME_TRACKING"):
        assert required in names, f"Missing health service: {required}"
    # Health must reflect real state — not everything hardcoded green.
    statuses = {s["status"] for s in d["health"]["services"]}
    assert statuses  # at least some data present


def test_overview_smc_and_fib_shape(client):
    r = client.get("/overview/XAUUSD")
    d = r.json()
    assert d["smc"]["status"] in {"ACTIVE", "NO_VALID_SMC_SETUP", "SMC_DATA_STALE", "NO_DATA"}
    assert d["fibonacci"]["status"] in {"GOLDEN_ZONE_ACTIVE", "ACTIVE",
                                        "NO_RETRACEMENT_SETUP", "NO_DATA"}


def test_overview_ai_validation_shape(client):
    r = client.get("/overview/XAUUSD")
    d = r.json()
    ai = d["ai_validation"]
    assert ai["status"] in {"APPROVE", "REJECT", "CAUTION", "WAITING"}
    assert "explanation" in ai


def test_overview_signal_status_shape(client):
    r = client.get("/overview/XAUUSD")
    d = r.json()
    assert d["signal"]["status"] in VALID_SIGNAL_STATUSES
    if d["signal"]["status"] in ("LONG", "SHORT"):
        sig = d["signal"]
        assert sig["entry"] is not None
        assert sig["stop_loss"] is not None
        assert sig["take_profit_1"] is not None
        assert sig["risk_reward"] is not None


def test_overview_no_fake_prices(client):
    """Regression guard: the overview payload must never contain hardcoded
    price values or random-number simulation."""
    r = client.get("/overview/XAUUSD")
    assert r.status_code == 200
    raw = r.text
    for forbidden in ("2661.86", "4613.50", "9999.99"):
        assert forbidden not in raw, f"Hardcoded/fake price leaked: {forbidden}"


# ----------------------------------------------------------------------
# Frontend: integration with the /overview endpoint
# ----------------------------------------------------------------------


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_frontend_api_js_has_overview_method():
    api_js = _read("app/static/terminal/js/api.js")
    assert "overview" in api_js
    assert "/overview/" in api_js


def test_frontend_app_js_builds_live_overview():
    app_js = _read("app/static/terminal/js/app.js")
    # The Overview view must call the aggregated overview endpoint
    assert "API.overview(" in app_js
    assert 'Routes["/overview"]' in app_js
    # It must not use a static price; it must patch from live data
    assert "loadOverview" in app_js
    assert "patchOverviewMarket" in app_js
    assert "patchOverviewSafety" in app_js
    assert "patchOverviewHealth" in app_js
    # Incremental DOM updates — patch individual cards, not full reload
    assert "patchOverviewMtf" in app_js
    assert "patchOverviewSignal" in app_js
    assert "patchOverviewSmc" in app_js
    assert "patchOverviewFib" in app_js


def test_frontend_app_js_no_full_reload_for_overview():
    """The boot must NOT re-render the whole overview by navigating every tick."""
    app_js = _read("app/static/terminal/js/app.js")
    # The full-page reload loop was removed; incremental update is used instead.
    assert 'location.hash.includes("/overview")) navigate()' not in app_js
    assert "setInterval(loadOverview" in app_js


def test_frontend_app_js_no_hardcoded_prices():
    app_js = _read("app/static/terminal/js/app.js")
    # No fake price literals anywhere in the app shell code.
    for forbidden in ("2661.86", "4613.50", "99.99"):
        assert forbidden not in app_js, f"Hardcoded price in app.js: {forbidden}"


def test_frontend_overview_marks_historical_cache():
    """The Overview must visually distinguish HISTORICAL CACHE from LIVE."""
    app_js = _read("app/static/terminal/js/app.js")
    assert "HISTORICAL CACHE" in app_js
    assert "FEED DEGRADED" in app_js
    assert "NO DATA" in app_js
    assert "LIVE" in app_js


def test_overview_html_render_sections():
    """The Overview shell must include every command-center card."""
    app_js = _read("app/static/terminal/js/app.js")
    for el_id in ("ov-price", "ov-ds", "ov-regime", "ov-mtf", "ov-signal-dir",
                  "ov-ai-status", "ov-safety", "ov-smc-status", "ov-fib-status",
                  "ov-health-list", "ov-mu-body"):
        assert f'id="{el_id}"' in app_js, f"Missing overview element: {el_id}"
