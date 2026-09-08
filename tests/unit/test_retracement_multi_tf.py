"""
RETRACEMENT_BOS_V1 — Multi-timeframe live monitor tests (15m / 30m / 1h).

Verifies the agent continuously monitors ALL THREE timeframes INDEPENDENTLY:

  1. three independent states coexist simultaneously
     (15m WAITING_FOR_ENTRY, 30m ENTRY_TOUCHED/TP_LOCKED, 1h NO_SETUP)
  2. a setup on one timeframe never overwrites another timeframe's setup
  3. a new valid high updates ONLY that timeframe's dynamic TP
  4. persistence is isolated per timeframe
  5. the RETRACEMENT SIGNAL endpoint returns complete details for all three
  6. a timeframe keeps scanning for the next setup after one completes
"""

import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Candle, DataQualityStatus, MultiTimeframeSnapshot
from app.retracement.models import RetracementState
from app.retracement.repository import RetracementRepository

_DT = timedelta(minutes=15)


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


# ---------------------------------------------------------------------------
# Known-state series per timeframe
# ---------------------------------------------------------------------------


def _m15_waiting_series():
    """15m: ends TP_DYNAMIC (WAITING_FOR_ENTRY)."""
    flat, ts = _flat(30, _ts())
    zig, _ = _zigzag([100, 106, 100, 110, 108, 113, 111, 117, 115, 121, 119, 125], ts)
    return flat + zig


def _m30_trade_active_series():
    """30m: ends TRADE_ACTIVE (ENTRY_TOUCHED -> TP_LOCKED)."""
    flat, ts = _flat(30, _ts())
    zig, _ = _zigzag([100, 106, 100, 110, 104, 113, 108, 117, 112, 121, 112], ts)
    return flat + zig


def _h1_no_setup_series():
    """1h: flat -> NO_SETUP."""
    flat, _ = _flat(30, _ts())
    return flat


def _snapshot(m15=None, m30=None, h1=None, price=None):
    if m15 is None:
        m15, _ = _flat(30, _ts())
    if m30 is None:
        m30, _ = _flat(30, _ts())
    if h1 is None:
        h1, _ = _flat(30, _ts())
    return MultiTimeframeSnapshot(
        symbol="XAUUSD",
        timestamp=m15[-1].timestamp,
        current_price=price if price is not None else m15[-1].close,
        m15=m15,
        m30=m30,
        h1=h1,
    )


def _patch_live(monkeypatch, snap, price=None):
    """Patch get_live_service in both the multi_tf module and the API route."""
    import app.api.routes.retracement as retr_route
    from app import retracement  # noqa: F401
    from app.retracement import multi_tf

    class FakeLive:
        async def get_multi_timeframe_snapshot(self, symbol, include_forming=False, m15_limit=400):
            return snap

        async def get_latest_price(self, symbol):
            return price if price is not None else snap.current_price

        async def data_quality(self):
            return DataQualityStatus(connected=True, degraded=False, candle_count=len(snap.m15))

    fake = FakeLive()
    monkeypatch.setattr(multi_tf, "get_live_service", lambda: fake)
    monkeypatch.setattr(retr_route, "get_live_service", lambda: fake)
    return fake


def _run_engines(series_by_tf):
    """Build a monitor and advance each slot over its full series once."""
    from app.retracement.multi_tf import RetracementMultiTFMonitor

    mon = RetracementMultiTFMonitor(symbol="XAUUSD", timeframes=["15m", "30m", "1h"])
    for tf, series in series_by_tf.items():
        slot = mon.slots[tf]
        candles = series
        for candle in candles:
            slot.engine.process_candle(candle)
            slot.engine.archive_completed()
        slot.last_processed_ts = candles[-1].timestamp
        slot.has_live_data = True
    return mon


# ===========================================================================
# 1. Three timeframes monitored independently — states coexist
# ===========================================================================

def test_three_timeframes_states_coexist_independently():
    """15m WAITING_FOR_ENTRY, 30m ENTRY_TOUCHED/TP_LOCKED, 1h NO_SETUP must
    all exist simultaneously and be fully independent."""
    mon = _run_engines({
        "15m": _m15_waiting_series(),
        "30m": _m30_trade_active_series(),
        "1h": _h1_no_setup_series(),
    })

    s15 = mon.slots["15m"].engine.setup
    s30 = mon.slots["30m"].engine.setup
    s1h = mon.slots["1h"].engine.setup

    # 15m -> WAITING_FOR_ENTRY (TP_DYNAMIC, not touched)
    assert s15 is not None
    assert s15.state == RetracementState.TP_DYNAMIC
    assert s15.entry_touched is False
    assert s15.tp_locked is False
    assert s15.timeframe == "15m"

    # 30m -> ENTRY_TOUCHED / TP_LOCKED (TRADE_ACTIVE)
    assert s30 is not None
    assert s30.entry_touched is True
    assert s30.tp_locked is True
    assert s30.locked_tp is not None
    assert s30.state in (RetracementState.TRADE_ACTIVE, RetracementState.ENTRY_TOUCHED,
                         RetracementState.TP_FROZEN)
    assert s30.timeframe == "30m"

    # 1h -> NO_SETUP
    assert s1h is None

    # Every setup is a DISTINCT object with its own setup_id (no sharing).
    ids = [s15.setup_id, s30.setup_id]
    assert len(set(ids)) == 2


