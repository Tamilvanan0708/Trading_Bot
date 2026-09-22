"""
Unit test for Live vs Backtest Execution Parity Comparator.
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting.live_comparison import compare_backtest_with_live_trades
from app.database.models import PaperTradeModel


@pytest.mark.asyncio
async def test_live_vs_backtest_comparison(in_memory_db: AsyncSession):
    start_dt = datetime(2026, 9, 22, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 9, 22, 23, 59, 59, tzinfo=timezone.utc)

    session = in_memory_db

    # Insert mock live paper trade
    trade = PaperTradeModel(
        id="test-live-1",
        signal_id="FIB_RETR_30M_L1_4374_1789456000_4331_1789460000",
        symbol="XAUUSD",
        direction="SHORT",
        state="CLOSED",
        lot_size=0.50,
        risk_amount=435.68,
        target_entry=4356.75,
        actual_entry=4356.75,
        stop_loss=4364.40,
        take_profit_1=4331.99,
        take_profit_2=4331.99,
        take_profit_3=4331.99,
        exit_price=4331.99,
        exit_reason="TP_HIT",
        realized_pnl=1238.00,
        opened_at=datetime(2026, 9, 22, 3, 24, 0, tzinfo=timezone.utc),
        closed_at=datetime(2026, 9, 22, 5, 0, 0, tzinfo=timezone.utc),
        state_logs=[{"timeframe": "30M"}],
    )
    session.add(trade)
    await session.commit()

    # Mock backtest trades list
    backtest_trades = [
        {
            "trade_id": "bt-1",
            "strategy": "FIB_WITH_RETRACEMENT",
            "timeframe": "30M",
            "direction": "SHORT",
            "entry_time": "2026-09-22T03:30:00+00:00",
            "entry_price": 4356.50,
            "sl_price": 4364.40,
            "tp_price": 4331.99,
            "exit_time": "2026-09-22T05:00:00+00:00",
            "exit_price": 4331.99,
            "exit_reason": "TP_HIT",
            "pnl_pts": 24.51,
            "pnl_usd": 1225.50,
            "r_multiple": 3.1,
            "status": "WIN",
        }
    ]

    res = await compare_backtest_with_live_trades(
        backtest_trades,
        start_dt,
        end_dt,
        session,
        selected_strategy="FIB_WITH_RETRACEMENT",
        selected_timeframe="30M",
    )

    assert res["enabled"] is True
    assert res["total_live_trades"] == 1
    assert res["matched_count"] == 1
    assert res["parity_rate_pct"] == 100.0
    assert res["outcome_agreement_pct"] == 100.0
    assert res["avg_entry_slippage_pts"] == 0.25  # 4356.75 - 4356.50 = 0.25
    assert len(res["comparisons"]) == 1
    assert res["comparisons"][0]["status"] == "PARITY_100"
