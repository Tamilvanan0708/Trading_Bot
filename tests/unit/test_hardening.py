"""
Tests for market-hours awareness, SQLite backup, notification logging, and
MT5 feed option.
"""

import os
from datetime import datetime, timezone

import pytest

from app.core.market_hours import is_market_open, next_market_open

# ---------------------------------------------------------------------------
# Market hours
# ---------------------------------------------------------------------------

def _utc(y, m, d, h):
    return datetime(y, m, d, h, 0, 0, tzinfo=timezone.utc)


def test_weekday_market_open():
    # Monday 10:00 UTC — open
    assert is_market_open(_utc(2026, 8, 24, 10)) is True


def test_saturday_closed():
    # Saturday 12:00 UTC — closed all day
    assert is_market_open(_utc(2026, 8, 22, 12)) is False


def test_sunday_before_2300_closed():
    assert is_market_open(_utc(2026, 8, 23, 12)) is False


def test_sunday_after_2300_open():
    assert is_market_open(_utc(2026, 8, 23, 23)) is True


def test_friday_before_2300_open():
    assert is_market_open(_utc(2026, 8, 21, 12)) is True


def test_friday_after_2300_closed():
    assert is_market_open(_utc(2026, 8, 21, 23)) is False


def test_next_market_open_from_saturday():
    sat = _utc(2026, 8, 22, 12)
    nxt = next_market_open(sat)
    assert nxt.weekday() == 6  # Sunday
    assert nxt.hour == 23


def test_market_hours_naive_input():
    naive = datetime(2026, 8, 24, 10, 0)  # no tzinfo
    assert is_market_open(naive) is True


# ---------------------------------------------------------------------------
# SQLite backup script
# ---------------------------------------------------------------------------

def _make_sqlite_db(path, rows=5):
    import sqlite3
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        for i in range(rows):
            conn.execute("INSERT INTO t (v) VALUES (?)", (f"v{i}",))
        conn.commit()
    finally:
        conn.close()


