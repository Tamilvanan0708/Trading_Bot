"""
Unit tests for Breakout Strategy MT5 candle feed and execution synchronization.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.config.execution_settings import get_execution_settings
from app.database.models import PaperTradeModel
from app.data.models import Candle
from app.paper_trading.sync import sync_strategy_paper_trades


@pytest.mark.asyncio
async def test_breakout_uses_mt5_candles_and_executes_on_breakout(in_memory_db):
    async_session = in_memory_db
    exec_cfg = get_execution_settings()
    exec_cfg.strategy_breakout = True
    exec_cfg.strategy_fib_retracement = False
    exec_cfg.strategy_smc_fib = False
    exec_cfg.strategy_fib_trend = False
    exec_cfg.mt5_bridge_enabled = True

    now = datetime.now(timezone.utc)
    base_price = 3900.0
    mock_candles = []
    # Generate 35 candles inside 3890 - 3910
    for i in range(35):
        t = now - timedelta(minutes=(35 - i) * 15)
        c = Candle(
            timestamp=t,
            open=base_price,
            high=base_price + 5.0,
            low=base_price - 5.0,
            close=base_price + 2.0,
            volume=100.0,
        )
        mock_candles.append(c)

    # Make the last candle break out bullishly (> 3915.80)
    mock_candles[-1] = Candle(
        timestamp=now,
        open=base_price + 2.0,
        high=3925.0,
        low=base_price + 1.0,
        close=3922.0,  # Clear bullish close above upper channel + buffer
        volume=250.0,
    )

    mock_mt5_provider = MagicMock()
    mock_mt5_provider._connected = True
    mock_mt5_provider.get_ohlcv = AsyncMock(return_value=mock_candles)

    mock_live_svc = MagicMock()
    mock_live_svc._mt5_provider = mock_mt5_provider
    mock_live_svc.get_latest_price = AsyncMock(return_value=3922.0)

    mock_bridge = MagicMock()
    mock_bridge.enqueue_order.return_value = {
        "status": "filled_native",
        "ticket": 99887766,
        "price": 3922.0,
    }

    with patch("app.paper_trading.sync.get_live_service", return_value=mock_live_svc), \
         patch("app.paper_trading.sync._dispatch_tg_alert"), \
         patch("app.services.mt5_bridge_manager.get_mt5_bridge_manager", return_value=mock_bridge):

        await sync_strategy_paper_trades(async_session, force=True)

    # Verify MT5 get_ohlcv was called for XAUUSD (proves MT5 feed was used)
    assert mock_mt5_provider.get_ohlcv.called
    calls = mock_mt5_provider.get_ohlcv.call_args_list
    assert any(call[0][0] == "XAUUSD" for call in calls)

    # Verify Breakout paper trade was created
    from sqlalchemy import select
    trades = (await async_session.execute(
        select(PaperTradeModel).where(PaperTradeModel.signal_id.like("BREAKOUT_%"))
    )).scalars().all()

    assert len(trades) >= 1
    bo_trade = trades[0]
    assert bo_trade.direction == "LONG"
    assert bo_trade.actual_entry == 3922.0
    assert bo_trade.state == "OPEN"
    assert any(log.get("event") == "MT5_FILLED" and log.get("ticket") == 99887766 for log in bo_trade.state_logs)
