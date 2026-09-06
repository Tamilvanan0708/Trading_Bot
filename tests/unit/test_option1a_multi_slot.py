"""
Tests for Option 1A: Multi-Slot Parallel Execution for Fib Retracement Strategy.

Verifies:
1. RetracementMultiTFMonitor allows multiple timeframes to hold active trades simultaneously (zero Winner-Takes-All purge).
2. Paper trading sync allows parallel open positions on different timeframes without cross-blocking.
"""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Candle, MultiTimeframeSnapshot
from app.retracement.models import RetracementState
from app.retracement.multi_tf import RetracementMultiTFMonitor
from app.database.models import PaperTradeModel
from app.paper_trading.sync import sync_strategy_paper_trades

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

def _active_trade_series():
    flat, ts = _flat(30, _ts())
    zig, _ = _zigzag([100, 106, 100, 110, 104, 113, 108, 117, 112, 121, 112], ts)
    return flat + zig

@pytest.mark.asyncio
async def test_multi_slot_parallel_trade_coexistence(in_memory_db: AsyncSession):
    """Option 1A: 15m and 30m can BOTH be in TRADE_ACTIVE simultaneously."""
    mon = RetracementMultiTFMonitor(symbol="XAUUSD")
    snap = MultiTimeframeSnapshot(
        symbol="XAUUSD",
        timestamp=_ts() + timedelta(hours=10),
        current_price=112.0,
        m15=_active_trade_series(),
        m30=_active_trade_series(),
        h1=_flat(30, _ts())[0],
    )

    from app.retracement import multi_tf

    class FakeLive:
        async def get_multi_timeframe_snapshot(self, symbol, include_forming=False, m15_limit=400):
            return snap

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(multi_tf, "get_live_service", lambda: FakeLive())

    states = await mon.advance(in_memory_db)
    monkeypatch.undo()

    s15 = states["15m"]
    s30 = states["30m"]

    # Both must be active setups, neither purged or reset!
    assert s15 is not None, "15m must not be purged"
    assert s30 is not None, "30m must not be purged"
    assert s15.entry_touched is True
    assert s30.entry_touched is True
    assert s15.tp_locked is True
    assert s30.tp_locked is True
    assert s15.state == RetracementState.TRADE_ACTIVE
    assert s30.state == RetracementState.TRADE_ACTIVE
    assert s15.setup_id != s30.setup_id

@pytest.mark.asyncio
async def test_paper_trading_parallel_slot_execution(in_memory_db: AsyncSession):
    """Option 1A: An open paper trade on 15m does NOT block an entry on 30m."""
    from app.paper_trading import sync as pt_sync
    mon = RetracementMultiTFMonitor(symbol="XAUUSD")
    snap = MultiTimeframeSnapshot(
        symbol="XAUUSD",
        timestamp=_ts() + timedelta(hours=10),
        current_price=112.0,
        m15=_active_trade_series(),
        m30=_active_trade_series(),
        h1=_flat(30, _ts())[0],
    )

    class FakeLive:
        async def get_multi_timeframe_snapshot(self, symbol, include_forming=False, m15_limit=400):
            return snap

    monkeypatch = pytest.MonkeyPatch()
    from app.retracement import multi_tf
    monkeypatch.setattr(multi_tf, "get_live_service", lambda: FakeLive())
    monkeypatch.setattr(pt_sync, "get_retracement_multi_tf_service", lambda sym: mon)

    # Insert an existing OPEN trade on 15M
    existing_15m_trade = PaperTradeModel(
        signal_id="FIB_RETR_15M_L1_99",
        symbol="XAUUSD",
        direction="BUY",
        state="OPEN",
        lot_size=0.01,
        risk_amount=8.0,
        target_entry=113.1,
        actual_entry=113.1,
        stop_loss=104.69,
        take_profit_1=121.5,
        take_profit_2=121.5,
        take_profit_3=121.5,
        opened_at=datetime.now(timezone.utc),
    )
    in_memory_db.add(existing_15m_trade)
    await in_memory_db.commit()

    # Run sync_strategy_paper_trades
    await sync_strategy_paper_trades(in_memory_db, force=True)
    monkeypatch.undo()

    # Query all open paper trades: 15M should still be open, AND 30M should have opened without being blocked!
    open_trades = (await in_memory_db.execute(
        select(PaperTradeModel).where(
            PaperTradeModel.state == "OPEN",
            PaperTradeModel.signal_id.like("FIB_RETR_%"),
        )
    )).scalars().all()

    trade_tfs = set()
    for t in open_trades:
        parts = t.signal_id.split("_")
        if len(parts) >= 3:
            trade_tfs.add(parts[2].lower())

    assert "15m" in trade_tfs
    assert "30m" in trade_tfs, "30m paper trade must execute in parallel without cross-timeframe block!"
