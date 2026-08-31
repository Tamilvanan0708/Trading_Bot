"""
Unit tests for settings and database operations.
"""

import pytest

from app.config.settings import Settings
from app.core.constants import MarketBias, SignalDirection, SignalQuality
from app.database.repository import Repository


@pytest.mark.asyncio
async def test_settings_validation():
    s = Settings(
        DEFAULT_SYMBOL="XAUUSD",
        RISK_PERCENT=1.5,
    )
    assert s.DEFAULT_SYMBOL == "XAUUSD"
    assert s.RISK_PERCENT == 1.5
    assert s.WEIGHT_HTF_BIAS + s.WEIGHT_MARKET_STRUCTURE + s.WEIGHT_SMC_CONFIRMATION + s.WEIGHT_FIB_CONFIRMATION + s.WEIGHT_LIQUIDITY + s.WEIGHT_ENTRY_CONFIRMATION + s.WEIGHT_RISK_REWARD == 100


@pytest.mark.asyncio
async def test_database_crud(in_memory_db):
    repo = Repository(in_memory_db)

    # 1. Save Signal
    signal_data = {
        "symbol": "XAUUSD",
        "strategy": "MULTI_TF_CONFLUENCE",
        "direction": SignalDirection.LONG.value,
        "timeframe": "15m",
        "entry_price": 2650.0,
        "stop_loss": 2640.0,
        "take_profit_1": 2670.0,
        "take_profit_2": 2685.0,
        "take_profit_3": 2700.0,
        "risk_reward": 2.0,
        "confidence_score": 85.0,
        "signal_quality": SignalQuality.STRONG.value,
        "market_bias": MarketBias.BULLISH.value,
        "reasons": ["4H Bullish Bias", "1H BOS", "30M 61.8% Fib", "15M Bullish CHoCH"],
        "invalidation_conditions": ["15M Close below 2638.0"],
        "metadata_payload": {"fib_level": 0.618},
    }

    signal = await repo.save_signal(signal_data)
    assert signal.id is not None
    assert signal.entry_price == 2650.0

    # 2. Save AI Validation
    ai_data = {
        "signal_id": signal.id,
        "status": "APPROVE",
        "confidence": 90.0,
        "explanation": "Clear multi-timeframe structural alignment at key golden zone.",
        "identified_risks": ["High impact USD news at 14:30"],
        "missing_confirmations": [],
    }
    ai_val = await repo.save_ai_validation(ai_data)
    assert ai_val.id is not None
    assert ai_val.signal_id == signal.id

    # 3. Fetch Signal with AI validation eager load
    fetched = await repo.get_signal_by_id(signal.id)
    assert fetched is not None
    assert fetched.ai_validation is not None
    assert fetched.ai_validation.status == "APPROVE"

    # 4. Create Paper Trade
    trade_data = {
        "signal_id": signal.id,
        "symbol": "XAUUSD",
        "direction": "LONG",
        "state": "PENDING",
        "lot_size": 0.1,
        "risk_amount": 100.0,
        "target_entry": 2650.0,
        "stop_loss": 2640.0,
        "take_profit_1": 2670.0,
        "take_profit_2": 2685.0,
        "take_profit_3": 2700.0,
    }
    trade = await repo.create_paper_trade(trade_data)
    assert trade.id is not None
    assert trade.state == "PENDING"
