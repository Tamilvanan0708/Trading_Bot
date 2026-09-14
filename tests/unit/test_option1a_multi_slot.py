"""
Tests for Option 1A: Multi-Slot Parallel Execution for Fib Retracement Strategy.

Verifies:
1. RetracementMultiTFMonitor allows multiple timeframes to hold active trades simultaneously (zero Winner-Takes-All purge).
2. Paper trading sync allows parallel open positions on different timeframes without cross-blocking.
"""

from datetime import datetime, timedelta, timezone
import uuid
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

    # Insert an existing OPEN trade on 15M with its own distinct setup
    existing_15m_trade = PaperTradeModel(
        signal_id="FIB_RETR_15M_L1_90",
        symbol="XAUUSD",
        direction="BUY",
        state="OPEN",
        lot_size=0.01,
        risk_amount=8.0,
        target_entry=108.0,
        actual_entry=108.0,
        stop_loss=100.0,
        take_profit_1=115.0,
        take_profit_2=115.0,
        take_profit_3=115.0,
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


@pytest.mark.asyncio
async def test_paper_trading_cross_timeframe_duplicate_skipped(in_memory_db: AsyncSession):
    """Smart Setup Deduplication: If 1H already has an open trade with identical entry & SL, 30M duplicate is skipped."""
    from app.paper_trading import sync as pt_sync
    mon = RetracementMultiTFMonitor(symbol="XAUUSD")
    snap = MultiTimeframeSnapshot(
        symbol="XAUUSD",
        timestamp=_ts() + timedelta(hours=10),
        current_price=112.0,
        m15=_flat(30, _ts())[0],
        m30=_active_trade_series(),
        h1=_flat(30, _ts())[0],
    )

    class FakeLive:
        async def get_multi_timeframe_snapshot(self, symbol, include_forming=False, m15_limit=400):
            return snap

    monkeypatch = pytest.MonkeyPatch()
    from app.config.execution_settings import ExecutionSettings
    from app.retracement import multi_tf
    monkeypatch.setattr(multi_tf, "get_live_service", lambda: FakeLive())
    monkeypatch.setattr(pt_sync, "get_retracement_multi_tf_service", lambda sym: mon)
    monkeypatch.setattr(pt_sync, "get_execution_settings", lambda: ExecutionSettings(cross_tf_dedup_enabled=True))

    # Insert existing OPEN trades on 1H with identical entry & SL
    for l_id, l_entry in [("L1", 116.15), ("L2", 114.50), ("L3", 112.85)]:
        in_memory_db.add(PaperTradeModel(
            signal_id=f"FIB_RETR_1H_{l_id}_107",
            symbol="XAUUSD",
            direction="BUY",
            state="OPEN",
            lot_size=0.01,
            risk_amount=8.0,
            target_entry=l_entry,
            actual_entry=l_entry,
            stop_loss=110.42,
            take_profit_1=121.5,
            take_profit_2=121.5,
            take_profit_3=121.5,
            opened_at=datetime.now(timezone.utc),
        ))
    await in_memory_db.commit()

    # Run sync_strategy_paper_trades
    await sync_strategy_paper_trades(in_memory_db, force=True)
    monkeypatch.undo()

    # Query all open paper trades: 30M MUST be skipped due to identical setup de-duplication!
    open_30m_trades = (await in_memory_db.execute(
        select(PaperTradeModel).where(
            PaperTradeModel.state == "OPEN",
            PaperTradeModel.signal_id.like("FIB_RETR_30M_%"),
        )
    )).scalars().all()

    assert len(open_30m_trades) == 0, "30m duplicate trade must be skipped when 1H has identical setup!"


@pytest.mark.asyncio
async def test_paper_trading_timestamped_anchor_avoids_collision(in_memory_db: AsyncSession):
    import app.paper_trading.sync as pt_sync

    # Simulate an OLD closed trade from yesterday with anchor price 4418
    old_trade = PaperTradeModel(
        signal_id="FIB_RETR_5M_L1_4418",
        symbol="XAUUSD",
        direction="BUY",
        state="CLOSED",
        lot_size=0.01,
        risk_amount=8.0,
        target_entry=4420.0,
        actual_entry=4420.0,
        stop_loss=4415.0,
        take_profit_1=4430.0,
        take_profit_2=4430.0,
        take_profit_3=4430.0,
        opened_at=datetime(2026, 9, 7, 18, 15, tzinfo=timezone.utc),
        closed_at=datetime(2026, 9, 7, 19, 0, tzinfo=timezone.utc),
        exit_price=4430.0,
        exit_reason="TP_HIT",
    )
    in_memory_db.add(old_trade)
    await in_memory_db.commit()

    # Now simulate today's monitor returning 5M active setup with anchor price 4418 but fresh timestamp
    p2_time = datetime(2026, 9, 8, 2, 5, tzinfo=timezone.utc)
    from app.retracement.models import RetracementSetup
    fresh_state = RetracementSetup(
        direction="LONG",
        point_1_price=4425.0,
        point_2_price=4418.0,
        point_2_timestamp=p2_time,
        fib_0_618=4421.0,
        fib_0_500=4420.0,
        fib_0_382=4419.0,
        fib_0_236=4416.0,
        fib_1_000=4426.0,
        layers={
            "L1": {"state": "FILLED", "entry_price": 4421.0, "tp": 4426.0},
            "L2": {"state": "FILLED", "entry_price": 4420.0, "tp": 4426.0},
            "L3": {"state": "FILLED", "entry_price": 4419.0, "tp": 4426.0},
        }
    )

    class FakeMonitor:
        timeframes = ["5m"]
        async def advance(self, db):
            return {"5m": fresh_state}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pt_sync, "get_retracement_multi_tf_service", lambda sym: FakeMonitor())

    await sync_strategy_paper_trades(in_memory_db, force=True)
    monkeypatch.undo()

    # Query all open trades: L1 MUST be executed and not skipped!
    open_trades = (await in_memory_db.execute(
        select(PaperTradeModel).where(
            PaperTradeModel.state == "OPEN",
            PaperTradeModel.signal_id.like("FIB_RETR_5M_%"),
        )
    )).scalars().all()

    open_layers = {t.signal_id.split("_")[3] for t in open_trades}
    assert "L1" in open_layers, "L1 must be opened and not skipped due to yesterday's 4418 trade!"
    assert "L2" in open_layers
    assert "L3" in open_layers


