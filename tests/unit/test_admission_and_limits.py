"""
Tests for the trade admission gate, trading limits, and duplicate protection.
"""

from datetime import datetime, timezone

import pytest

from app.config.settings import Settings
from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.market_regime.detector import (
    MarketRegime,
    MarketRegimeDetector,
    RegimeAnalysis,
)
from app.news.filter import NewsFilter
from app.paper_trading.limits import TradingLimits
from app.risk.admission import TradeAdmissionGate
from app.signals.models import SignalPayload


def _signal(quality=SignalQuality.STRONG, rr=2.0, direction=SignalDirection.LONG, entry=2650.0, sl=2640.0, tp2=2680.0) -> SignalPayload:
    return SignalPayload(
        instrument="XAUUSD",
        direction=direction,
        strategy=StrategyType.CONFLUENCE,
        entry=entry,
        stop_loss=sl,
        take_profit_1=tp2 - 5.0,
        take_profit_2=tp2,
        take_profit_3=tp2 + 10.0,
        risk_reward=rr,
        confidence_score=85.0,
        signal_quality=quality,
        market_bias=MarketBias.BULLISH if direction == SignalDirection.LONG else MarketBias.BEARISH,
        reasons=["test"],
    )


# ---------------------------------------------------------------------------
# Admission gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admission_passes_strong_signal():
    gate = TradeAdmissionGate()
    signal = _signal()
    decision = await gate.evaluate(signal, candle_closed=True, market_data_fresh=True, ai_status_ok=True)
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_admission_rejects_no_trade():
    gate = TradeAdmissionGate()
    signal = _signal(direction=SignalDirection.NO_TRADE, quality=SignalQuality.NO_TRADE)
    decision = await gate.evaluate(signal)
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_admission_rejects_low_rr():
    gate = TradeAdmissionGate(Settings(MIN_RISK_REWARD=2.0))
    signal = _signal(rr=1.2)
    decision = await gate.evaluate(signal, candle_closed=True, market_data_fresh=True, ai_status_ok=True)
    assert decision.allowed is False
    assert "R:R" in decision.rejected_reason


@pytest.mark.asyncio
async def test_admission_rejects_low_quality():
    gate = TradeAdmissionGate()
    signal = _signal(quality=SignalQuality.MODERATE)
    decision = await gate.evaluate(signal, candle_closed=True, market_data_fresh=True, ai_status_ok=True)
    assert decision.allowed is False
    assert "below STRONG" in decision.rejected_reason


@pytest.mark.asyncio
async def test_admission_rejects_stale_data():
    gate = TradeAdmissionGate()
    decision = await gate.evaluate(_signal(), market_data_fresh=False, candle_closed=True, ai_status_ok=True)
    assert decision.allowed is False
    assert "stale" in decision.rejected_reason


@pytest.mark.asyncio
async def test_admission_rejects_open_candle():
    gate = TradeAdmissionGate()
    decision = await gate.evaluate(_signal(), market_data_fresh=True, candle_closed=False, ai_status_ok=True)
    assert decision.allowed is False
    assert "not fully closed" in decision.rejected_reason


@pytest.mark.asyncio
async def test_admission_rejects_ai_fail():
    gate = TradeAdmissionGate()
    decision = await gate.evaluate(_signal(), market_data_fresh=True, candle_closed=True, ai_status_ok=False)
    assert decision.allowed is False
    assert "AI validation" in decision.rejected_reason


@pytest.mark.asyncio
async def test_admission_invalid_stop_loss():
    gate = TradeAdmissionGate()
    signal = _signal(direction=SignalDirection.LONG, sl=2660.0)  # SL above entry
    decision = await gate.evaluate(signal, candle_closed=True, market_data_fresh=True, ai_status_ok=True)
    assert decision.allowed is False
    assert "stop loss must be below" in decision.rejected_reason


@pytest.mark.asyncio
async def test_admission_regime_filter():
    gate = TradeAdmissionGate(Settings(REGIME_FILTER_ENABLED=True, ALLOWED_REGIMES="TRENDING"))
    regime = RegimeAnalysis(MarketRegime.RANGING, "NEUTRAL", "Test ranging market.")
    decision = await gate.evaluate(_signal(), regime=regime, candle_closed=True, market_data_fresh=True, ai_status_ok=True)
    assert decision.allowed is False
    assert "RANGING" in decision.rejected_reason


