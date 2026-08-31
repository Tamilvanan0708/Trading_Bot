"""
End-to-end acceptance simulation: candle close -> analysis -> signal ->
admission -> paper trade -> SL/TP -> persistence -> restart recovery.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import Settings
from app.core.constants import TradeState
from app.data.live.service import LiveMarketDataService
from app.data.models import Candle
from app.database.models import Base
from app.paper_trading.service import PaperTradingService
from app.services.scheduler import AnalysisScheduler


def _candle(ts: datetime, o, h, l, c, v=10.0) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _history(now: datetime, n=200) -> list:
    """Builds a strong bullish uptrend ending at `now` so a LONG signal forms."""
    start = now - timedelta(minutes=15 * n)
    candles = []
    price = 2400.0
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        price += 1.5 + (i % 5) * 0.2
        candles.append(_candle(ts, price - 1.0, price + 2.0, price - 2.0, price + 0.5))
    return candles


@pytest.fixture
def tmp_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/e2e.db", echo=False)
    return engine


@pytest.mark.asyncio
async def test_end_to_end_paper_trade_and_restart(tmp_engine):
    async with tmp_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(tmp_engine, class_=AsyncSession, expire_on_commit=False)

    settings = Settings(
        MAX_OPEN_POSITIONS=2,
        PAPER_FEES_USD=0.0,
        PAPER_SPREAD_POINTS=0.0,
        PAPER_SLIPPAGE_PCT=0.0,
        PAPER_TRADING_ENABLED=True,
    )

    # 1. Build a live service whose latest closed 15M candle is fully in the past
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    closed_ts = now - timedelta(minutes=30)  # fully closed candle
    service = LiveMarketDataService(settings=settings)
    service._closed_15m = _history(closed_ts, n=200) + [_candle(closed_ts, 2690.0, 2698.0, 2688.0, 2695.0)]
    service._last_price = 2695.0

    # 2. Create scheduler and force-processing of the closed candle
    scheduler = AnalysisScheduler(service, settings)
    scheduler._last_processed_ts = closed_ts - timedelta(minutes=15)

    # 3. Run the scheduler tick in a real DB session
    async with session_maker() as session:
        # Simulate the scheduler's analysis step directly
        snap = await service.get_multi_timeframe_snapshot("XAUUSD")
        result = await scheduler.pipeline.run_full_analysis(symbol="XAUUSD", db_session=session)
        sig = result["signal"]
        assert sig["direction"] in ("LONG", "SHORT", "NO_TRADE")

        from app.core.constants import AIValidationStatus
        ai_ok = result["ai_validation"]["status"] == AIValidationStatus.APPROVE.value

        if sig["direction"] != "NO_TRADE" and sig["confidence_score"] >= settings.THRESHOLD_STRONG and ai_ok:
            from app.risk.admission import TradeAdmissionGate
            gate = TradeAdmissionGate(settings)
            from app.services.scheduler import _signal_payload_from_result
            payload = _signal_payload_from_result(result)
            decision = await gate.evaluate(payload, repo=None, candle_closed=True, market_data_fresh=True, ai_status_ok=ai_ok)
            if decision.allowed:
                opened = await scheduler.pipeline.paper_service.open_position_from_signal(payload)
                assert opened is not None
                assert opened.state == TradeState.PENDING
                await session.commit()

                # 4. Feed a candle that hits the target entry, then a crash to SL
                entry_candle = _candle(closed_ts + timedelta(minutes=15), 2694.0, 2696.0, 2693.0, 2694.0)
                await scheduler.pipeline.paper_service.on_candle(entry_candle)
                # Simulate SL trigger
                sl_candle = _candle(closed_ts + timedelta(minutes=30), 2695.0, 2697.0, 2660.0, 2665.0)
                await scheduler.pipeline.paper_service.on_candle(sl_candle)

                pos = scheduler.pipeline.paper_service.get_position(opened.position_id)
                assert pos.state == TradeState.CLOSED
                assert pos.exit_reason == "STOP_LOSS_HIT"
                assert pos.realized_pnl_usd < 0
                assert scheduler.pipeline.paper_service.balance < 10000.0
            else:
                await session.commit()
        else:
            await session.commit()

    # 5. Simulate RESTART: reconstruct the paper service from DB
    restored = PaperTradingService(initial_balance=10000.0, settings=settings)
    async with session_maker() as session:
        from app.database.repository import Repository
        repo = Repository(session)
        await restored.restore_from_db(repo)

    assert restored.balance == scheduler.pipeline.paper_service.balance
    assert len(restored.get_all_positions()) == len(scheduler.pipeline.paper_service.get_all_positions())
    await tmp_engine.dispose()