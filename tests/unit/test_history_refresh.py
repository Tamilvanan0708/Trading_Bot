"""
Unit tests for automatic historical-data refresh + backfill.

Covers: periodic refresh, safe merge, backfill, forming-candle protection,
gap/dup/ooo recomputation, WS-reconnect backfill, emergency refresh +
cooldown, race safety, degraded behavior, and live-price integrity.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import Settings
from app.core.constants import TimeFrame
from app.data.ingestion import validate_candles
from app.data.live.service import LiveMarketDataService, _bucket_start
from app.data.models import Candle, Tick
from app.services.scheduler import AnalysisScheduler


def _candle(ts, c) -> Candle:
    return Candle(timestamp=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)


def _candles(n, end_ts=None, base=4600.0):
    end = end_ts or (datetime.now(timezone.utc) - timedelta(hours=2))
    return [_candle(end - timedelta(minutes=15 * (n - 1 - i)), base + i) for i in range(n)]


def _cutoff() -> datetime:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return _bucket_start(now, 15)


class _FakeHistory:
    """Fake history provider returning a controllable candle set."""
    def __init__(self, candles=None, fail=False, error=None):
        self._candles = list(candles or [])
        self.fail = fail
        self.error = error or RuntimeError("rest down")
        self.calls = 0

    async def load_base_15m(self, limit=800):
        self.calls += 1
        if self.fail:
            raise self.error
        v = validate_candles(self._candles, TimeFrame.M15, strict_gaps=False)
        return self._candles, {
            "total": len(self._candles),
            "valid": v.valid,
            "gaps": v.gaps,
            "duplicates": v.duplicates,
            "out_of_order": v.out_of_order,
            "invalid_ohlc": v.invalid_ohlc,
            "errors": v.errors,
            "warnings": v.warnings,
        }


def _settings(**overrides):
    base = dict(LIVE_HISTORY_REFRESH_INTERVAL_MINUTES=15,
                LIVE_HISTORY_REFRESH_ON_DEGRADED=True,
                LIVE_HISTORY_REFRESH_LOOKBACK_HOURS=48,
                LIVE_HISTORY_EMERGENCY_REFRESH_COOLDOWN_SECONDS=60)
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Basic refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_success_updates_timestamp():
    provider = _FakeHistory(candles=_candles(50))
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    await service._load_historical_base()
    before = service._last_history_refresh_at

    result = await service.refresh_history()
    assert result["status"] == "SUCCESS"
    assert service._refresh_status == "SUCCESS"
    assert service._refresh_error is None
    assert service._last_history_refresh_at is not None
    assert service._last_history_refresh_at >= (before or datetime.now(timezone.utc) - timedelta(minutes=5))


@pytest.mark.asyncio
async def test_refresh_failure_keeps_degraded():
    provider = _FakeHistory(candles=_candles(50), fail=True)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    await service._load_historical_base()

    result = await service.refresh_history()
    assert result["status"] == "FAILED"
    assert service._refresh_status == "FAILED"
    assert service._refresh_error is not None

    dq = await service.data_quality()
    assert dq.degraded is True


@pytest.mark.asyncio
async def test_refresh_timeout_keeps_degraded():
    provider = _FakeHistory(candles=_candles(50), fail=True, error=TimeoutError("timeout"))
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    result = await service.refresh_history()
    assert result["status"] == "FAILED"
    assert "timeout" in result["error"].lower()
    dq = await service.data_quality()
    assert dq.degraded is True


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_fills_missing_candles():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Existing has a hole: ..., 10:30, [gap 10:45-11:15], 11:30 ...
    existing = [
        _candle(now - timedelta(minutes=15 * 6), 4600.0),
        _candle(now - timedelta(minutes=15 * 5), 4600.1),
        _candle(now - timedelta(minutes=15 * 4), 4600.2),  # 10:30
        _candle(now - timedelta(minutes=15 * 1), 4600.3),  # 11:30 (3 candles missing)
    ]
    # REST returns the full set including the missing candles
    full = [existing[0], existing[1], existing[2],
            _candle(now - timedelta(minutes=15 * 3), 4600.4),
            _candle(now - timedelta(minutes=15 * 2), 4600.5),
            existing[3]]
    provider = _FakeHistory(candles=full)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._closed_15m = list(existing)

    await service.refresh_history()

    timestamps = [c.timestamp for c in service._closed_15m]
    assert len(timestamps) == len(set(timestamps))
    # All 6 candles present (gap filled)
    assert len(service._closed_15m) == 6
    assert service._closed_15m[-1].timestamp == existing[-1].timestamp


@pytest.mark.asyncio
async def test_backfill_does_not_duplicate_candles():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    end = now - timedelta(minutes=30)
    existing = _candles(10, end_ts=end)
    fetched = _candles(10, end_ts=end)  # identical set
    provider = _FakeHistory(candles=fetched)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._closed_15m = list(existing)

    await service.refresh_history()

    timestamps = [c.timestamp for c in service._closed_15m]
    assert len(timestamps) == len(set(timestamps))
    assert len(service._closed_15m) == 10  # no growth, no duplicates


@pytest.mark.asyncio
async def test_backfill_does_not_insert_forming_candle():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    cutoff = _bucket_start(now, 15)
    # REST would return the forming bucket candle — must be excluded.
    fetched = _candles(10) + [_candle(cutoff, 9999.0)]
    provider = _FakeHistory(candles=fetched)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._closed_15m = _candles(10)
    service._forming_15m = _candle(cutoff, 9999.0)

    await service.refresh_history()

    assert all(c.timestamp < cutoff for c in service._closed_15m)
    assert cutoff not in [c.timestamp for c in service._closed_15m]


# ---------------------------------------------------------------------------
# Metric recalculation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_recalculates_gap_count():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Existing has a real gap that REST also cannot fill (missing in both).
    candles = [
        _candle(now - timedelta(minutes=15 * 10), 4600.0),
        _candle(now - timedelta(minutes=15 * 9), 4600.1),
        _candle(now - timedelta(minutes=15 * 1), 4600.2),  # 2h gap
    ]
    provider = _FakeHistory(candles=candles)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    await service._load_historical_base()

    await service.refresh_history()
    assert service._gap_count >= 1
    dq = await service.data_quality()
    assert dq.gap_count >= 1


@pytest.mark.asyncio
async def test_refresh_recalculates_duplicate_count():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    end = now - timedelta(minutes=30)
    candles = _candles(6, end_ts=end)
    # Inject a duplicate timestamp
    candles.append(candles[-1].model_copy())
    provider = _FakeHistory(candles=candles)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._closed_15m = _candles(6, end_ts=end)

    await service.refresh_history()
    # Merge dedups by timestamp, so the stored set has no duplicates
    timestamps = [c.timestamp for c in service._closed_15m]
    assert len(timestamps) == len(set(timestamps))
    assert service._dup_count == 0


@pytest.mark.asyncio
async def test_refresh_recalculates_out_of_order_count():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    end = now - timedelta(minutes=30)
    candles = _candles(6, end_ts=end)
    candles.append(_candle(candles[-1].timestamp - timedelta(minutes=15), 4600.0))
    provider = _FakeHistory(candles=candles)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._closed_15m = _candles(6, end_ts=end)

    await service.refresh_history()
    # Merge sorts chronologically, so out-of-order is resolved
    assert service._ooo_count == 0
    timestamps = [c.timestamp for c in service._closed_15m]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# WS reconnect backfill + emergency refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ws_reconnect_triggers_backfill(monkeypatch):
    service = LiveMarketDataService(settings=_settings())
    calls = 0

    async def fake_emergency():
        nonlocal calls
        calls += 1
        return {"status": "SUCCESS"}

    monkeypatch.setattr(service, "request_emergency_refresh", fake_emergency)
    await service._on_feed_reconnect()
    assert calls == 1


@pytest.mark.asyncio
async def test_degraded_state_triggers_emergency_refresh(monkeypatch):
    service = LiveMarketDataService(settings=_settings())  # empty -> degraded
    scheduler = AnalysisScheduler(service, settings=_settings())
    refreshed = []

    async def fake_emergency():
        refreshed.append(True)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(service, "request_emergency_refresh", fake_emergency)
    await scheduler._tick()
    assert refreshed == [True]


@pytest.mark.asyncio
async def test_emergency_refresh_cooldown():
    provider = _FakeHistory(candles=_candles(50))
    service = LiveMarketDataService(
        historical_provider=provider,
        settings=_settings(LIVE_HISTORY_EMERGENCY_REFRESH_COOLDOWN_SECONDS=60),
    )
    r1 = await service.request_emergency_refresh()
    assert r1["status"] == "SUCCESS"
    r2 = await service.request_emergency_refresh()
    assert r2["status"] == "SKIPPED"
    assert r2.get("reason") == "cooldown"


# ---------------------------------------------------------------------------
# Live price integrity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rest_refresh_does_not_overwrite_live_price():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    provider = _FakeHistory(candles=_candles(50, end_ts=now - timedelta(minutes=30)))
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._closed_15m = _candles(50, end_ts=now - timedelta(minutes=30))
    await service.on_tick(Tick(symbol="XAUUSD", timestamp=now, bid=4640.0, ask=4640.0))
    assert service._live_price == 4640.0

    await service.refresh_history()
    assert service._live_price == 4640.0  # unchanged by REST


# ---------------------------------------------------------------------------
# Race safety
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_and_on_tick_are_race_safe():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    provider = _FakeHistory(candles=_candles(50, end_ts=now - timedelta(minutes=30)))
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._closed_15m = _candles(50, end_ts=now - timedelta(minutes=30))

    async def ticker():
        for i in range(20):
            await service.on_tick(Tick(symbol="XAUUSD", timestamp=now + timedelta(seconds=i), bid=4630.0 + i, ask=4630.0 + i))

    await asyncio.gather(service.refresh_history(), ticker())
    # No exception, and the closed list is internally consistent
    timestamps = [c.timestamp for c in service._closed_15m]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Safety: failed refresh must not unblock trading; success can restore quality
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_refresh_does_not_unblock_paper_trading():
    from app.core.constants import (
        MarketBias,
        SignalDirection,
        SignalQuality,
        StrategyType,
    )
    from app.risk.admission import TradeAdmissionGate
    from app.signals.models import SignalPayload

    provider = _FakeHistory(candles=_candles(50), fail=True)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._closed_15m = _candles(50, end_ts=datetime.now(timezone.utc) - timedelta(hours=5))  # stale

    await service.refresh_history()
    dq = await service.data_quality()
    assert dq.degraded is True

    gate = TradeAdmissionGate()
    sig = SignalPayload(
        instrument="XAUUSD", direction=SignalDirection.LONG, strategy=StrategyType.CONFLUENCE,
        entry=4600.0, stop_loss=4590.0, take_profit_1=4615.0, take_profit_2=4630.0, take_profit_3=4650.0,
        risk_reward=3.0, confidence_score=85.0, signal_quality=SignalQuality.STRONG,
        market_bias=MarketBias.BULLISH, reasons=["test"],
    )
    decision = await gate.evaluate(sig, data_quality=dq, candle_closed=True, market_data_fresh=True, ai_status_ok=True)
    assert decision.allowed is False
    assert "Data Quality Gate" in decision.rejected_reason


@pytest.mark.asyncio
async def test_successful_backfill_can_restore_data_quality(monkeypatch):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Start with stale data (old candles only)
    stale = _candles(50, end_ts=now - timedelta(hours=5))
    service = LiveMarketDataService(
        historical_provider=_FakeHistory(candles=stale),
        settings=_settings(),
    )
    service._closed_15m = list(stale)
    await service.on_tick(Tick(symbol="XAUUSD", timestamp=now, bid=4600.0, ask=4600.0))

    async def _healthy_health():
        return [{"connected": True, "provider": "binance"}]
    monkeypatch.setattr(service, "health", _healthy_health)

    dq0 = await service.data_quality()
    assert dq0.degraded is True  # stale

    # Now REST returns fresh candles; refresh backfills and restores freshness.
    fresh = _candles(60, end_ts=now - timedelta(minutes=15))
    monkeypatch.setattr(service, "_historical_provider", _FakeHistory(candles=fresh))
    await service.refresh_history()

    dq1 = await service.data_quality()
    assert dq1.historical_fresh is True
    assert dq1.degraded is False
    assert dq1.newest_candle > dq0.newest_candle


# ---------------------------------------------------------------------------
# No synthetic fallback; shutdown; metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_synthetic_csv_fallback_in_live_mode():
    provider = _FakeHistory(candles=[], fail=True)
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    await service._load_historical_base()
    assert service._closed_15m == []
    dq = await service.data_quality()
    assert dq.degraded is True
    assert dq.historical_available is False


@pytest.mark.asyncio
async def test_refresh_task_stops_on_shutdown():
    provider = _FakeHistory(candles=_candles(50))
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    service._running = True
    service._refresh_task = asyncio.create_task(service._history_refresh_loop())
    await asyncio.sleep(0.01)
    assert not service._refresh_task.done()

    await service.stop()
    assert service._refresh_task is None or service._refresh_task.done()


@pytest.mark.asyncio
async def test_data_quality_exposes_refresh_metadata():
    provider = _FakeHistory(candles=_candles(50))
    service = LiveMarketDataService(historical_provider=provider, settings=_settings())
    await service.refresh_history()

    dq = await service.data_quality()
    dump = dq.model_dump(mode="json")
    assert "last_history_refresh_at" in dump
    assert "last_history_refresh_status" in dump
    assert "last_history_refresh_error" in dump
    assert "next_history_refresh_at" in dump
    assert dump["last_history_refresh_status"] == "SUCCESS"
    assert dump["last_history_refresh_error"] is None