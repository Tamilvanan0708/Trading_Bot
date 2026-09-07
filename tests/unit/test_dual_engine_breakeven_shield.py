import pytest
from datetime import datetime, timezone
from app.data.models import Candle
from app.retracement.dual_engine import DualRetracementEngine
from app.retracement.models import RetracementSetup, RetracementState

def _candle(ts_sec: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        timestamp=datetime.fromtimestamp(ts_sec, tz=timezone.utc),
        open=o, high=h, low=l, close=c, volume=10.0
    )

def test_long_breakeven_shield_on_l2_tp():
    # 1. Test 0.618 Default (Entry Breakeven)
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m", smart_shield_level="0.618")
    setup = RetracementSetup(
        setup_id="test_long_shield",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        direction="LONG",
        state=RetracementState.TRADE_ACTIVE,
        low_price=2000.0,
        current_high_price=2100.0,
        point_1_price=2000.0,
        point_2_price=2100.0,
    )
    engine._apply_bullish_fib(setup, 2000.0, 2100.0)
    engine.setup = setup

    # Setup layers: L1 and L2 filled
    setup.layers = {
        "L1": {
            "layer": "L1", "ratio": 0.618, "entry_price": setup.fib_0_618,
            "tp": setup.fib_1_000, "sl": setup.fib_0_236, "state": "FILLED",
            "filled_at": "2026-07-01T00:00:00Z", "lots": 0.01
        },
        "L2": {
            "layer": "L2", "ratio": 0.500, "entry_price": setup.fib_0_500,
            "tp": setup.fib_0_618, "sl": setup.fib_0_236, "state": "FILLED",
            "filled_at": "2026-07-01T00:05:00Z", "lots": 0.01
        }
    }
    setup.sl_price = setup.fib_0_236
    assert setup.sl_price == 2023.60
    assert setup.layers["L1"]["sl"] == 2023.60

    # Candle where price rallies to 2062.00 (>= L2 TP of 2061.80)
    c1 = _candle(1000, 2055.0, 2062.0, 2054.0, 2060.0)
    engine._track_active_trade(c1)

    # L2 must hit TP
    assert setup.layers["L2"]["state"] == "TP_HIT"
    # In 0.618 mode, L1 SL moved to 0.618 Entry Breakeven (2061.80)
    assert setup.layers["L1"]["sl"] == setup.fib_0_618
    assert setup.sl_price == setup.fib_0_618
    assert setup.sl_price == 2061.80

    # 2. Test 0.500 Buffer mode
    engine_buf = DualRetracementEngine(symbol="XAUUSD", timeframe="5m", smart_shield_level="0.500")
    setup_buf = RetracementSetup(
        setup_id="test_long_buf",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        direction="LONG",
        state=RetracementState.TRADE_ACTIVE,
        low_price=2000.0,
        current_high_price=2100.0,
        point_1_price=2000.0,
        point_2_price=2100.0,
    )
    engine_buf._apply_bullish_fib(setup_buf, 2000.0, 2100.0)
    engine_buf.setup = setup_buf
    setup_buf.layers = {
        "L1": {
            "layer": "L1", "ratio": 0.618, "entry_price": setup_buf.fib_0_618,
            "tp": setup_buf.fib_1_000, "sl": setup_buf.fib_0_236, "state": "FILLED",
            "filled_at": "2026-07-01T00:00:00Z", "lots": 0.01
        },
        "L2": {
            "layer": "L2", "ratio": 0.500, "entry_price": setup_buf.fib_0_500,
            "tp": setup_buf.fib_0_618, "sl": setup_buf.fib_0_236, "state": "FILLED",
            "filled_at": "2026-07-01T00:05:00Z", "lots": 0.01
        }
    }
    setup_buf.sl_price = setup_buf.fib_0_236
    engine_buf._track_active_trade(c1)
    assert setup_buf.layers["L2"]["state"] == "TP_HIT"
    assert setup_buf.layers["L1"]["sl"] == setup_buf.fib_0_500
    assert setup_buf.sl_price == 2050.00


