"""
Unit tests for Breakout Strategy backtesting in StrategyBacktester.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

from app.backtesting.strategy_simulator import StrategyBacktester
from app.data.models import Candle


@pytest.mark.asyncio
async def test_breakout_strategy_backtest():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 10, 5, tzinfo=timezone.utc)
    
    # Generate synthetic candles with a clear breakout pattern
    candles = []
    base_price = 2700.0
    for i in range(100):
        ts = start.replace(hour=i % 24, minute=(i * 15) % 60)
        # First 40 candles range between 2695 and 2705
        # Candle 45 breaks out upward to 2720
        if i < 40:
            c = Candle(timestamp=ts, open=base_price, high=base_price + 3.0, low=base_price - 3.0, close=base_price + 1.0, volume=500.0)
        elif i == 45:
            c = Candle(timestamp=ts, open=base_price + 4.0, high=base_price + 18.0, low=base_price + 2.0, close=base_price + 16.0, volume=2500.0)
        elif i == 46:
            c = Candle(timestamp=ts, open=base_price + 16.0, high=base_price + 35.0, low=base_price + 15.0, close=base_price + 32.0, volume=2000.0)
        else:
            c = Candle(timestamp=ts, open=base_price + 10.0, high=base_price + 12.0, low=base_price + 8.0, close=base_price + 10.0, volume=400.0)
        candles.append(c)

    simulator = StrategyBacktester(
        symbol="XAUUSD",
        lot_size=0.01,
        initial_capital=10000.0,
        risk_mode="dynamic",
        risk_percent=5.0,
        sizing_mode="fixed",
    )

    with patch("app.backtesting.strategy_simulator.fetch_historical_candles", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = candles
        result = await simulator.run(
            strategy_name="BREAKOUT_STRATEGY",
            timeframe="15m",
            start_date=start,
            end_date=end,
        )

        assert "summary" in result
        assert "trades" in result
        summary = result["summary"]
        assert summary["symbol"] == "XAUUSD"
        assert summary["initial_capital"] == 10000.0
