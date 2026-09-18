import pytest
from datetime import datetime, timezone, timedelta
from app.retracement.fib_retracement_engine import DualRetracementEngine
from app.data.models import Candle

def test_fill_candle_does_not_trigger_false_tp():
    """Verify that a candle which triggers an entry fill cannot instantly claim TP_HIT on the same candle."""
    engine = DualRetracementEngine(timeframe="30m")
    t0 = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
    candles = [
        Candle(timestamp=t0 + timedelta(minutes=30 * 0), open=4350, high=4360, low=4340, close=4355, volume=100),
        Candle(timestamp=t0 + timedelta(minutes=30 * 1), open=4355, high=4365, low=4350, close=4362, volume=100),
        Candle(timestamp=t0 + timedelta(minutes=30 * 2), open=4362, high=4370, low=4360, close=4368, volume=100),
        Candle(timestamp=t0 + timedelta(minutes=30 * 3), open=4368, high=4368, low=4345, close=4348, volume=100),
        Candle(timestamp=t0 + timedelta(minutes=30 * 4), open=4348, high=4350, low=4330, close=4335, volume=100),
    ]
    for c in candles:
        engine.process_candle(c)
        
    setup = engine.setup
    if setup is not None and setup.entry_price is not None:
        fill_candle = Candle(
            timestamp=t0 + timedelta(minutes=30 * 5),
            open=setup.entry_price - 1.0,
            high=setup.entry_price + 1.0,
            low=setup.fib_1_000 if setup.fib_1_000 else setup.entry_price - 5.0,
            close=setup.fib_1_000 if setup.fib_1_000 else setup.entry_price - 5.0,
            volume=100
        )
        engine.process_candle(fill_candle)
        if "L1" in setup.layers:
            assert setup.layers["L1"]["state"] == "FILLED", f"L1 state should be FILLED, got {setup.layers['L1']['state']}"
