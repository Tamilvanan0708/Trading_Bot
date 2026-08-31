"""
Runtime validation harness for LIVE PAPER-TRADING.

Drives the real runtime objects (LiveMarketDataService, AnalysisScheduler,
AnalysisPipeline, PaperTradingService, SQLite repository) through the full
loop: tick ingestion -> candle close -> scheduler -> analysis -> admission
-> paper trade -> persistence -> SL/TP management.

Usage:
  python scripts/runtime_validation.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import Settings
from app.core.constants import SignalDirection
from app.data.live.service import LiveMarketDataService
from app.data.models import Candle
from app.database.models import Base
from app.paper_trading.service import PaperTradingService
from app.services.scheduler import AnalysisScheduler


def _candle(ts, o, h, l, c, v=10.0):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _history(now, n=200, base=4600.0):
    """Strong bullish uptrend ending at `now` so a LONG signal can form."""
    start = now - timedelta(minutes=15 * n)
    candles = []
    price = base
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        price += 2.0 + (i % 4) * 0.3
        candles.append(_candle(ts, price - 1.0, price + 2.0, price - 2.0, price + 0.5))
    return candles


async def main() -> None:
    db_path = "data/runtime_validation.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    settings = Settings(
        MAX_OPEN_POSITIONS=2,
        MAX_DAILY_TRADES=20,
        PAPER_FEES_USD=0.0,
        PAPER_SPREAD_POINTS=0.0,
        PAPER_SLIPPAGE_PCT=0.0,
        PROCESS_LAST_CLOSED_ON_START=True,
        ACCOUNT_BALANCE=10000.0,
    )

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    service = LiveMarketDataService(settings=settings)
    # History ends 75 minutes ago so the latest live candle is fully closed
    closed_ts = now - timedelta(minutes=75)
    service._closed_15m = _history(closed_ts, n=220)
    service._last_price = service._closed_15m[-1].close

    print("=" * 64)
    print("RUNTIME PAPER-TRADING VALIDATION")
    print("=" * 64)

    # 1. Ingestion: push ticks that form and close a candle at `closed_ts` bucket
    print("\n[1] TICK INGESTION -> CANDLE CLOSE")
    bucket_a = closed_ts
    bucket_b = closed_ts + timedelta(minutes=15)
    for i in range(30):
        await service.on_tick(_tick_data(bucket_a, price=4620.0 + i * 0.2))
    # Finalize bucket_a by pushing one tick in bucket_b
    await service.on_tick(_tick_data(bucket_b, price=4623.0))

    snap = await service.get_multi_timeframe_snapshot("XAUUSD")
    last_closed = snap.m15[-1]
    print(f"  last closed 15M candle: {last_closed.timestamp} close={last_closed.close}")
    print(f"  current_price: {snap.current_price}")
    print(f"  candles: m15={len(snap.m15)} m30={len(snap.m30)} h1={len(snap.h1)} h4={len(snap.h4)}")

    # 2. Scheduler: process the closed candle (simulates the automatic loop)
    print("\n[2] SCHEDULER -> ANALYSIS")
    scheduler = AnalysisScheduler(service, settings)
    # Simulate one scheduler poll cycle that sees the closed candle
    await scheduler._tick()
    snap = await service.get_multi_timeframe_snapshot("XAUUSD")
    last_closed = snap.m15[-1]
    # If it was skipped (initial), force process
    if scheduler._last_processed_ts is None or scheduler._last_processed_ts < last_closed.timestamp:
        await scheduler._tick()
    print(f"  scheduler last_processed: {scheduler._last_processed_ts}")

    # 3. Run the full analysis pipeline directly (with DB)
    print("\n[3] FULL ANALYSIS PIPELINE")
    async with session_maker() as session:
        result = await scheduler.pipeline.run_full_analysis(symbol="XAUUSD", db_session=session)
        sig = result["signal"]
        print(f"  signal: {sig['direction']} score={sig['confidence_score']} quality={sig['signal_quality']}")
        print(f"  market structure: bias 4H={result['market_bias']['4h']['trend']}")
        print(f"  fibonacci: {'present' if result['fibonacci_setup'] else 'none'}")
        print(f"  smc: zone={result['smc_analysis']['current_zone']}")
        print(f"  confluence: {result['confluence']['total_score']}/100")
        print(f"  ai: {result['ai_validation']['status']} ({result['ai_validation']['confidence']}%)")
        print(f"  risk: {'present' if result['position_sizing'] else 'none'}")
        print("  admission not-run-in-pipeline")

        # 4. Admission gate
        from app.risk.admission import TradeAdmissionGate
        from app.services.scheduler import _signal_payload_from_result
        payload = _signal_payload_from_result(result)
        from app.market_regime.detector import MarketRegimeDetector
        regime = MarketRegimeDetector().analyze(snap.m15)
        gate = TradeAdmissionGate(settings)
        ai_ok = result["ai_validation"]["status"] == "APPROVE"
        decision = await gate.evaluate(
            payload, repo=None, snapshot_timestamp=last_closed.timestamp,
            market_data_fresh=True, candle_closed=True, regime=regime,
            ai_status_ok=ai_ok, has_conflicting_position=False, is_duplicate=False,
        )
        print(f"\n[4] ADMISSION GATE: allowed={decision.allowed}")
        for r in decision.reasons:
            print(f"  {r}")

        # 5. Paper trade if admitted (deterministic signal path)
        opened = None
        if decision.allowed:
            opened = await scheduler.pipeline.paper_service.open_position_from_signal(payload)
            print(f"\n[5] PAPER TRADE OPENED (signal): {opened.position_id}")
        else:
            print(f"\n[5] DETERMINISTIC SIGNAL REJECTED: {decision.rejected_reason}")
            print("    -> forcing a trade to validate the paper-trade lifecycle (steps 12-19)")

        # Force a valid tradable signal to exercise the paper-trade lifecycle
        from app.core.constants import MarketBias, SignalQuality, StrategyType
        from app.signals.models import SignalPayload
        forced_signal = SignalPayload(
            instrument="XAUUSD",
            direction=SignalDirection.LONG,
            strategy=StrategyType.CONFLUENCE,
            timeframe="15m",
            timestamp=last_closed.timestamp,
            entry=4620.0,
            stop_loss=4610.0,
            take_profit_1=4635.0,
            take_profit_2=4650.0,
            take_profit_3=4670.0,
            risk_reward=3.0,
            confidence_score=85.0,
            signal_quality=SignalQuality.STRONG,
            market_bias=MarketBias.BULLISH,
            reasons=["forced validation signal"],
            invalidation_conditions=[],
            explanation="",
        )

        forced = await scheduler.pipeline.paper_service.open_position_from_signal(forced_signal)
        if forced is not None:
            print(f"  FORCED PAPER TRADE OPENED: {forced.position_id}")
            print(f"  entry={forced.target_entry} sl={forced.stop_loss} "
                  f"tp1={forced.take_profit_1} tp2={forced.take_profit_2} tp3={forced.take_profit_3}")
            print(f"  lot={forced.lot_size} risk_usd={forced.risk_amount_usd} "
                  f"fees={forced.fees_usd} spread={forced.spread_points} slip={forced.slippage_pct}")

            # 6. Simulate price reaching ENTRY then SL (Path A)
            print("\n[6] SIMULATED PRICE PATHS")
            entry_bar = _candle(last_closed.timestamp + timedelta(minutes=15), 4619.0, 4621.0, 4618.0, 4620.5)
            await scheduler.pipeline.paper_service.on_candle(entry_bar)
            pos = scheduler.pipeline.paper_service.get_position(forced.position_id)
            print(f"  after entry bar: state={pos.state.value} actual_entry={pos.actual_entry}")

            sl_bar = _candle(last_closed.timestamp + timedelta(minutes=30), 4620.0, 4621.0, 4600.0, 4605.0)
            await scheduler.pipeline.paper_service.on_candle(sl_bar)
            pos = scheduler.pipeline.paper_service.get_position(forced.position_id)
            print(f"  Path A (entry->SL): state={pos.state.value} exit_reason={pos.exit_reason} pnl={pos.realized_pnl_usd}")

            # Path B: fresh trade -> TP1 -> TP2 -> TP3
            forced2 = await scheduler.pipeline.paper_service.open_position_from_signal(forced_signal)
            await scheduler.pipeline.paper_service.on_candle(entry_bar)
            tp_bar = _candle(last_closed.timestamp + timedelta(minutes=30), 4620.0, 4680.0, 4619.0, 4675.0)
            await scheduler.pipeline.paper_service.on_candle(tp_bar)
            pos2 = scheduler.pipeline.paper_service.get_position(forced2.position_id)
            print(f"  Path B (entry->TP3): state={pos2.state.value} exit_reason={pos2.exit_reason} pnl={pos2.realized_pnl_usd}")
            print(f"\n  account balance after all: {scheduler.pipeline.paper_service.balance}")

            # 7. Persist + restart recovery
            from app.database.repository import Repository
            repo = Repository(session)
            for p in scheduler.pipeline.paper_service.get_all_positions():
                await scheduler.pipeline.paper_service._persist_position(repo, p)
            await session.commit()

            print("\n[7] RESTART RECOVERY")
            restored = PaperTradingService(initial_balance=10000.0, settings=settings)
            async with session_maker() as s2:
                await restored.restore_from_db(Repository(s2))
            print(f"  restored balance: {restored.balance} (expected {scheduler.pipeline.paper_service.balance})")
            print(f"  restored positions: {len(restored.get_all_positions())}")
            assert abs(restored.balance - scheduler.pipeline.paper_service.balance) < 0.01
            print("  restart recovery: OK")
        else:
            print("  forced trade not opened (position sizing rejected)")

    await engine.dispose()
    print("\n" + "=" * 64)
    print("RUNTIME VALIDATION COMPLETE")


def _tick_data(ts, price):
    from app.data.models import Tick
    return Tick(symbol="XAUUSD", timestamp=ts, bid=price, ask=price + 0.1)


if __name__ == "__main__":
    asyncio.run(main())