def test_short_breakeven_shield_on_l2_tp():
    # 1. Test 0.618 Default (Entry Breakeven)
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m", smart_shield_level="0.618")
    setup = RetracementSetup(
        setup_id="test_short_shield",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        direction="SHORT",
        state=RetracementState.TRADE_ACTIVE,
        low_price=2000.0,
        current_high_price=2100.0,
        point_1_price=2100.0,
        point_2_price=2000.0,
    )
    engine._apply_bearish_fib(setup, 2100.0, 2000.0)
    engine.setup = setup

    # Setup layers: L1 and L2 filled
    setup.layers = {
        "L1": {
            "layer": "L1", "ratio": 0.618, "entry_price": setup.fib_0_618,
            "tp": setup.fib_1_000, "sl": setup.fib_0_236, "state": "FILLED",
            "filled_at": "2026-07-01T00:00:00Z", "lots": 0.01
        },
        "L2": {
            "layer": "L2", "ratio": 0.500, "entry_price": setup.fib_0_500,
            "tp": setup.fib_0_618, "sl": setup.fib_0_236, "state": "FILLED",
            "filled_at": "2026-07-01T00:05:00Z", "lots": 0.01
        }
    }
    setup.sl_price = setup.fib_0_236
    assert setup.sl_price == 2076.40
    assert setup.layers["L1"]["sl"] == 2076.40

    # Candle where price drops to 2038.00 (<= L2 TP of 2038.20)
    c1 = _candle(1000, 2045.0, 2046.0, 2038.0, 2039.0)
    engine._track_active_trade(c1)

    # L2 must hit TP
    assert setup.layers["L2"]["state"] == "TP_HIT"
    # In 0.618 mode, L1 SL lowered to 0.618 Entry Breakeven (2038.20)
    assert setup.layers["L1"]["sl"] == setup.fib_0_618
    assert setup.sl_price == setup.fib_0_618
    assert setup.sl_price == 2038.20

    # 2. Test 0.500 Buffer mode
    engine_buf = DualRetracementEngine(symbol="XAUUSD", timeframe="5m", smart_shield_level="0.500")
    setup_buf = RetracementSetup(
        setup_id="test_short_buf",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        direction="SHORT",
        state=RetracementState.TRADE_ACTIVE,
        low_price=2000.0,
        current_high_price=2100.0,
        point_1_price=2100.0,
        point_2_price=2000.0,
    )
    engine_buf._apply_bearish_fib(setup_buf, 2100.0, 2000.0)
    engine_buf.setup = setup_buf
    setup_buf.layers = {
        "L1": {
            "layer": "L1", "ratio": 0.618, "entry_price": setup_buf.fib_0_618,
            "tp": setup_buf.fib_1_000, "sl": setup_buf.fib_0_236, "state": "FILLED",
            "filled_at": "2026-07-01T00:00:00Z", "lots": 0.01
        },
        "L2": {
            "layer": "L2", "ratio": 0.500, "entry_price": setup_buf.fib_0_500,
            "tp": setup_buf.fib_0_618, "sl": setup_buf.fib_0_236, "state": "FILLED",
            "filled_at": "2026-07-01T00:05:00Z", "lots": 0.01
        }
    }
    setup_buf.sl_price = setup_buf.fib_0_236
    engine_buf._track_active_trade(c1)
    assert setup_buf.layers["L2"]["state"] == "TP_HIT"
    assert setup_buf.layers["L1"]["sl"] == setup_buf.fib_0_500
    assert setup_buf.sl_price == 2050.00


def test_pre_entry_sl_breach_invalidates_setup():
    """If price breaches SL (0.236) before filling any layers, the setup must be invalidated immediately."""
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m")
    # Seed 20 historical candles so engine passes len < 20 check
    for i in range(20):
        engine._candles.append(_candle(i * 300, 2050.0, 2055.0, 2045.0, 2050.0))

    setup = RetracementSetup(
        setup_id="test_pre_entry_sl",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        direction="SHORT",
        state=RetracementState.TP_DYNAMIC,
        point_1_price=2050.0,
        point_2_price=2100.0,
    )
    engine._apply_bearish_fib(setup, high_anchor=2100.0, low_target=2000.0)
    # SL is at fib_0_236 = 2076.40
    engine.setup = setup

    # Price spikes through 2076.40 (SL) without touching L1 (which is at 2061.80) or enters and breaches
    c = _candle(21 * 300, 2070.0, 2080.0, 2069.0, 2078.0)
    engine.process_candle(c)

    assert engine.setup.state == RetracementState.INVALIDATED
    assert "Price breached Stop Loss" in engine.setup.invalidation_reason


def test_opposite_bos_reverses_direction():
    """If waiting for SHORT entry and a Bullish BOS occurs, the SHORT setup is invalidated and a LONG setup is created."""
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m", left_bars=2, right_bars=2)
    setup = RetracementSetup(
        setup_id="stale_short",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        direction="SHORT",
        state=RetracementState.TP_DYNAMIC,
        point_1_price=2020.0,
        point_2_price=2050.0,
    )
    engine._apply_bearish_fib(setup, high_anchor=2050.0, low_target=2000.0)
    engine.setup = setup

    # Feed 25 candles creating a swing low then a swing high, then a candle breaking the swing high (Bullish BOS)
    # Base candles
    candles = []
    base_ts = 10000
    for i in range(20):
        candles.append(_candle(base_ts + i * 300, 2000.0, 2005.0, 1995.0, 2000.0))
    # Swing low at index 20
    candles.append(_candle(base_ts + 20 * 300, 1995.0, 1998.0, 1980.0, 1985.0))
    # Higher candles
    candles.append(_candle(base_ts + 21 * 300, 1985.0, 2010.0, 1985.0, 2005.0))
    candles.append(_candle(base_ts + 22 * 300, 2005.0, 2030.0, 2000.0, 2025.0))  # Swing high = 2030.0
    candles.append(_candle(base_ts + 23 * 300, 2025.0, 2028.0, 2015.0, 2020.0))
    candles.append(_candle(base_ts + 24 * 300, 2020.0, 2026.0, 2018.0, 2022.0))

    for c in candles:
        engine._candles.append(c)

    # Now breakout candle closing above swing high (close > 2030.0)
    breakout_candle = _candle(base_ts + 25 * 300, 2022.0, 2045.0, 2020.0, 2040.0)
    engine.process_candle(breakout_candle)

    # Stale SHORT setup should be superseded and active setup must now be LONG!
    assert engine.setup is not None
    assert engine.setup.direction == "LONG"
    assert engine.setup.bos_price == 2030.0
    assert engine.setup.state == RetracementState.TP_DYNAMIC
    assert len(engine._archived_setups) >= 1
    assert engine._archived_setups[-1].setup_id == "stale_short"

