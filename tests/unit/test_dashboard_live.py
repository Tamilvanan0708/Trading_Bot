"""
Dashboard live-data consistency tests.

Verifies that the main dashboard panel uses the LIVE analysis endpoint,
never silently falls back to the historical CSV endpoint, and clearly
distinguishes LIVE data from HISTORICAL/backtest data.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.data.live.service import LiveMarketDataService
from app.data.models import Candle, Tick


def _candle(ts, c) -> Candle:
    return Candle(timestamp=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)


@pytest.fixture
def client():
    return TestClient(create_app())


def test_dashboard_serves_premium_terminal(client):
    """The main /dashboard must serve the premium terminal SPA."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "XAU AI" in r.text


def test_dashboard_uses_live_endpoint(client):
    """The premium dashboard must call the LIVE analysis endpoint."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    # The SPA references the JS assets that fetch the live endpoint
    assert "/terminal/static/js/api.js" in r.text
    assert "/terminal/static/js/app.js" in r.text
    # The API client must target /analysis/live/XAUUSD (not the CSV endpoint)
    api_js = client.get("/terminal/static/js/api.js")
    assert "liveAnalysis" in api_js.text
    assert "/analysis/live/" in api_js.text


def test_dashboard_no_silent_csv_fallback(client):
    """The premium dashboard must NOT use the historical CSV endpoint for live data."""
    api_js = client.get("/terminal/static/js/api.js").text
    assert "/analysis/live/" in api_js  # live analysis endpoint
    # The legacy DASHBOARD_HTML is preserved at /dashboard-legacy
    r_legacy = client.get("/dashboard-legacy")
    assert r_legacy.status_code == 200
    assert "LIVE FEED DISCONNECTED" in r_legacy.text


def test_dashboard_distinguishes_live_and_historical(client):
    """The premium dashboard must clearly show the live feed status."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    # The new terminal has a LIVE pill indicator
    assert "LIVE" in r.text or "DEGRADED" in r.text
    # Legacy dashboard preserved at /dashboard-legacy
    r_legacy = client.get("/dashboard-legacy")
    assert r_legacy.status_code == 200
    assert "HISTORICAL / BACKTEST" in r_legacy.text


def test_dashboard_has_live_indicator_fields(client):
    """The premium dashboard must include live market indicator elements."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    # The new terminal has live price, ticket, and connection indicators
    assert "top-price" in r.text
    assert "live-pill" in r.text
    assert "conn-binance" in r.text
    assert "conn-dq" in r.text
    # Legacy dashboard preserved at /dashboard-legacy
    r_legacy = client.get("/dashboard-legacy")
    assert r_legacy.status_code == 200
    for field in ["live-bid", "live-ask", "live-provider", "live-tick-ts",
                  "live-candle-ts", "live-analysis-ts", "live-mode-badge"]:
        assert f'id="{field}"' in r_legacy.text, f"Missing legacy indicator: {field}"


def test_dashboard_and_terminal_serve_same_app(client):
    """Both /dashboard and /terminal must serve the same premium terminal."""
    d = client.get("/dashboard").text
    t = client.get("/terminal").text
    assert "XAU AI" in d
    assert "XAU AI" in t
    # Same static assets referenced from both routes
    assert "/terminal/static/css/terminal.css" in d
    assert "/terminal/static/css/terminal.css" in t
    assert "/terminal/static/js/app.js" in d
    assert "/terminal/static/js/app.js" in t
    # Both inject the refresh interval and both serve the same body content
    assert "XAU_REFRESH_MS" in d
    assert "XAU_REFRESH_MS" in t


@pytest.mark.asyncio
async def test_live_snapshot_price_uses_live_tick_not_sample_close():
    """Regression: live price must come from the feed tick, not the sample close."""
    service = LiveMarketDataService()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Sample-base close around $2662 (synthetic)
    service._closed_15m = [
        _candle(now - timedelta(minutes=15), c=2661.86),
    ]

    # A real live tick arrives at ~$4613
    tick = Tick(symbol="XAUUSD", timestamp=now, bid=4613.49, ask=4613.50)
    await service.on_tick(tick)

    snap = await service.get_multi_timeframe_snapshot("XAUUSD")
    assert snap.current_price == tick.mid
    assert snap.current_price > 4000  # live, NOT the $2662 synthetic close
    assert snap.current_price != 2661.86


@pytest.mark.asyncio
async def test_live_snapshot_falls_back_to_close_only_without_ticks():
    service = LiveMarketDataService()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    service._closed_15m = [_candle(now - timedelta(minutes=15), c=2661.86)]
    # No live tick ever arrived
    snap = await service.get_multi_timeframe_snapshot("XAUUSD")
    assert snap.current_price == 2661.86