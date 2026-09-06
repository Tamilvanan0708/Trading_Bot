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
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m")
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
    # L1 SL must be moved directly to 0.618 Breakeven (2061.80)
    assert setup.layers["L1"]["sl"] == setup.fib_0_618
    assert setup.sl_price == setup.fib_0_618
    assert setup.sl_price == 2061.80

    # Next candle wicks down to 2061.00 (<= 2061.80), triggering L1 SL at Breakeven
    c2 = _candle(1300, 2062.0, 2062.5, 2061.0, 2061.5)
    engine._track_active_trade(c2)

    assert setup.outcome == "SL_HIT"
    assert setup.state == RetracementState.COMPLETED
    assert setup.layers["L1"]["state"] == "SL_HIT"
    # L1 stopped at 2061.80 (Breakeven - 0 loss), while L2 secured +11.80 pts!

def test_short_breakeven_shield_on_l2_tp():
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m")
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
    # L1 SL must be lowered directly to 0.618 Breakeven (2038.20)
    assert setup.layers["L1"]["sl"] == setup.fib_0_618
    assert setup.sl_price == setup.fib_0_618
    assert setup.sl_price == 2038.20