@pytest.mark.asyncio
async def test_cross_timeframe_duplicate_blocked_with_matching_sl_tp(in_memory_db: AsyncSession):
    """Verify that a 15M trade with 2.58 pt entry diff is blocked when SL and TP match an existing 5M trade."""
    import app.paper_trading.sync as pt_sync
    from app.retracement.models import RetracementSetup

    # Existing 5M open trade
    in_memory_db.add(PaperTradeModel(
        id=str(uuid.uuid4()),
        signal_id="FIB_RETR_5M_L1_4430",
        symbol="XAUUSD",
        direction="SHORT",
        state="OPEN",
        lot_size=0.05,
        risk_amount=100.0,
        target_entry=4399.07,
        actual_entry=4399.07,
        stop_loss=4419.81,
        take_profit_1=4379.46,
        take_profit_2=4379.46,
        take_profit_3=4379.46,
        opened_at=datetime.now(timezone.utc),
    ))
    await in_memory_db.commit()

    # 15M setup with entry 4401.65 (2.58 pt diff) but identical SL and TP
    p2_time = datetime(2026, 9, 9, 21, 15, tzinfo=timezone.utc)
    setup_15m = RetracementSetup(
        direction="SHORT",
        point_1_price=4414.0,
        point_2_price=4430.0,
        point_2_timestamp=p2_time,
        sl_price=4419.81,
        fib_0_236=4419.81,
        fib_0_618=4401.65,
        fib_0_500=4407.0,
        fib_0_382=4412.0,
        fib_1_000=4379.46,
        layers={
            "L1": {"state": "FILLED", "entry_price": 4401.65, "tp": 4379.46, "sl": 4419.81},
        },
    )

    class FakeMonitor:
        timeframes = ["15m"]
        async def advance(self, db):
            return {"15m": setup_15m}

    monkeypatch = pytest.MonkeyPatch()
    from app.config.execution_settings import ExecutionSettings
    monkeypatch.setattr(pt_sync, "get_retracement_multi_tf_service", lambda sym: FakeMonitor())
    monkeypatch.setattr(pt_sync, "get_execution_settings", lambda: ExecutionSettings(cross_tf_dedup_enabled=True))

    await sync_strategy_paper_trades(in_memory_db, force=True)
    monkeypatch.undo()

    # 15M duplicate must be blocked!
    trades_15m = (await in_memory_db.execute(
        select(PaperTradeModel).where(
            PaperTradeModel.signal_id.like("FIB_RETR_15M_%"),
        )
    )).scalars().all()
    assert len(trades_15m) == 0, "15M duplicate trade must be blocked by cross-timeframe deduplication filter!"


