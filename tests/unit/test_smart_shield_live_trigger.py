import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from app.database.models import PaperTradeModel
from app.paper_trading.sync import sync_strategy_paper_trades
from app.config.execution_settings import get_execution_settings


@pytest.mark.asyncio
async def test_fast_monitor_triggers_smart_shield_on_l2_tp(in_memory_db):
    async_session = in_memory_db
    exec_cfg = get_execution_settings()
    exec_cfg.smart_shield_enabled = True
    exec_cfg.smart_shield_level = "0.500"
    exec_cfg.strategy_fib_retracement = False
    exec_cfg.strategy_smc_fib = False
    exec_cfg.strategy_fib_trend = False

    # Create companion L1 and L2 paper trades on 5M (where Smart Shield is active)
    l1_trade = PaperTradeModel(
        id="test-l1-trade",
        signal_id="FIB_RETR_5M_L1_4315_1789083900",
        symbol="XAUUSD",
        direction="LONG",
        state="OPEN",
        lot_size=0.01,
        risk_amount=20.0,
        target_entry=4350.0,
        actual_entry=4350.0,
        stop_loss=4330.0,  # 0.236
        take_profit_1=4390.0,  # 1.000
        take_profit_2=4390.0,
        take_profit_3=4390.0,
        opened_at=datetime.now(timezone.utc),
        state_logs=[],
    )

    l2_trade = PaperTradeModel(
        id="test-l2-trade",
        signal_id="FIB_RETR_5M_L2_4315_1789083900",
        symbol="XAUUSD",
        direction="LONG",
        state="OPEN",
        lot_size=0.01,
        risk_amount=20.0,
        target_entry=4340.0,  # 0.500
        actual_entry=4340.0,
        stop_loss=4330.0,  # 0.236
        take_profit_1=4350.0,  # 0.618 TP
        take_profit_2=4350.0,
        take_profit_3=4350.0,
        opened_at=datetime.now(timezone.utc),
        state_logs=[],
    )

    async_session.add(l1_trade)
    async_session.add(l2_trade)
    await async_session.commit()

    # Live price hits L2's TP (4351.0 >= 4350.0)
    mock_live_svc = MagicMock()
    mock_live_svc.get_latest_price = AsyncMock(return_value=4351.0)

    with patch("app.paper_trading.sync.get_live_service", return_value=mock_live_svc), \
         patch("app.paper_trading.sync._dispatch_tg_alert") as mock_tg, \
         patch("app.services.mt5_bridge_manager.get_mt5_bridge_manager") as mock_mt5_mgr:
        
        mock_bridge = MagicMock()
        mock_mt5_mgr.return_value = mock_bridge

        await sync_strategy_paper_trades(async_session, force=True)

    # Refresh L1 trade from DB
    await async_session.refresh(l1_trade)
    await async_session.refresh(l2_trade)

    # Verify L2 trade closed with TP_HIT
    assert l2_trade.state == "CLOSED"
    assert l2_trade.exit_reason == "TP_HIT"

    # Verify L1 trade Stop Loss was trailed from 4330.0 to 4340.0 (0.500 buffer level)
    assert l1_trade.stop_loss == 4340.0
    assert l1_trade.state == "OPEN"

    # Verify state logs recorded the event
    assert any(log.get("event") == "SMART_SHIELD_TRAILED" for log in (l1_trade.state_logs or []))

    # Verify MT5 enqueue_modify was called for L1 trade
    mock_bridge.enqueue_modify.assert_called_with(
        paper_trade_id=l1_trade.id,
        symbol=exec_cfg.mt5_symbol or "XAUUSD-VIP",
        new_sl=4340.0,
        direction="LONG",
    )

    # Verify TG alert was called (close alert for L2 + shield alert for L1)
    assert mock_tg.call_count >= 2