# ---------------------------------------------------------------------------
# Trading limits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_limits_max_daily_trades_blocked(in_memory_db):
    from app.database.repository import Repository
    repo = Repository(in_memory_db)
    limits = TradingLimits(Settings(MAX_DAILY_TRADES=1))
    decision = await limits.check_open_allowed(repo)
    assert decision.allowed is True
    await limits.register_trade_opened(repo)
    decision2 = await limits.check_open_allowed(repo)
    assert decision2.allowed is False
    assert "Daily trade limit" in decision2.reason


@pytest.mark.asyncio
async def test_limits_consecutive_losses_blocked(in_memory_db):
    from app.database.repository import Repository
    repo = Repository(in_memory_db)
    limits = TradingLimits(Settings(MAX_CONSECUTIVE_LOSSES=2))
    # First two checks pass and register losses
    for _ in range(2):
        decision = await limits.check_open_allowed(repo)
        assert decision.allowed is True
        await limits.register_trade_result(repo, -50.0)
    # Third check must be blocked after 2 consecutive losses
    decision = await limits.check_open_allowed(repo)
    assert decision.allowed is False
    assert "consecutive" in decision.reason


@pytest.mark.asyncio
async def test_limits_max_drawdown_blocks(in_memory_db):
    from app.database.repository import Repository
    repo = Repository(in_memory_db)
    limits = TradingLimits(Settings(MAX_TOTAL_DRAWDOWN_PCT=10.0, ACCOUNT_BALANCE=10000.0))
    # Balance = 9000 → drawdown = 10% → threshold hit
    decision = await limits.check_open_allowed(repo, current_balance=9000.0)
    assert decision.allowed is False
    assert "drawdown" in decision.reason.lower()


@pytest.mark.asyncio
async def test_limits_max_drawdown_allowed_under_threshold(in_memory_db):
    from app.database.repository import Repository
    repo = Repository(in_memory_db)
    limits = TradingLimits(Settings(MAX_TOTAL_DRAWDOWN_PCT=10.0, ACCOUNT_BALANCE=10000.0))
    decision = await limits.check_open_allowed(repo, current_balance=9500.0)
    assert decision.allowed is True


# ---------------------------------------------------------------------------
# Market regime
# ---------------------------------------------------------------------------

def test_market_regime_insufficient_data():
    detector = MarketRegimeDetector()
    from app.data.models import Candle
    c = [Candle(timestamp=datetime.now(timezone.utc), open=2650.0, high=2655.0, low=2645.0, close=2652.0)]
    result = detector.analyze(c)
    assert result.regime == MarketRegime.UNCERTAIN
    assert "Insufficient" in result.details


# ---------------------------------------------------------------------------
# News filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_news_filter_disabled_allows_trades():
    nf = NewsFilter(Settings(NEWS_FILTER_ENABLED=False))
    assert await nf.in_blackout() is False


@pytest.mark.asyncio
async def test_news_filter_unavailable_reports_fallback():
    nf = NewsFilter(Settings(NEWS_FILTER_ENABLED=True))
    assert nf.available is False
    # No provider configured — safe fallback should not block
    assert await nf.in_blackout() is False


# ---------------------------------------------------------------------------
# Performance engine
# ---------------------------------------------------------------------------

def test_performance_engine_empty():
    from app.performance.engine import compute_performance
    result = compute_performance([])
    assert result["total_trades"] == 0
    assert result["profit_factor"] == 0.0


def test_performance_engine_with_trades():
    from app.core.constants import SignalDirection
    from app.paper_trading.state_machine import PaperPosition
    from app.performance.engine import compute_performance
    now = datetime.now(timezone.utc)
    trades = [
        PaperPosition(
            position_id="1", signal_id="s1", direction=SignalDirection.LONG,
            lot_size=0.1, risk_amount_usd=100.0, target_entry=2650.0,
            stop_loss=2640.0, take_profit_1=2665.0, take_profit_2=2680.0, take_profit_3=2700.0,
            closed_at=now, realized_pnl_usd=200.0, realized_r=2.0, exit_reason="TP2_HIT",
        ),
        PaperPosition(
            position_id="2", signal_id="s2", direction=SignalDirection.SHORT,
            lot_size=0.1, risk_amount_usd=100.0, target_entry=2650.0,
            stop_loss=2660.0, take_profit_1=2640.0, take_profit_2=2620.0, take_profit_3=2600.0,
            closed_at=now, realized_pnl_usd=-100.0, realized_r=-1.0, exit_reason="STOP_LOSS_HIT",
        ),
    ]
    result = compute_performance(trades)
    assert result["total_trades"] == 2
    assert result["wins"] == 1
    assert result["losses"] == 1
    assert result["win_rate_pct"] == 50.0
    assert result["net_pnl_usd"] == 100.0