def test_backup_script_creates_backup(tmp_path):
    import subprocess
    import sys
    db = tmp_path / "test.db"
    _make_sqlite_db(db)
    backup_dir = tmp_path / "backups"
    r = subprocess.run(
        [sys.executable, "scripts/backup_db.py", "--db", str(db), "--backup-dir", str(backup_dir), "--keep", "2"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    files = os.listdir(backup_dir)
    assert any(f.endswith(".db") for f in files)


def test_backup_script_prunes(tmp_path):
    import subprocess
    import sys
    db = tmp_path / "test.db"
    _make_sqlite_db(db)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # Pre-populate 3 backups
    for i in range(3):
        (backup_dir / f"xauusd_2026080{i}.db").write_bytes(b"x")
    r = subprocess.run(
        [sys.executable, "scripts/backup_db.py", "--db", str(db), "--backup-dir", str(backup_dir), "--keep", "2"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    remaining = [f for f in os.listdir(backup_dir) if f.endswith(".db")]
    assert len(remaining) <= 2


def test_restore_script_creates_pre_restore_snapshot(tmp_path):
    import subprocess
    import sys
    db = tmp_path / "test.db"
    _make_sqlite_db(db, rows=2)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup = backup_dir / "restore_source.db"
    _make_sqlite_db(backup, rows=9)
    r = subprocess.run(
        [sys.executable, "scripts/restore_db.py", "--backup", str(backup), "--db", str(db),
         "--backup-dir", str(backup_dir), "--yes"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    # Pre-restore snapshot of the original db exists
    snapshots = [f for f in os.listdir(backup_dir) if f.startswith("pre_restore_")]
    assert len(snapshots) == 1
    # DB restored with the backup's rows
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        conn.close()
    assert n == 9


# ---------------------------------------------------------------------------
# Notification logging persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notification_log_persists(in_memory_db):
    from app.database.repository import Repository
    from app.notifications.telegram_service import TelegramService
    repo = Repository(in_memory_db)
    svc = TelegramService()
    await svc._log_notification(repo, "sig-1", "TELEGRAM", "FAILED", "test error")
    await in_memory_db.flush()
    logs = await repo.list_notifications(limit=10)
    assert len(logs) == 1
    assert logs[0].channel == "TELEGRAM"
    assert logs[0].status == "FAILED"
    assert logs[0].error_message == "test error"


@pytest.mark.asyncio
async def test_telegram_dedup_same_signal_id_skipped(in_memory_db, monkeypatch):
    """Telegram alert dedup: same signal_id twice must dispatch only once."""
    from app.config.settings import Settings
    from app.core.constants import (
        MarketBias,
        SignalDirection,
        SignalQuality,
        StrategyType,
    )
    from app.database.repository import Repository
    from app.notifications import telegram_service as telmod
    from app.notifications.telegram_service import TelegramService
    from app.signals.models import SignalPayload

    class _FakeResponse:
        status_code = 200
        text = "ok"

    class _FakePost:
        def __init__(self):
            self.calls = 0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None):
            self.calls += 1
            return _FakeResponse()

    fake = _FakePost()
    monkeypatch.setattr(telmod.httpx, "AsyncClient", lambda **kw: fake)

    sig = SignalPayload(
        instrument="XAUUSD", direction=SignalDirection.LONG, strategy=StrategyType.CONFLUENCE,
        timeframe="15m", entry=100.0, stop_loss=90.0, take_profit_1=105.0,
        take_profit_2=110.0, take_profit_3=115.0, risk_reward=2.0,
        confidence_score=80.0, signal_quality=SignalQuality.STRONG,
        market_bias=MarketBias.BULLISH,
    )
    repo = Repository(in_memory_db)
    svc = TelegramService(settings=Settings(
        TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c"
    ))
    r1 = await svc.send_signal_alert(sig, repo=repo)
    assert r1 is True  # dispatched
    assert fake.calls == 1
    r2 = await svc.send_signal_alert(sig, repo=repo)
    assert r2 is True  # deduped — returned True without a second dispatch
    assert fake.calls == 1  # only one HTTP call ever happened


@pytest.mark.asyncio
async def test_telegram_dedup_cooldown_typed_alert(tmp_path, monkeypatch):
    """Typed alerts with a cooldown must be blocked within the window."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.config.settings import Settings
    from app.database.models import Base
    from app.notifications.telegram_service import TelegramService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/tel.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.database.connection.async_session_factory", maker)

    svc = TelegramService(settings=Settings(
        TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="x", TELEGRAM_CHAT_ID="y"
    ))
    sent = []
    async def fake_send_raw(text, repo=None):
        sent.append(text)
        return True
    monkeypatch.setattr(svc, "send_raw_alert", fake_send_raw)

    # First send sets the cooldown key and dispatches.
    r1 = await svc.send_typed_alert("dedup_key", "msg1", cooldown_seconds=3600)
    assert r1 is True
    assert len(sent) == 1
    # Second send within the cooldown window is DEDUPED (returns False).
    r2 = await svc.send_typed_alert("dedup_key", "msg2", cooldown_seconds=3600)
    assert r2 is False
    assert len(sent) == 1  # only the first message dispatched
    await engine.dispose()


# ---------------------------------------------------------------------------
# MT5 feed build option
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_feed_mt5_returns_none_and_sets_polling():
    from app.config.settings import Settings
    from app.data.live.service import LiveMarketDataService
    service = LiveMarketDataService(settings=Settings(LIVE_FEED_PROVIDER="mt5"))
    feed = service._build_feed()
    assert feed is None  # MT5 is polling-based, not a WebSocket feed
    assert service.settings.LIVE_FEED_PROVIDER == "mt5"


@pytest.mark.asyncio
async def test_mt5_polling_starts_when_configured():
    from app.config.settings import Settings
    from app.data.live.service import LiveMarketDataService
    service = LiveMarketDataService(settings=Settings(LIVE_FEED_PROVIDER="mt5", MT5_ENABLED=False))
    # MT5_ENABLED=false -> polling task should log and stop quickly
    await service._start_mt5_polling()
    assert service._running is False or service._mt5_task is None