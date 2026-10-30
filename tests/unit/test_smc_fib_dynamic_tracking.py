import pytest
from datetime import datetime, timezone
from app.core.constants import SignalDirection
from app.data.models import Candle
from app.retracement.models import RetracementState
from app.retracement.smc_fib_engine import SMCFibEngine


def make_candle(ts_min: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        timestamp=datetime(2026, 10, 30, 10, ts_min, tzinfo=timezone.utc),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


def test_smc_fib_curr_move_zero_before_entry():
    """Verify that current movement points is strictly 0.0 before entry is touched."""
    engine = SMCFibEngine(symbol="XAUUSD", timeframe="5m")
    engine.direction = SignalDirection.SHORT
    engine.state = RetracementState.TP_DYNAMIC
    engine.entry_price = 3988.71
    engine.target_tp_price = 3966.41
    engine.point_2_price = 3999.21
    engine.entry_touched = False

    # Live price crashed way below entry (3914.26)
    data = engine.to_dict(live_price=3914.26)
    assert data["metrics"]["current_movement_pts"] == 0.0, "Movement points must be 0.0 before entry touch"
    assert data["is_entry_touched"] is False


def test_smc_fib_live_price_dynamic_tp_tracking():
    """Verify that update_live_price dynamically expands 0.000 Target TP and Golden Pocket."""
    engine = SMCFibEngine(symbol="XAUUSD", timeframe="5m")
    engine.direction = SignalDirection.SHORT
    engine.state = RetracementState.TP_DYNAMIC
    engine.point_2_price = 3999.21
    engine.target_tp_price = 3966.41
    engine.entry_touched = False
    engine._recompute_fib_levels()

    initial_entry = engine.entry_price
    # Live price drops to 3940.00
    engine.update_live_price(3940.00)

    assert engine.target_tp_price == 3940.00
    # In SHORT: span is wider (3999.21 - 3940.00 = 59.21)
    # Entry at 0.680 is lo + span * 0.680 = 3940 + 59.21 * 0.680 = 3980.26
    assert engine.entry_price < initial_entry
    assert engine.levels["0.000"].price == 3940.00


def test_smc_fib_anchor_rolling_on_extreme_extension():
    """Verify that when span exceeds max_span, engine rolls anchor to recent lower high."""
    engine = SMCFibEngine(symbol="XAUUSD", timeframe="5m")
    # Feed 25 candles creating a downward move with a local lower high at 3966.00
    candles = []
    base_price = 3999.0
    for i in range(25):
        if i == 10:
            # Local lower high at 3966.00
            c = make_candle(i, 3960.0, 3966.0, 3955.0, 3958.0)
        else:
            p = base_price - (i * 3.0)
            c = make_candle(i, p, p + 1.0, p - 2.0, p - 1.0)
        candles.append(c)

    engine._history = list(candles)
    engine.direction = SignalDirection.SHORT
    engine.state = RetracementState.TP_DYNAMIC
    engine.point_2_price = 3999.00
    engine.target_tp_price = 3960.00
    engine.entry_touched = False

    # Now price drops further to 3915 (span = 84 pts, > 35 pts max span for 5m)
    c_new = make_candle(26, 3920.0, 3922.0, 3915.0, 3916.0)
    engine._track_and_check_entry(c_new)

    # Point 2 should have rolled from 3999.00 to the best valid lower high within max_span (3949.00)
    assert engine.point_2_price == 3949.00
    assert engine.target_tp_price == 3915.00
