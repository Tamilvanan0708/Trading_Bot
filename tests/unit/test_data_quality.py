"""
Unit tests for live data-quality state, the hard safety gate, and
historical/live candle join correctness.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType
from app.data.live.service import LiveMarketDataService
from app.data.models import Candle, Tick
from app.risk.admission import TradeAdmissionGate
from app.signals.models import SignalPayload


def _candle(ts, c) -> Candle:
    return Candle(timestamp=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)


def _tick(price, ts) -> Tick:
    return Tick(symbol="XAUUSD", timestamp=ts, bid=price, ask=price)


def _signal() -> SignalPayload:
    return SignalPayload(
        instrument="XAUUSD", direction=SignalDirection.LONG,
        strategy=StrategyType.CONFLUENCE, entry=4617.0, stop_loss=4607.0,
        take_profit_1=4632.0, take_profit_2=4647.0, take_profit_3=4677.0,
        risk_reward=3.0, confidence_score=85.0, signal_quality=SignalQuality.STRONG,
        market_bias=MarketBias.BULLISH, reasons=["test"],
    )


# ---------------------------------------------------------------------------
# data_quality states
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_data_quality_degraded_when_no_history():
    service = LiveMarketDataService()
    dq = await service.data_quality()
    assert dq.degraded is True
    assert dq.historical_available is False
    assert "Historical data unavailable" in dq.degradation_reason


@pytest.mark.asyncio
async def test_data_quality_stale_history():
    service = LiveMarketDataService()
    old_ts = datetime.now(timezone.utc) - timedelta(hours=5)
    service._closed_15m = [_candle(old_ts, c=4610.0) for _ in range(60)]
    dq = await service.data_quality()
    assert dq.historical_available is True  # enough candles
    assert dq.historical_fresh is False      # but stale
    assert dq.degraded is True
    assert "stale" in dq.degradation_reason.lower()


@pytest.mark.asyncio
async def test_data_quality_healthy(monkeypatch):
    service = LiveMarketDataService()
    async def _healthy_health():
        return [{"connected": True, "provider": "binance", "symbol": "XAUUSD", "running": True,
                 "ticks_cached": 10, "buffer_capacity": 10000, "latest_tick": None, "metadata": {}}]
    monkeypatch.setattr(service, "health", _healthy_health)
    now = datetime.now(timezone.utc)
    service._closed_15m = [_candle(now - timedelta(minutes=15 * (50 - i)), c=4610.0 + i) for i in range(50)]
    await service.on_tick(_tick(4617.0, now))
    dq = await service.data_quality()
    assert dq.historical_available is True
    assert dq.historical_fresh is True
    assert dq.live_price is not None
    assert dq.degraded is False


@pytest.mark.asyncio
async def test_data_quality_duplicates_and_gaps_degrade():
    service = LiveMarketDataService()
    now = datetime.now(timezone.utc)
    service._closed_15m = [_candle(now - timedelta(minutes=15 * (50 - i)), c=4610.0) for i in range(50)]
    service._gap_count = 10
    service._dup_count = 3
    await service.on_tick(_tick(4617.0, now))
    dq = await service.data_quality()
    assert dq.degraded is True
    assert "gaps" in dq.degradation_reason.lower() or "duplicates" in dq.degradation_reason.lower()


@pytest.mark.asyncio
async def test_data_quality_no_live_price_degrades():
    service = LiveMarketDataService()
    now = datetime.now(timezone.utc)
    service._closed_15m = [_candle(now - timedelta(minutes=15 * (50 - i)), c=4610.0) for i in range(50)]
    dq = await service.data_quality()
    assert dq.live_price is None
    assert dq.degraded is True
    assert "Live price unavailable" in dq.degradation_reason


# ---------------------------------------------------------------------------
# No synthetic fallback in live mode
# ---------------------------------------------------------------------------

class _FailingProvider:
    async def load_base_15m(self, limit=800):
        raise RuntimeError("binance down")

    async def get_ohlcv(self, *args, **kwargs):
        raise RuntimeError("binance down")


@pytest.mark.asyncio
async def test_live_history_unavailable_blocks_no_synthetic_fallback():
    """When real history fails, the service must NOT load the synthetic CSV."""
    service = LiveMarketDataService(historical_provider=_FailingProvider())
    await service._load_historical_base()
    assert service._closed_15m == []
    dq = await service.data_quality()
    assert dq.degraded is True
    assert dq.historical_available is False


# ---------------------------------------------------------------------------
# Historical / live candle join correctness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_historical_live_join_no_overlap():
    """Closed live candles must not duplicate historical candle timestamps."""
    service = LiveMarketDataService()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Historical base ends at now-30min (30 closed 15M candles)
    service._closed_15m = [
        _candle(now - timedelta(minutes=15 * (30 - i) + 15), c=4610.0 + i) for i in range(30)
    ]
    # A live candle closes in the bucket AFTER the historical cutoff
    service._live_price = 4617.0
    closed_live = now - timedelta(minutes=15)
    service._closed_15m.append(_candle(closed_live, c=4617.0))

    timestamps = [c.timestamp for c in service._closed_15m]
    assert len(timestamps) == len(set(timestamps)), "Duplicate candle timestamps!"

    # The forming candle (current bucket) must NOT be in closed
    snap = await service.get_multi_timeframe_snapshot("XAUUSD", include_forming=False)
    forming = service._forming_15m
    if forming is not None:
        assert forming.timestamp not in [c.timestamp for c in snap.m15]


@pytest.mark.asyncio
async def test_forming_candle_excluded_from_snapshot():
    service = LiveMarketDataService()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    service._closed_15m = [_candle(now - timedelta(minutes=15 * (50 - i)), c=4610.0) for i in range(50)]
    # Live ticks in the current bucket form a forming candle
    await service.on_tick(_tick(4617.0, now))
    assert service._forming_15m is not None

    snap = await service.get_multi_timeframe_snapshot("XAUUSD", include_forming=False)
    assert snap.m15[-1].timestamp != service._forming_15m.timestamp
    # With include_forming=True it appears
    snap_form = await service.get_multi_timeframe_snapshot("XAUUSD", include_forming=True)
    assert snap_form.m15[-1].timestamp == service._forming_15m.timestamp


# ---------------------------------------------------------------------------
# Hard safety gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admission_gate_blocks_degraded_data_quality():
    service = LiveMarketDataService()
    dq = await service.data_quality()  # degraded (no history)
    gate = TradeAdmissionGate()
    decision = await gate.evaluate(
        _signal(), data_quality=dq, candle_closed=True, market_data_fresh=True, ai_status_ok=True
    )
    assert decision.allowed is False
    assert "Data Quality Gate" in decision.rejected_reason


@pytest.mark.asyncio
async def test_admission_gate_blocks_stale_history():
    service = LiveMarketDataService()
    old_ts = datetime.now(timezone.utc) - timedelta(hours=5)
    service._closed_15m = [_candle(old_ts, c=4610.0) for _ in range(60)]
    await service.on_tick(_tick(4617.0, datetime.now(timezone.utc)))
    dq = await service.data_quality()
    assert dq.degraded is True
    gate = TradeAdmissionGate()
    decision = await gate.evaluate(
        _signal(), data_quality=dq, candle_closed=True, market_data_fresh=dq.historical_fresh, ai_status_ok=True
    )
    assert decision.allowed is False
    assert "stale" in decision.rejected_reason.lower() or "Data Quality" in decision.rejected_reason


@pytest.mark.asyncio
async def test_admission_gate_healthy_data_quality_passes(monkeypatch):
    service = LiveMarketDataService()
    async def _healthy_health():
        return [{"connected": True, "provider": "binance"}]
    monkeypatch.setattr(service, "health", _healthy_health)
    now = datetime.now(timezone.utc)
    service._closed_15m = [_candle(now - timedelta(minutes=15 * (50 - i)), c=4610.0 + i) for i in range(50)]
    await service.on_tick(_tick(4617.0, now))
    dq = await service.data_quality()
    assert dq.degraded is False
    gate = TradeAdmissionGate()
    decision = await gate.evaluate(
        _signal(), data_quality=dq, candle_closed=True, market_data_fresh=True, ai_status_ok=True
    )
    # Data quality passes; rejection (if any) must NOT be from the data-quality gate
    assert "Data Quality Gate" not in decision.rejected_reason