"""Unit tests for:
1. Staircase Continuation BOS Rollover (5M).
2. Timeframe-aware crisp expiry thresholds (5M=15 bars, 15M=20 bars, 30M=24 bars, 1H=30 bars).
3. MT5 Direct Pipeline bypassing Binance REST provider.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import TimeFrame
from app.data.models import Candle
from app.retracement.dual_engine import DualRetracementEngine
from app.retracement.models import RetracementEventType, RetracementState
from app.retracement.multi_tf import RetracementMultiTFMonitor

_DT_5M = timedelta(minutes=5)


def _c(ts: datetime, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=50.0)


def _flat_base(n: int, start_ts: datetime, price: float = 4268.0) -> list[Candle]:
    out = []
    for i in range(n):
        out.append(_c(start_ts + i * _DT_5M, price, price + 0.5, price - 0.5, price))
    return out


def test_timeframe_expiry_thresholds():
    """Verify that _max_expiry_bars provides crisp timeframe thresholds on Gold."""
    base_ts = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
    gold_candle = _c(base_ts, 4300.0, 4305.0, 4295.0, 4300.0)

    for tf, expected_bars in [("1m", 15), ("3m", 15), ("5m", 15), ("15m", 20), ("30m", 24), ("1h", 30)]:
        engine = DualRetracementEngine("XAUUSD", tf)
        engine._candles = [gold_candle]
        assert engine._max_expiry_bars() == expected_bars, f"Expected {expected_bars} for {tf}"


def test_5m_setup_expires_after_15_candles():
    """Verify that a 5M setup expires after exactly 15 candles without entry touch."""
    ts = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)
    engine = DualRetracementEngine("XAUUSD", "5m")

    # Prepend 20 baseline candles
    base_candles = _flat_base(20, ts, price=4268.0)
    cur_ts = base_candles[-1].timestamp + _DT_5M

    # Build initial structure: Low @ 4259.5, High @ 4285.5, BOS candle closing @ 4288.0
    structure = [
        _c(cur_ts, 4268, 4271, 4267, 4270),
        _c(cur_ts + _DT_5M, 4270, 4271, 4259.5, 4260),       # Swing Low @ 4259.5
        _c(cur_ts + 2 * _DT_5M, 4260, 4265, 4260.0, 4264),
        _c(cur_ts + 3 * _DT_5M, 4264, 4285.5, 4263.0, 4282), # Swing High @ 4285.5
        _c(cur_ts + 4 * _DT_5M, 4280, 4281.0, 4277.0, 4278),
        _c(cur_ts + 5 * _DT_5M, 4278, 4279.0, 4276.0, 4277),
        _c(cur_ts + 6 * _DT_5M, 4277, 4290.0, 4276.0, 4288), # Breakout close @ 4288 > 4285.5
    ]
    cur_ts += 6 * _DT_5M

    for c in base_candles + structure:
        engine.process_candle(c)

    assert engine.setup is not None
    assert engine.setup.state == RetracementState.TP_DYNAMIC
    assert engine.setup.bos_price == 4285.5

    # Feed 15 candles lingering high without touching entry (~4278)
    for i in range(1, 16):
        cur_ts += _DT_5M
        c = _c(cur_ts, 4288, 4289, 4287, 4288)
        engine.process_candle(c)
        assert engine.setup is not None
        assert engine.setup.state == RetracementState.TP_DYNAMIC

    # 16th candle triggers expiry
    cur_ts += _DT_5M
    exp_candle = _c(cur_ts, 4288, 4289, 4287, 4288)
    engine.process_candle(exp_candle)

    assert engine.setup is not None
    assert engine.setup.state == RetracementState.INVALIDATED
    assert "Setup expired after 15 candles" in engine.setup.invalidation_reason


def test_staircase_continuation_bos_rollover():
    """Verify that when price makes a Higher Low and continuation BOS before entry touch,
    the engine rolls over cleanly to the new Higher Low and resets expiry."""
    ts = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)
    engine = DualRetracementEngine("XAUUSD", "5m")

    # Prepend 20 baseline candles
    base_candles = _flat_base(20, ts, price=4268.0)
    cur_ts = base_candles[-1].timestamp + _DT_5M

    # Step 1: Initial Bullish BOS
    # Low @ 4259.5 -> High @ 4285.5 -> Breakout close @ 4288.0
    structure = [
        _c(cur_ts, 4268, 4271, 4267, 4270),
        _c(cur_ts + _DT_5M, 4270, 4271, 4259.5, 4260),       # Swing Low @ 4259.5
        _c(cur_ts + 2 * _DT_5M, 4260, 4265, 4260.0, 4264),
        _c(cur_ts + 3 * _DT_5M, 4264, 4285.5, 4263.0, 4282), # Swing High @ 4285.5
        _c(cur_ts + 4 * _DT_5M, 4280, 4281.0, 4277.0, 4278),
        _c(cur_ts + 5 * _DT_5M, 4278, 4279.0, 4276.0, 4277),
        _c(cur_ts + 6 * _DT_5M, 4277, 4290.0, 4276.0, 4288), # Breakout close @ 4288 > 4285.5
    ]
    cur_ts += 6 * _DT_5M
    for c in base_candles + structure:
        engine.process_candle(c)

    assert engine.setup is not None
    assert engine.setup.state == RetracementState.TP_DYNAMIC
    first_setup_id = engine.setup.setup_id
    assert engine.setup.point_2_price == 4259.5

    # Step 2: Rally up to peak @ 4301.5, followed by a Higher Low pullback to 4288.5
    rally_candles = [
        _c(cur_ts + _DT_5M, 4288, 4294.0, 4287, 4293),
        _c(cur_ts + 2 * _DT_5M, 4293, 4301.5, 4292, 4300),   # Confirmed peak @ 4301.5
        _c(cur_ts + 3 * _DT_5M, 4300, 4300.0, 4293, 4294),
        _c(cur_ts + 4 * _DT_5M, 4294, 4295.0, 4288.5, 4290), # Pullback dip to 4288.5 (> 4259.5 Higher Low)
        _c(cur_ts + 5 * _DT_5M, 4290, 4293.0, 4289.0, 4292),
    ]
    cur_ts += 5 * _DT_5M
    for c in rally_candles:
        engine.process_candle(c)

    # Step 3: Continuation breakout above 4301.5!
    breakout_candle = _c(cur_ts + _DT_5M, 4292, 4308.0, 4291.0, 4305.0)
    evs = engine.process_candle(breakout_candle)

    # Check that rollover event occurred
    rollover_evs = [e for e in evs if e.metadata and e.metadata.get("rollover")]
    assert len(rollover_evs) == 1
    assert rollover_evs[0].metadata["reason"] == "Rollover to Continuation BOS"

    # Verify new setup anchors
    assert engine.setup is not None
    assert engine.setup.setup_id != first_setup_id
    assert engine.setup.state == RetracementState.TP_DYNAMIC
    assert engine.setup.point_2_price == 4288.5, f"Expected HL anchor 4288.5, got {engine.setup.point_2_price}"
    assert engine.setup.bos_price == 4301.5
    assert engine.setup.dynamic_tp == 4308.0
    assert engine._candles_since_bos == 0

    # Verify old setup was archived
    archived = engine._archived_setups
    assert len(archived) >= 1
    assert archived[-1].setup_id == first_setup_id
    assert archived[-1].invalidation_reason == "Rollover to Continuation BOS."


@pytest.mark.asyncio
async def test_mt5_direct_refresh_bypasses_binance():
    """Verify that when MT5 is enabled, refresh_history never calls Binance REST."""
    from app.config.settings import Settings
    from app.data.live.service import LiveMarketDataService

    settings = Settings(
        LIVE_FEED_PROVIDER="mt5",
        MT5_ENABLED=True,
        MT5_SYMBOL="XAUUSD-VIP",
    )

    sample_candles = [
        _c(datetime.now(timezone.utc) - timedelta(minutes=30), 4300, 4305, 4295, 4302),
        _c(datetime.now(timezone.utc) - timedelta(minutes=15), 4302, 4306, 4298, 4304),
    ]

    service = LiveMarketDataService(settings=settings)
    mock_mt5 = AsyncMock()
    mock_mt5.get_ohlcv = AsyncMock(return_value=sample_candles)
    service._mt5_provider = mock_mt5

    with patch("app.data.live.service.BinanceHistoryProvider") as mock_binance:
        res = await service.refresh_history()
        assert res["status"] == "SUCCESS"
        mock_binance.assert_not_called()
        assert len(service._closed_15m) >= 2


@pytest.mark.asyncio
async def test_mt5_direct_bootstrap_bypasses_binance():
    """Verify that RetracementMultiTFMonitor._bootstrap_from_history uses MT5 and bypasses Binance."""
    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "", "APP_ENV": "production"}), \
         patch("app.config.settings.get_settings") as mock_settings, \
         patch("app.config.execution_settings.get_execution_settings") as mock_exec, \
         patch("app.data.live.service.get_live_service") as mock_live_svc, \
         patch("app.data.live.binance_history.BinanceHistoryProvider") as mock_binance:

        settings_obj = MagicMock(LIVE_FEED_PROVIDER="mt5", MT5_ENABLED=True)
        exec_obj = MagicMock(mt5_bridge_enabled=True)
        mock_settings.return_value = settings_obj
        mock_exec.return_value = exec_obj

        sample_bars = [_c(datetime.now(timezone.utc), 4300, 4305, 4295, 4300)] * 35

        mock_provider = AsyncMock()
        mock_provider._connected = True
        mock_provider.get_ohlcv = AsyncMock(return_value=sample_bars)
        mock_live_svc.return_value = MagicMock(_mt5_provider=mock_provider)

        monitor = RetracementMultiTFMonitor(symbol="XAUUSD-VIP", timeframes=["5m", "15m"])
        result = await monitor._bootstrap_from_history()

        assert "5m" in result
        assert "15m" in result
        mock_binance.assert_not_called()