# ===========================================================================
# 2. Timeframe isolation — no overwrite across timeframes
# ===========================================================================

@pytest.mark.asyncio
async def test_timeframe_isolation_no_overwrite(in_memory_db: AsyncSession):
    """A setup detected on 15m must NEVER overwrite the 30m or 1h setup."""
    from app.retracement.multi_tf import RetracementMultiTFMonitor

    mon = RetracementMultiTFMonitor(symbol="XAUUSD", timeframes=["15m", "30m", "1h"])
    snap = _snapshot(
        m15=_m15_waiting_series(),
        m30=_m30_trade_active_series(),
        h1=_h1_no_setup_series(),
    )

    # Patch only the multi_tf get_live_service (the monitor's data source).
    from app.retracement import multi_tf

    class FakeLive:
        async def get_multi_timeframe_snapshot(self, symbol, include_forming=False, m15_limit=400):
            return snap

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(multi_tf, "get_live_service", lambda: FakeLive())

    states = await mon.advance(in_memory_db)
    monkeypatch.undo()

    assert states["15m"] is not None
    assert states["30m"] is not None
    assert states["1h"] is None

    # 15m and 30m setups are distinct, each with its own setup_id and state.
    s15 = states["15m"]
    s30 = states["30m"]
    assert s15.setup_id != s30.setup_id
    assert s15.entry_touched is False       # 15m untouched
    assert s30.entry_touched is True        # 30m touched — NOT overwritten by 15m

    # Persisted rows are isolated per timeframe.
    repo = RetracementRepository(in_memory_db)
    p15 = await repo.load_latest_active("XAUUSD", strategy="RETRACEMENT_BOS_V1", timeframe="15m")
    p30 = await repo.load_latest_active("XAUUSD", strategy="RETRACEMENT_BOS_V1", timeframe="30m")
    assert p15 is not None and p15.setup_id == s15.setup_id
    assert p30 is not None and p30.setup_id == s30.setup_id
    assert p15.entry_touched is False
    assert p30.entry_touched is True


# ===========================================================================
# 3. New valid high updates ONLY that timeframe's dynamic TP
# ===========================================================================

@pytest.mark.asyncio
async def test_new_high_updates_only_that_timeframe(in_memory_db: AsyncSession):
    """A new valid high on 15m updates the 15M dynamic TP; 30m/1h unchanged."""
    from app.retracement.multi_tf import RetracementMultiTFMonitor

    mon = RetracementMultiTFMonitor(symbol="XAUUSD", timeframes=["15m", "30m", "1h"])
    snap1 = _snapshot(
        m15=_m15_waiting_series(),
        m30=_m30_trade_active_series(),
        h1=_h1_no_setup_series(),
    )

    from app.retracement import multi_tf

    calls = {"n": 0}

    class FakeLive:
        async def get_multi_timeframe_snapshot(self, symbol, include_forming=False, m15_limit=400):
            calls["n"] += 1
            if calls["n"] == 1:
                return snap1
            # Second call: 15m gets a higher leg (TP update), others unchanged.
            m15 = _m15_waiting_series()
            last = m15[-1].timestamp
            leg, _ = _leg(last, m15[-1].close, 138.0)
            confirm = [
                _bar(leg[-1].timestamp + _DT, 138.0, 138.5, 137.0, 137.5),
                _bar(leg[-1].timestamp + 2 * _DT, 137.5, 137.8, 136.5, 137.0),
                _bar(leg[-1].timestamp + 3 * _DT, 137.0, 137.3, 136.2, 136.8),
            ]
            m15 = m15 + leg + confirm
            return _snapshot(
                m15=m15,
                m30=_m30_trade_active_series(),
                h1=_h1_no_setup_series(),
            )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(multi_tf, "get_live_service", lambda: FakeLive())

    states1 = await mon.advance(in_memory_db)
    tp15_before = states1["15m"].dynamic_tp
    tp30_before = states1["30m"].dynamic_tp if states1["30m"] else None

    states2 = await mon.advance(in_memory_db)
    monkeypatch.undo()

    assert states2["15m"] is not None
    assert states2["15m"].dynamic_tp > tp15_before, "15m TP must follow the new 15m high"
    assert states2["15m"].entry_touched is False
    # 30m / 1h untouched by the 15m high
    assert states2["30m"] is not None
    assert states2["30m"].dynamic_tp == tp30_before
    assert states2["1h"] is None