@pytest.mark.asyncio
async def test_fast_tp_layer_captured_and_records_profit(in_memory_db: AsyncSession):
    """Verify that a layer that resolved to TP_HIT within one cycle is recorded as CLOSED with profit."""
    import app.paper_trading.sync as pt_sync
    from app.retracement.models import RetracementSetup

    p2_time = datetime(2026, 9, 9, 21, 20, tzinfo=timezone.utc)
    setup_with_tp = RetracementSetup(
        direction="SHORT",
        point_1_price=4414.0,
        point_2_price=4430.0,
        point_2_timestamp=p2_time,
        sl_price=4420.0,
        fib_0_236=4420.0,
        fib_0_618=4402.0,
        fib_0_500=4407.0,
        fib_0_382=4412.0,
        fib_1_000=4385.0,
        layers={
            "L2": {"state": "TP_HIT", "entry_price": 4407.0, "tp": 4402.0, "sl": 4420.0},
        },
    )

    class FakeMonitor:
        timeframes = ["5m"]
        async def advance(self, db):
            return {"5m": setup_with_tp}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pt_sync, "get_retracement_multi_tf_service", lambda sym: FakeMonitor())

    await sync_strategy_paper_trades(in_memory_db, force=True)
    monkeypatch.undo()

    # L2 must be recorded in paper trades as CLOSED with exit_reason TP_HIT and positive realized_pnl!
    l2_trades = (await in_memory_db.execute(
        select(PaperTradeModel).where(
            PaperTradeModel.signal_id.like("FIB_RETR_5M_L2_%"),
        )
    )).scalars().all()
    assert len(l2_trades) == 1, "L2 fast TP trade must be created in paper trades table!"
    t = l2_trades[0]
    assert t.state == "CLOSED"
    assert t.exit_reason == "TP_HIT"
    assert t.exit_price == 4402.0
    assert t.actual_entry == 4407.0
    assert t.realized_pnl is not None and t.realized_pnl > 0.0, f"Expected positive realized PnL, got {t.realized_pnl}"


@pytest.mark.asyncio
async def test_paper_trading_degraded_data_quality_does_not_block_fib_retracement(in_memory_db: AsyncSession):
    """Verifies that transient data quality degradation (e.g. startup / weekend gap) does NOT block valid Fib Retracement paper trades."""
    from app.data.live.service import DataQualityStatus
    from app.paper_trading import sync as pt_sync
    from app.retracement.models import RetracementSetup, RetracementState

    p2_time = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    setup = RetracementSetup(
        setup_id="test_dq_unblock_1h",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        timeframe="1h",
        state=RetracementState.TRADE_ACTIVE,
        direction="LONG",
        point_1_price=4365.0,
        point_2_price=4306.0,
        point_2_timestamp=p2_time,
        sl_price=4329.82,
        fib_0_236=4329.82,
        fib_0_618=4368.35,
        fib_0_500=4356.45,
        fib_0_382=4344.55,
        fib_1_000=4406.88,
        layers={
            "L1": {"state": "FILLED", "entry_price": 4368.35, "tp": 4406.88, "sl": 4329.82},
        },
    )

    class FakeMonitor:
        timeframes = ["1h"]
        slots = {"1h": type("Slot", (), {"live_price": 4350.0})()}
        live_price = 4350.0
        async def advance(self, db):
            return {"1h": setup}

    class FakeLiveService:
        _running = True
        _startup_task = None
        async def data_quality(self):
            return DataQualityStatus(
                provider="binance",
                degraded=True,
                degradation_reason="Historical data stale (weekend gap)",
            )
        async def get_latest_price(self, sym):
            return 4350.0

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pt_sync, "get_retracement_multi_tf_service", lambda sym: FakeMonitor())
    monkeypatch.setattr(pt_sync, "get_live_service", lambda: FakeLiveService())

    await sync_strategy_paper_trades(in_memory_db, force=True)
    monkeypatch.undo()

    # The trade MUST be open in paper trades despite degraded data quality!
    open_trades = (await in_memory_db.execute(
        select(PaperTradeModel).where(
            PaperTradeModel.signal_id.like("FIB_RETR_1H_L1_%"),
            PaperTradeModel.state == "OPEN",
        )
    )).scalars().all()
    assert len(open_trades) == 1, "Fib Retracement trade must successfully open even when Data Quality is degraded!"
    trade = open_trades[0]
    assert trade.actual_entry == 4368.35
    assert trade.stop_loss == 4329.82
    assert trade.take_profit_1 == 4406.88

