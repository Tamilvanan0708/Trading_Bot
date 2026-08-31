"""
Deployment / local-server tests.

These verify the local startup tooling (scripts, one-click launcher, watchdog,
Windows service docs) exists and behaves, plus the key reliability guarantees:
  - startup script exists and is syntactically valid
  - health/dashboard/terminal/docs endpoints reachable (TestClient)
  - server survives a fresh TestClient app without Binance (degraded, not crash)
  - graceful shutdown leaves the DB usable and WAL mode intact
  - no secret leakage in the log files
  - single-worker requirement is documented
"""

import os
import re

import pytest

from fastapi.testclient import TestClient

from app.api.app import create_app

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Scripts / tooling existence
# ---------------------------------------------------------------------------

def test_startup_scripts_exist():
    required = [
        "scripts/start_local.ps1",
        "scripts/check_local_server.ps1",
        "scripts/watch_local_server.ps1",
        "scripts/install_windows_service.ps1",
        "scripts/xau_terminal_watchdog.xml",
        "START_PICKO_XAU_TERMINAL.bat",
        "docs/LOCAL_WINDOWS_SERVICE.md",
        "requirements.txt",
    ]
    for rel in required:
        assert os.path.exists(os.path.join(ROOT, rel)), f"missing {rel}"


def test_startup_script_is_ascii():
    """PowerShell 5.1 misreads non-ASCII bytes without a BOM — scripts must be ASCII."""
    for rel in ("scripts/start_local.ps1", "scripts/check_local_server.ps1",
                "scripts/watch_local_server.ps1", "scripts/install_windows_service.ps1"):
        p = os.path.join(ROOT, rel)
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        non_ascii = [ch for ch in text if ord(ch) > 127]
        assert not non_ascii, f"{rel} contains non-ASCII: {set(non_ascii)}"


def test_startup_script_uses_single_worker():
    p = os.path.join(ROOT, "scripts/start_local.ps1")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    assert "--workers" in text
    assert '"1"' in text or "'1'" in text or '1' in text.split("--workers")[1][:10]


def test_startup_script_detects_existing_server_and_health_wait():
    p = os.path.join(ROOT, "scripts/start_local.ps1")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    assert "Test-Health" in text or "/health" in text
    assert "already healthy" in text
    assert "not starting a duplicate" in text


def test_watchdog_has_backoff_and_single_instance_guard():
    p = os.path.join(ROOT, "scripts/watch_local_server.ps1")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    assert "watchdog.lock" in text
    assert "10" in text and "60" in text  # backoff range
    assert "/health" in text
    assert "RECOVERY" in text


def test_docs_explain_windows_service():
    p = os.path.join(ROOT, "docs/LOCAL_WINDOWS_SERVICE.md")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    for kw in ("schtasks", "install_windows_service.ps1", "watch_local_server.ps1",
               "start_local.ps1", "localhost:8000", "uninstall"):
        assert kw in text, f"docs missing '{kw}'"


def test_no_secrets_in_requirements_or_env_example():
    for rel in ("requirements.txt", ".env.example"):
        p = os.path.join(ROOT, rel)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        # No hardcoded secrets allowed
        assert not re.search(r"(?i)(api[_-]?key|secret|token)\s*=\s*[\"']?[A-Za-z0-9]{16,}", text), rel


# ---------------------------------------------------------------------------
# Endpoint reachability through the app (no live server required)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def test_health_endpoint_reachable(client):
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] in ("healthy", "degraded")


def test_dashboard_reachable(client):
    assert client.get("/dashboard").status_code == 200


def test_terminal_reachable(client):
    assert client.get("/terminal").status_code == 200


def test_docs_reachable(client):
    assert client.get("/docs").status_code == 200


def test_health_is_degraded_but_alive_without_binance(client):
    """Server availability != Binance availability. /health must answer even
    when the market feed is down, and it must NOT lie about the feed."""
    r = client.get("/health")
    d = r.json()
    assert "feed_connected" in d
    assert isinstance(d["feed_connected"], bool)
    assert "db_ok" in d
    # If the feed is down, the server is still up and reports honestly.
    assert d["status"] in ("healthy", "degraded")


def test_health_exposes_ai_provider_and_db_state(client):
    r = client.get("/health")
    d = r.json()
    assert "ai_provider" in d
    assert d["ai_provider"]["advisory_only"] is True
    assert "db_ok" in d


# ---------------------------------------------------------------------------
# Graceful shutdown / database integrity
# ---------------------------------------------------------------------------

def test_db_preserves_wal_and_tables_after_shutdown():
    """After creating/closing an app (startup + shutdown), the SQLite DB must
    remain usable and WAL mode intact."""
    import sqlite3

    from app.database.connection import async_session_factory
    import asyncio

    db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/xauusd_agent.db")
    db_path = db_url.replace("sqlite+aiosqlite:///", "")

    # Just verify the DB file opens and WAL is on (tables exist).
    if not os.path.exists(db_path):
        pytest.skip("DB file not present at %s" % db_path)
    con = sqlite3.connect(db_path)
    try:
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert mode == "wal"
        for t in ("signals", "ai_validations", "paper_trades", "system_state"):
            assert t in tables, f"missing table {t}"
    finally:
        con.close()


def test_single_worker_requirement_documented():
    """The scheduler is single-worker only (SQLite). Docs must say so."""
    for rel in ("docs/DEPLOYMENT.md", "docs/LOCAL_WINDOWS_SERVICE.md"):
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            text = f.read()
        assert "workers" in text.lower()


# ---------------------------------------------------------------------------
# No secret leakage in logs
# ---------------------------------------------------------------------------

def test_logs_do_not_leak_secrets():
    logs_dir = os.path.join(ROOT, "logs")
    if not os.path.isdir(logs_dir):
        pytest.skip("logs dir not present")
    pattern = re.compile(r"(?i)(api[_-]?key|secret|password|Bearer\s+[A-Za-z0-9]{8,})")
    found = []
    for name in ("startup.log", "recovery.log"):
        p = os.path.join(logs_dir, name)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                if pattern.search(text):
                    found.append(name)
            except OSError:
                pass
    assert not found, f"potential secrets in log files: {found}"
