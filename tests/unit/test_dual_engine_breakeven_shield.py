import pytest
from datetime import datetime, timezone
from app.data.models import Candle
from app.retracement.dual_engine import DualRetracementEngine
from app.retracement.models import RetracementEventType, RetracementSetup, RetracementState

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
    # Structural setup SL must remain at 0.236 (2023.60) to protect multi-tranche setup!
    assert setup.sl_price == setup.fib_0_236
    assert setup.sl_price == 2023.60

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
    assert setup_buf.sl_price == setup_buf.fib_0_236
    assert setup_buf.sl_price == 2023.60


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
    # Structural setup SL must remain at 0.236 (2076.40) to protect multi-tranche setup!
    assert setup.sl_price == setup.fib_0_236
    assert setup.sl_price == 2076.40

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
    assert setup_buf.sl_price == setup_buf.fib_0_236
    assert setup_buf.sl_price == 2076.40


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


def test_l3_fills_after_l2_tp_and_shield_moves_l1():
    """Verify that when L2 hits TP and Smart Shield trails L1 SL to 0.618,
    a subsequent pullback to 0.382 correctly fills L3 without prematurely aborting the setup.
    """
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m", smart_shield_level="0.618")
    setup = RetracementSetup(
        setup_id="test_l3_after_shield",
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

    # fib_0_618 = 2061.80 (L1 entry, L2 TP)
    # fib_0_500 = 2050.00 (L2 entry)
    # fib_0_382 = 2038.20 (L3 entry)
    # fib_0_236 = 2023.60 (Structural SL)
    # fib_1_000 = 2100.00 (L1 TP)
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

    # 1. Price hits L2 TP at 2062.00 (>= 2061.80)
    c1 = _candle(1000, 2055.0, 2062.0, 2054.0, 2060.0)
    engine._track_active_trade(c1)
    assert setup.layers["L2"]["state"] == "TP_HIT"
    assert setup.layers["L1"]["sl"] == 2061.80
    assert setup.sl_price == 2023.60  # Setup SL must NOT have changed!

    # 2. Next candle drops to 2038.00 (touching L3 @ 2038.20), low > 2023.60 (0.236 holds)
    c2 = _candle(1300, 2060.0, 2060.0, 2038.0, 2040.0)
    engine._track_active_trade(c2)

    # L3 must be filled!
    assert "L3" in setup.layers
    assert setup.layers["L3"]["state"] == "FILLED"
    assert setup.layers["L3"]["entry_price"] == 2038.20
    # L1 had trailed SL at 2061.80, so it stops out at breakeven
    assert setup.layers["L1"]["state"] == "SL_HIT"
    # Setup must STILL be active because L3 is open!
    assert setup.state == RetracementState.TRADE_ACTIVE

    # 3. Next candle rallies back to 2062.00 (>= L3 TP of 2061.80)
    c3 = _candle(1600, 2040.0, 2062.0, 2040.0, 2061.0)
    engine._track_active_trade(c3)
    assert setup.layers["L3"]["state"] == "TP_HIT"
    # Now all filled layers (L1 SL_HIT at BE, L2 TP_HIT, L3 TP_HIT) resolved -> setup completed with TP_HIT!
    assert setup.state == RetracementState.COMPLETED
    assert setup.outcome == "TP_HIT"


def test_bullish_bos_anchors_to_immediate_higher_low_not_older_low():
    """Verify that when a series has a previous BOS (from an older low) followed by
    a higher low and a new BOS, the anchor low (Point 2 / 0.000) is anchored to the
    current leg's higher low (e.g. 4374.24) and NOT the older low (4371.09).
    """
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m", left_bars=2, right_bars=2)

    candles = []
    t = 10000

    # 20 flat base candles around 4373
    for i in range(20):
        candles.append(_candle(t + i * 300, 4373.0, 4374.0, 4372.0, 4373.0))

    # Swing Low 1 at index 20: 4371.09
    candles.append(_candle(t + 20 * 300, 4372.0, 4373.0, 4371.09, 4372.0))

    # Rally to Swing High 1 at index 24: 4378.00
    candles.append(_candle(t + 21 * 300, 4372.0, 4374.0, 4372.0, 4373.5))
    candles.append(_candle(t + 22 * 300, 4373.5, 4376.0, 4373.0, 4375.0))
    candles.append(_candle(t + 23 * 300, 4375.0, 4377.0, 4374.5, 4376.5))
    candles.append(_candle(t + 24 * 300, 4376.5, 4378.00, 4376.0, 4377.5))  # SH 1

    # Pullback to Swing Low 2 (Higher Low) at index 27: 4374.24
    candles.append(_candle(t + 25 * 300, 4377.5, 4377.5, 4375.5, 4376.0))
    candles.append(_candle(t + 26 * 300, 4376.0, 4376.0, 4374.5, 4375.0))
    candles.append(_candle(t + 27 * 300, 4375.0, 4375.5, 4374.24, 4375.0))  # SL 2 (HL)
    candles.append(_candle(t + 28 * 300, 4375.0, 4377.0, 4375.0, 4376.5))
    candles.append(_candle(t + 29 * 300, 4376.5, 4379.0, 4376.0, 4378.5))

    # Rally to Swing High 2 at index 32: 4384.14
    candles.append(_candle(t + 30 * 300, 4378.5, 4381.0, 4378.0, 4380.5))
    candles.append(_candle(t + 31 * 300, 4380.5, 4383.0, 4380.0, 4382.5))
    candles.append(_candle(t + 32 * 300, 4382.5, 4384.14, 4382.0, 4383.5))  # SH 2
    candles.append(_candle(t + 33 * 300, 4383.5, 4383.8, 4381.0, 4382.0))
    candles.append(_candle(t + 34 * 300, 4382.0, 4383.5, 4381.5, 4382.5))

    for c in candles:
        engine.process_candle(c)

    # Breakout candle closing above 4384.14 (BOS 2)
    breakout = _candle(t + 35 * 300, 4382.5, 4393.75, 4382.0, 4390.50)
    engine.process_candle(breakout)

    assert engine.setup is not None
    assert engine.setup.direction == "LONG"
    assert engine.setup.bos_price == 4384.14
    # Anchor Low MUST be the immediate Higher Low (4374.24), NOT the older 4371.09
    assert engine.setup.point_2_price == 4374.24
    assert engine.setup.fib_0 == 4374.24


def test_active_l1_trade_not_closed_by_minor_pullback_holds_for_full_target():
    """Verify that an active L1 trade does NOT prematurely exit on a minor pullback
    or opposite swing dip, but safely holds until the full 1.000 Target is reached.
    """
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m", left_bars=2, right_bars=2)
    setup = RetracementSetup(
        setup_id="test_l1_hold_tp",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        direction="LONG",
        state=RetracementState.TRADE_ACTIVE,
        point_1_price=2050.0,
        point_2_price=2000.0,
    )
    engine._apply_bullish_fib(setup, low_anchor=2000.0, high_target=2100.0)
    setup.layers["L1"] = {
        "layer": "L1",
        "entry_ratio": 0.618,
        "entry_price": 2061.80,
        "tp": 2100.00,
        "sl": 2023.60,
        "lots": 0.01,
        "state": "FILLED",
    }
    setup.locked_tp = 2100.00
    setup.tp_locked = True
    engine.setup = setup

    # 1. Minor pullback candle dips and closes lower (e.g. 2055.0 to 2058.0)
    c_pullback = _candle(1500, 2065.0, 2066.0, 2055.0, 2058.0)
    events = engine._track_active_trade(c_pullback)
    # Trade MUST remain active! Must NOT be closed due to OPPOSITE_BOS or early exit
    assert engine.setup.state == RetracementState.TRADE_ACTIVE
    assert engine.setup.layers["L1"]["state"] == "FILLED"
    assert not any(e.event_type == RetracementEventType.SL_HIT for e in events)

    # 2. Next candle pushes up and hits the full 1.000 Target TP (2102.0 >= 2100.0)
    c_tp = _candle(1800, 2060.0, 2105.0, 2059.0, 2102.0)
    tp_events = engine._track_active_trade(c_tp)
    assert engine.setup.layers["L1"]["state"] == "TP_HIT"
    assert engine.setup.state == RetracementState.COMPLETED
    assert engine.setup.outcome == "TP_HIT"
    assert any(e.event_type == RetracementEventType.TP_HIT for e in tp_events)