# ===========================================================================
# 4. Persistence isolation per timeframe (restart-safe)
# ===========================================================================

@pytest.mark.asyncio
async def test_persistence_isolated_per_timeframe(in_memory_db: AsyncSession):
    """Each timeframe keeps its OWN active persisted row; loading one timeframe
    never returns another's setup."""
    from app.retracement.multi_tf import RetracementMultiTFMonitor

    mon = RetracementMultiTFMonitor(symbol="XAUUSD", timeframes=["15m", "30m", "1h"])
    snap = _snapshot(
        m15=_m15_waiting_series(),
        m30=_m30_trade_active_series(),
        h1=_h1_no_setup_series(),
    )

    from app.retracement import multi_tf

    class FakeLive:
        async def get_multi_timeframe_snapshot(self, symbol, include_forming=False, m15_limit=400):
            return snap

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(multi_tf, "get_live_service", lambda: FakeLive())

    states = await mon.advance(in_memory_db)
    monkeypatch.undo()

    repo = RetracementRepository(in_memory_db)
    # 15m row exists and is the 15m setup.
    p15 = await repo.load_latest_active("XAUUSD", strategy="RETRACEMENT_BOS_V1", timeframe="15m")
    assert p15 is not None and p15.setup_id == states["15m"].setup_id
    assert p15.timeframe == "15m"
    # 30m row exists and is the 30m setup.
    p30 = await repo.load_latest_active("XAUUSD", strategy="RETRACEMENT_BOS_V1", timeframe="30m")
    assert p30 is not None and p30.setup_id == states["30m"].setup_id
    assert p30.timeframe == "30m"
    # The 15m and 30m active rows are distinct.
    assert p15.setup_id != p30.setup_id
    # Without a timeframe filter the queries stay isolated by column value.
    assert p15.timeframe == "15m" and p30.timeframe == "30m"


# ===========================================================================
# 5. RETRACEMENT SIGNAL endpoint returns all three timeframes
# ===========================================================================

@pytest.mark.asyncio
async def test_multi_api_endpoint_returns_all_timeframes(in_memory_db: AsyncSession):
    """GET /retracement/multi/XAUUSD must return complete details for 15m/30m/1h,
    each clearly identified by its timeframe label."""
    from app.api.app import create_app
    from app.api.routes import retracement as retr_route

    snap = _snapshot(
        m15=_m15_waiting_series(),
        m30=_m30_trade_active_series(),
        h1=_h1_no_setup_series(),
    )
    _patch_live(pytest.MonkeyPatch(), snap, price=snap.m15[-1].close)

    # Reset the singleton so the test starts clean.
    from app.retracement import multi_tf
    from app.retracement.multi_tf import RetracementMultiTFMonitor
    multi_tf._instances["XAUUSD"] = RetracementMultiTFMonitor("XAUUSD", timeframes=["15m", "30m", "1h"])

    async def fake_db_session():
        yield in_memory_db

    app = create_app()
    app.dependency_overrides[retr_route.get_db_session] = fake_db_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/retracement/multi/XAUUSD")
            assert resp.status_code == 200
            data = resp.json()
            assert data["strategy"] == "RETRACEMENT_BOS_V1"
            assert {"15m", "30m", "1h"}.issubset(set(data["timeframes"].keys()))

            tf15 = data["timeframes"]["15m"]
            tf30 = data["timeframes"]["30m"]
            tf1h = data["timeframes"]["1h"]

            # Every response identifies its timeframe.
            assert tf15["timeframe"] == "15m"
            assert tf30["timeframe"] == "30m"
            assert tf1h["timeframe"] == "1h"

            # 15m: WAITING_FOR_ENTRY with a full Fibonacci structure.
            assert tf15["state"] == "TP_DYNAMIC"
            assert tf15["entry"]["touched"] is False
            assert "0.000" in tf15["levels"]
            assert "0.618" in tf15["levels"]
            assert "1.000" in tf15["levels"]

            # 30m: ENTRY_TOUCHED / TP LOCKED.
            assert tf30["entry"]["touched"] is True
            assert tf30["tp"]["is_locked"] is True
            assert tf30["tp"]["locked"] is not None

            # 1h: NO_SETUP (no fabricated levels).
            assert tf1h["state"] == "NO_SETUP"
            assert tf1h["levels"] == {}
    finally:
        app.dependency_overrides.clear()


