"""
Tests for the premium trading terminal (new UI) and its read-only endpoints.
The legacy /dashboard remains untouched (covered by test_dashboard_live.py).
"""

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def test_terminal_serves_spa(client):
    r = client.get("/terminal")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "XAU AI" in r.text
    assert "Signal Intelligence Terminal" in r.text


def test_terminal_serves_css(client):
    r = client.get("/terminal/static/css/terminal.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]
    assert "--bg-0" in r.text  # design tokens present
    assert "chart-box" in r.text  # chart container
    assert "canvas.chart" in r.text  # full-width canvas
    assert "live-chart-status" in r.text  # chart status overlay
    assert "live-price-legend" in r.text  # price legend
    assert "feed-banner" in r.text  # status banner
    assert "tf-toolbar" in r.text  # timeframe buttons


def test_terminal_serves_js(client):
    for js in ("api.js", "app.js", "ui.js", "charts.js"):
        r = client.get(f"/terminal/static/js/{js}")
        assert r.status_code == 200, js
        assert "text/javascript" in r.headers["content-type"] or "text/javascript" in r.headers.get("content-type", "")


def test_terminal_spa_fallback_for_client_routes(client):
    r = client.get("/terminal/candidates")  # SPA client route
    assert r.status_code == 200
    assert "XAU AI" in r.text


def test_notifications_endpoint(client):
    r = client.get("/notifications?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_telegram_status_endpoint(client):
    r = client.get("/telegram/status")
    assert r.status_code == 200
    body = r.json()
    assert "configured" in body
    assert "status" in body
    # Never expose secret VALUES (token/chat-id strings).
    raw = str(body)
    assert "BOT_TOKEN" not in raw
    assert "CHAT_ID" not in raw
    for v in body.values():
        assert not (isinstance(v, str) and len(v) > 20 and ":" in v)  # no token-like values


def test_legacy_dashboard_still_serves(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
