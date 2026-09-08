"""
RETRACEMENT_BOS_V1 — Live engine integration tests.

Tests that the live engine bridge (RetracementLiveService) correctly:
  1. advances the engine with new closed candles
  2. persists active setup to the DB idempotently
  3. tracks dynamic TP updates before entry
  4. detects entry touch and freezes TP
  5. keeps TP immutable after entry (post-entry highs)
  6. returns the correct current state on GET
  7. does not create duplicate polling loops or duplicate DB rows
  8. shows points-only display (no monetary P&L)
  9. navigation does not stop live updates
  10. manages only one controlled update interval per page
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Candle, MultiTimeframeSnapshot
from app.retracement.engine import RetracementBOSEngine
from app.retracement.live import RetracementLiveService, get_retracement_live_service
from app.retracement.models import RetracementSetup, RetracementState
from app.retracement.repository import RetracementRepository

_DT = timedelta(minutes=15)

_APP_JS = os.path.join(os.path.dirname(__file__), "..", "..", "app/static/terminal/js/app.js")


def _bar(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _flat(n, start_ts, price=100.0, spread=0.5):
    out = []
    for _ in range(n):
        out.append(_bar(start_ts, price, price + spread, price - spread, price))
        start_ts += _DT
    return out, start_ts


def _leg(start_ts, prev, tgt, bars=5, spread=0.5, gap=0.3):
    out = []
    direction = 1 if tgt >= prev else -1
    last_close = None
    for k in range(bars):
        price = prev + (tgt - prev) * (k + 1) / bars
        o = prev + direction * gap if k == 0 else last_close
        h = max(o, price) + spread
        l = min(o, price) - spread
        out.append(_bar(start_ts, o, h, l, price))
        start_ts += _DT
        last_close = price
    return out, start_ts


def _zigzag(targets, start_ts, bars=5, spread=0.5, gap=0.3):
    out = []
    prev = targets[0]
    for tgt in targets[1:]:
        leg_bars, start_ts = _leg(start_ts, prev, tgt, bars=bars, spread=spread, gap=gap)
        out.extend(leg_bars)
        prev = tgt
    return out, start_ts


def _ts():
    return datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)


# ===========================================================================
# Test series that produce known retracement states
# ===========================================================================


def _tp_dynamic_series():
    """Ends with an ACTIVE TP_DYNAMIC setup (waiting for entry, not touched).

    Target curve: [100, 106, 100, 110, 104, 113, 108, 117, 112, 121, 116, 125]
    """
    flat, ts = _flat(30, _ts())
    zig, _ = _zigzag([100, 106, 100, 110, 104, 113, 108, 117, 112, 121, 116, 125], ts)
    return flat + zig


def _trade_active_series():
    """Ends with TRADE_ACTIVE (entry touched, TP frozen, no outcome yet).

    Target curve: [100, 106, 100, 110, 104, 113, 108, 117, 112, 121, 116]
    """
    flat, ts = _flat(30, _ts())
    zig, _ = _zigzag([100, 106, 100, 110, 104, 113, 108, 117, 112, 121, 116], ts)
    return flat + zig


def _appended_high_leg(candles, target=135.0, confirm=True):
    """Append a leg up to ``target`` and (optionally) 3 confirming bars."""
    last_ts = candles[-1].timestamp
    new_leg, _ = _leg(last_ts, candles[-1].close, target, bars=5, spread=0.5, gap=0.3)
    if not confirm:
        return candles + new_leg
    # Confirming bars must have highs STRICTLY below the peak's high (target + 0.5)
    # so that detect_swings recognises the peak bar as a confirmed swing HIGH.
    confirm_bars = [
        _bar(new_leg[-1].timestamp + _DT, target - 1.0, target - 0.5, target - 2.0, target - 1.5),
        _bar(new_leg[-1].timestamp + 2 * _DT, target - 1.5, target - 1.2, target - 3.0, target - 2.0),
        _bar(new_leg[-1].timestamp + 3 * _DT, target - 2.0, target - 1.8, target - 3.5, target - 2.5),
    ]
    return candles + new_leg + confirm_bars


# ===========================================================================
# 1. Live state refresh
# ===========================================================================


@pytest.mark.asyncio
async def test_live_state_refresh_with_new_candle(in_memory_db: AsyncSession):
    """Advancing with a candle series must produce a RetracementSetup and
    persist it to the database."""
    candles = _tp_dynamic_series()
    svc = RetracementLiveService(symbol="XAUUSD", timeframe="15m")
    svc._closed_candles = AsyncMock(return_value=candles)

    setup = await svc.advance(in_memory_db)
    assert setup is not None, "expected an active setup"
    assert setup.state == RetracementState.TP_DYNAMIC
    assert setup.dynamic_tp is not None
    assert setup.entry_touched is False
    assert setup.tp_locked is False

    repo = RetracementRepository(in_memory_db)
    persisted = await repo.load_latest_active("XAUUSD", strategy="RETRACEMENT_BOS_V1")
    assert persisted is not None
    assert persisted.setup_id == setup.setup_id


@pytest.mark.asyncio
async def test_live_no_duplicate_db_rows(in_memory_db: AsyncSession):
    """Repeated advance with no new candles must NOT create duplicate setup rows."""
    candles = _tp_dynamic_series()
    svc = RetracementLiveService(symbol="XAUUSD", timeframe="15m")
    svc._closed_candles = AsyncMock(return_value=candles)

    setup1 = await svc.advance(in_memory_db)
    assert setup1 is not None

    svc._closed_candles = AsyncMock(return_value=candles)
    setup2 = await svc.advance(in_memory_db)
    assert setup2 is not None

    # Same logical setup (idempotent persistence -> same setup_id)
    assert setup2.setup_id == setup1.setup_id
    repo = RetracementRepository(in_memory_db)
    persisted = await repo.load_latest_active("XAUUSD", strategy="RETRACEMENT_BOS_V1")
    assert persisted is not None
    assert persisted.setup_id == setup1.setup_id


# ===========================================================================
# 2. Dynamic TP updates before entry
# ===========================================================================


@pytest.mark.asyncio
async def test_dynamic_tp_updates_before_entry(in_memory_db: AsyncSession):
    """A new confirmed swing high must update the dynamic TP before entry."""
    candles = _tp_dynamic_series()
    svc = RetracementLiveService(symbol="XAUUSD", timeframe="15m")
    svc._closed_candles = AsyncMock(return_value=candles)

    setup = await svc.advance(in_memory_db)
    initial_tp = setup.dynamic_tp
    initial_high = setup.current_high_price
    assert initial_tp is not None

    new_candles = _appended_high_leg(candles, target=135.0)
    svc._closed_candles = AsyncMock(return_value=new_candles)

    setup2 = await svc.advance(in_memory_db)
    assert setup2 is not None
    assert setup2.entry_touched is False, "entry should not be touched yet"
    assert setup2.tp_locked is False, "TP should not be locked yet"
    assert setup2.dynamic_tp > initial_tp, "TP should increase with a new valid high"
    assert setup2.current_high_price > initial_high


# ===========================================================================
# 3. Entry touch + TP freeze
# ===========================================================================


@pytest.mark.asyncio
async def test_entry_touch_and_tp_freeze(in_memory_db: AsyncSession):
    """When price touches 0.618, entry must be touched and TP frozen."""
    candles = _trade_active_series()
    svc = RetracementLiveService(symbol="XAUUSD", timeframe="15m")
    svc._closed_candles = AsyncMock(return_value=candles)

    setup = await svc.advance(in_memory_db)
    assert setup.entry_touched is True
    assert setup.tp_locked is True
    assert setup.locked_tp is not None
    assert setup.dynamic_tp == setup.locked_tp


# ===========================================================================
# 4. Post-entry TP immutability
# ===========================================================================


@pytest.mark.asyncio
async def test_tp_immutable_after_entry(in_memory_db: AsyncSession):
    """New highs after entry must never change the locked TP."""
    candles = _trade_active_series()
    svc = RetracementLiveService(symbol="XAUUSD", timeframe="15m")
    svc._closed_candles = AsyncMock(return_value=candles)

    setup = await svc.advance(in_memory_db)
    locked_tp = setup.locked_tp
    assert locked_tp is not None

    new_candles = _appended_high_leg(candles, target=145.0)
    svc._closed_candles = AsyncMock(return_value=new_candles)

    await svc.advance(in_memory_db)
    # A post-entry high that exceeds the frozen TP completes the setup at the
    # FROZEN level (TP_HIT) — the locked TP must never be recalculated from the
    # new high.  The DB row (whether still active or completed) must keep the
    # exact frozen value.
    repo = RetracementRepository(in_memory_db)
    row = await repo.load_setup_by_id(setup.setup_id)
    assert row is not None, "setup row must exist after post-entry highs"
    assert row.locked_tp == locked_tp, f"TP moved from {locked_tp} to {row.locked_tp}"


# ===========================================================================
# 5. GET endpoint reflects live state
# ===========================================================================


@pytest.mark.asyncio
async def test_get_endpoint_returns_live_state(in_memory_db: AsyncSession):
    """GET /retracement/XAUUSD must return the live engine state when available,
    including live_price and data_status."""
    from app.api.app import create_app
    from app.api.routes import retracement as retr_route

    candles = _tp_dynamic_series()
    svc = get_retracement_live_service("XAUUSD")
    svc._closed_candles = AsyncMock(return_value=candles)
    svc.reset()
    svc.live_price = candles[-1].close
    svc.data_status = "HEALTHY"

    class FakeQuoteLive:
        async def get_latest_price(self, symbol):
            return candles[-1].close

        async def data_quality(self):
            from app.data.models import DataQualityStatus
            return DataQualityStatus(connected=True, degraded=False, candle_count=len(candles))

    async def fake_db_session():
        yield in_memory_db

    mp = pytest.MonkeyPatch()
    mp.setattr(retr_route, "get_live_service", lambda: FakeQuoteLive())

    app = create_app()
    # Override the dependency by its captured function object so the route uses
    # the in-memory session (never the real database file).
    app.dependency_overrides[retr_route.get_db_session] = fake_db_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/retracement/XAUUSD")
            assert resp.status_code == 200
            data = resp.json()
            assert data["strategy"] == "RETRACEMENT_BOS_V1"
            assert data["state"] != "NO_SETUP"
            assert "live_price" in data
            assert "data_status" in data
            assert data["levels"] != {}
            assert "0.000" in data["levels"]
            assert "0.618" in data["levels"]
            assert "1.000" in data["levels"]
    finally:
        mp.undo()


# ===========================================================================
# 6. Points-only display (no monetary P&L) — static frontend test
# ===========================================================================


def _retr_section():
    with open(_APP_JS, encoding="utf-8") as f:
        js = f.read()
    return js[js.find('Routes["/retracement"]'):js.find("function renderAnalysis")]


def test_no_monetary_pnl_in_retracement_section():
    """The retracement section must not contain monetary '$' or P&L."""
    import re

    section = _retr_section()
    # JS template literals use `${...}` — only flag '$' that is NOT a template
    # interpolation (i.e. monetary amounts).
    monetary = re.findall(r"\$(?!\{)", section)
    assert not monetary, f"monetary $ found in retracement section: {monetary}"
    assert "P&L" not in section, "P&L found in retracement section"
    assert "UNREALIZED" not in section
    assert "CURRENT MOVEMENT" in section
    assert "POINTS" in section
    assert "WAITING FOR ENTRY" in section


# ===========================================================================
# 7. Navigation does not stop live updates + no duplicate polling loops
# ===========================================================================


def test_navigation_cleanup_and_recreate():
    """The retracement route must set up cleanup and create live interval on
    each navigation (re-creation = no stale timers)."""
    section = _retr_section()
    assert "window.__viewCleanup" in section
    assert "_liveTimer" in section
    # Exactly one controlled live interval
    assert "setInterval(doFetch, RETR_LIVE_MS)" in section
    # Auxiliary (history/research) refresh uses the shared REFRESH_MS cadence
    assert "REFRESH_MS" in section
    # Stale-response guard
    assert "_seq" in section
    # Live poll is fast (1-5 s), never the 30s dashboard cadence for state
    assert "RETR_LIVE_MS = 2500" in section


# ===========================================================================
# 8. Engine archive_completed does not change rules
# ===========================================================================


def test_archive_completed_clears_completed_setup():
    """archive_completed must return the completed setup and clear the engine."""
    engine = RetracementBOSEngine()
    s = RetracementSetup()
    s.state = RetracementState.COMPLETED
    engine.restore_setup(s)

    archived = engine.archive_completed()
    assert archived is not None
    assert archived.state == RetracementState.COMPLETED
    assert engine.setup is None


def test_archive_completed_ignores_active():
    """archive_completed must return None for active (non-completed) setups."""
    engine = RetracementBOSEngine()
    s = RetracementSetup()
    s.state = RetracementState.TP_DYNAMIC
    engine.restore_setup(s)

    archived = engine.archive_completed()
    assert archived is None
    assert engine.setup is not None