# ===========================================================================
# 6. Continuous scanning after completion
# ===========================================================================

def test_timeframe_keeps_scanning_after_completion():
    """After a 15m setup completes, the 15m slot immediately scans for the next
    valid BOS + retracement (a new setup forms on continued candles)."""
    from app.retracement.multi_tf import RetracementMultiTFMonitor

    # A series that completes the first setup (TP_HIT) and then forms a second.
    flat, ts = _flat(30, _ts())
    zig, ts = _zigzag([100, 106, 100, 110, 108, 113, 111, 117, 115, 121,
                       119, 125, 114, 130], ts)
    # Append another oscillation so a fresh BOS can form after the first setup.
    more, _ = _zigzag([130, 126, 134, 131, 140], ts)
    series = flat + zig + more

    mon = RetracementMultiTFMonitor(symbol="XAUUSD", timeframes=["15m", "30m", "1h"])
    slot = mon.slots["15m"]
    archived = []
    for candle in series:
        slot.engine.process_candle(candle)
        archived_setup = slot.engine.archive_completed()
        if archived_setup is not None:
            archived.append(archived_setup)

    # The 15m slot must have detected a second setup after the first completed.
    current = slot.engine.setup
    assert current is not None, "15m must keep scanning after the first setup completed"
    assert current.point_2_price is not None
    # The completed setup was archived (engine moved on) — at least one completed outcome.
    assert any(a.outcome in ("TP_HIT", "SL_HIT") for a in archived)
    # The new current setup is distinct from every archived one.
    assert all(a.setup_id != current.setup_id for a in archived)


# ===========================================================================
# 7. RETRACEMENT SIGNAL navigation — frontend consumes the multi endpoint
# ===========================================================================

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_API_JS = os.path.join(_ROOT, "app/static/terminal/js/api.js")
_APP_JS = os.path.join(_ROOT, "app/static/terminal/js/app.js")


def _retr_section():
    with open(_APP_JS, encoding="utf-8") as f:
        js = f.read()
    return js[js.find('Routes["/retracement"]'):js.find("function renderAnalysis")]


def test_api_js_exposes_retracement_multi():
    """api.js must expose a retracementMulti method hitting the multi endpoint."""
    with open(_API_JS, encoding="utf-8") as f:
        js = f.read()
    assert "retracementMulti: (sym" in js
    assert "/retracement/multi/${sym}" in js


def test_app_js_renders_multi_timeframe_signal():
    """The RETRACEMENT SIGNAL navigation must render all three timeframes
    independently, each clearly labelled 15M/30M/1H."""
    section = _retr_section()
    # Multi-timeframe signal section
    assert "RETRACEMENT SIGNAL" in section
    assert "monitored independently" in section
    # All three timeframes always visible
    assert "15M" in section and "30M" in section and "1H" in section
    # Per-timeframe states rendered
    assert "NO VALID SETUP" in section
    assert "WAITING FOR ENTRY" in section
    assert "ENTRY TOUCHED" in section
    assert "TP LOCKED" in section
    # Complete setup details per signal
    assert "POINT 2 (0.000)" in section
    assert "ENTRY → TP" in section
    assert "ENTRY → SL" in section
    assert "TOTAL RANGE" in section
    assert "BULLISH RETRACEMENT" in section


def test_multi_section_has_no_monetary_pnl():
    """The multi-timeframe signal section must stay points-only (no '$', P&L)."""
    import re

    section = _retr_section()
    monetary = re.findall(r"\$(?!\{)", section)
    assert not monetary, f"monetary $ found: {monetary}"
    assert "P&L" not in section
    assert "PTS" in section


def test_new_high_bar_does_not_fill_pullback_on_same_bar():
    """A candle that pushes to a new expansion high must not use its own low
    (which occurred before the push) to trigger a premature retracement fill."""
    from app.retracement.dual_engine import DualRetracementEngine

    series = _m15_waiting_series()
    engine = DualRetracementEngine("XAUUSD", "15m")
    for c in series:
        engine.process_candle(c)

    setup = engine.setup
    assert setup is not None and setup.state == RetracementState.TP_DYNAMIC

    # Push bar with a new high (135.0 > 125.5) and low at 120.0 (below 0.618 level 121.3, but above SL 117.1)
    last_ts = series[-1].timestamp
    push = _bar(last_ts + _DT, 125.0, 135.0, 120.0, 133.0)
    engine.process_candle(push)

    # Must remain in TP_DYNAMIC with zero layers filled because this bar set the new high
    assert engine.setup.state == RetracementState.TP_DYNAMIC
    assert len(engine.setup.layers) == 0
    assert engine.setup.entry_touched is False
