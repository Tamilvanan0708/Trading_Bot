"""
Unit tests for Confluence Engine, Signal Engine, and Risk Management.
"""

import pytest

from app.confluence.engine import ConfluenceEngine
from app.core.constants import SignalDirection
from app.data.csv_provider import CsvMarketDataProvider
from app.risk.manager import RiskManager
from app.risk.models import RiskCalculationRequest
from app.signals.engine import SignalEngine


@pytest.mark.asyncio
async def test_confluence_and_signal_generation():
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    snapshot = await provider.get_multi_timeframe_snapshot("XAUUSD")

    confluence_engine = ConfluenceEngine()
    score = confluence_engine.evaluate(snapshot)

    assert 0.0 <= score.total_score <= 100.0
    assert score.breakdown.htf_bias.max_points == 20
    assert score.breakdown.market_structure.max_points == 20

    signal_engine = SignalEngine()
    signal = signal_engine.generate_signal(snapshot)

    assert signal.instrument == "XAUUSD"
    assert signal.direction in [SignalDirection.LONG, SignalDirection.SHORT, SignalDirection.NO_TRADE]
    # Final score >= base confluence score (R:R points may be added after candidate selection)
    assert signal.confidence_score >= score.total_score


def test_risk_manager_position_sizing():
    risk_mgr = RiskManager()

    # Valid Long
    req = RiskCalculationRequest(
        account_balance=10000.0,
        risk_percent=1.0,  # $100 risk
        entry_price=2650.0,
        stop_loss=2640.0,  # $10 SL distance
        take_profit_1=2665.0,
        take_profit_2=2675.0,
        take_profit_3=2690.0,
        direction=SignalDirection.LONG,
        contract_size=100.0,
    )
    res = risk_mgr.calculate_position_size(req)

    assert res.is_valid is True
    assert res.lot_size == 0.10  # $100 / ($10 * 100) = 0.10 lots
    assert res.potential_loss_usd == 100.0
    assert res.risk_reward_tp2 == 2.5

    # Invalid Stop loss (Long with SL > Entry)
    invalid_req = RiskCalculationRequest(
        account_balance=10000.0,
        risk_percent=1.0,
        entry_price=2650.0,
        stop_loss=2660.0,
        take_profit_1=2670.0,
        take_profit_2=2680.0,
        take_profit_3=2690.0,
        direction=SignalDirection.LONG,
        contract_size=100.0,
    )
    invalid_res = risk_mgr.calculate_position_size(invalid_req)
    assert invalid_res.is_valid is False
    assert "Invalid Long SL" in invalid_res.rejection_reason


# ---------------------------------------------------------------------------
# Phase 1 regression: R:R calculation
# ---------------------------------------------------------------------------

def test_confluence_rr_not_hardcoded():
    """R:R component must not be awarded when no SL/TP is provided."""
    from app.confluence.engine import ConfluenceEngine
    rr_pts, rr_passed, rr_detail = ConfluenceEngine.compute_rr_score(
        entry=2650.0, stop_loss=2650.0, take_profit=2660.0,  # zero SL distance
        min_rr=1.5, max_points=5.0,
    )
    assert rr_pts == 0.0
    assert rr_passed is False
    assert "zero" in rr_detail.lower()


def test_confluence_rr_awarded_when_met():
    from app.confluence.engine import ConfluenceEngine
    rr_pts, rr_passed, rr_detail = ConfluenceEngine.compute_rr_score(
        entry=2650.0, stop_loss=2640.0, take_profit=2665.0,  # 10 SL, 15 TP = 1.5 R:R
        min_rr=1.5, max_points=5.0,
    )
    assert rr_pts == 5.0
    assert rr_passed is True


def test_confluence_rr_not_awarded_when_below_min():
    from app.confluence.engine import ConfluenceEngine
    rr_pts, rr_passed, rr_detail = ConfluenceEngine.compute_rr_score(
        entry=2650.0, stop_loss=2640.0, take_profit=2652.0,  # 10 SL, 2 TP = 0.2 R:R
        min_rr=1.5, max_points=5.0,
    )
    assert rr_pts == 0.0
    assert rr_passed is False


# ---------------------------------------------------------------------------
# Phase 1 regression: Signal admission threshold consistency
# ---------------------------------------------------------------------------

def test_signal_is_tradable_uses_quality():
    from app.core.constants import (
        MarketBias,
        SignalDirection,
        SignalQuality,
        StrategyType,
    )
    from app.signals.models import SignalPayload

    # Signal with MODERATE quality should NOT be tradable
    moderate = SignalPayload(
        direction=SignalDirection.LONG,
        strategy=StrategyType.CONFLUENCE,
        entry=2650.0, stop_loss=2640.0,
        take_profit_1=2665.0, take_profit_2=2675.0, take_profit_3=2690.0,
        risk_reward=1.5, confidence_score=70.0, signal_quality=SignalQuality.MODERATE,
        market_bias=MarketBias.BULLISH,
    )
    assert moderate.is_tradable is False

    # Signal with STRONG quality should be tradable
    strong = SignalPayload(
        direction=SignalDirection.LONG,
        strategy=StrategyType.CONFLUENCE,
        entry=2650.0, stop_loss=2640.0,
        take_profit_1=2665.0, take_profit_2=2675.0, take_profit_3=2690.0,
        risk_reward=2.5, confidence_score=80.0, signal_quality=SignalQuality.STRONG,
        market_bias=MarketBias.BULLISH,
    )
    assert strong.is_tradable is True


# ---------------------------------------------------------------------------
# Phase 1 regression: ATR in signal engine (no hardcoded 2.5)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signal_engine_uses_atr_not_hardcoded():
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    snapshot = await provider.get_multi_timeframe_snapshot("XAUUSD")
    from app.signals.engine import SignalEngine

    engine = SignalEngine()
    atr = engine._compute_atr(snapshot)
    assert atr > 0.0
    assert atr != 2.5  # must not be the old hardcoded value


# ---------------------------------------------------------------------------
# Phase 1 regression: Paper trading persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_position_to_dict_includes_all_fields():
    from app.core.constants import SignalDirection, TradeState
    from app.paper_trading.service import paper_position_to_dict
    from app.paper_trading.state_machine import PaperPosition

    pos = PaperPosition(
        position_id="test-123",
        signal_id="sig-abc",
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        lot_size=0.1,
        risk_amount_usd=100.0,
        target_entry=2650.0,
        stop_loss=2640.0,
        take_profit_1=2665.0,
        take_profit_2=2675.0,
        take_profit_3=2690.0,
        state=TradeState.PENDING,
    )
    d = paper_position_to_dict(pos)
    assert d["id"] == "test-123"
    assert d["signal_id"] == "sig-abc"
    assert d["direction"] == "LONG"
    assert d["state"] == "PENDING"
    assert d["lot_size"] == 0.1
    assert d["risk_amount"] == 100.0
    assert d["target_entry"] == 2650.0
    assert d["stop_loss"] == 2640.0
    assert d["take_profit_1"] == 2665.0
    assert d["take_profit_2"] == 2675.0
    assert d["take_profit_3"] == 2690.0
    assert "opened_at" in d
    assert "exit_price" in d
    assert "exit_reason" in d
    assert "realized_pnl" in d
    assert "realized_r" in d
    assert "closed_at" in d
    assert "state_logs" in d
