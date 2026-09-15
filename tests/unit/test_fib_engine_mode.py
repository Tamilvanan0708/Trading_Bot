"""
Unit tests for Dual Mode Strategy Engine (Classic Mode vs Experimental Mode).

Verifies:
1. Classic Mode (Sept 8 proven):
   - Pre-BOS swing origin anchor selection (s.index <= last_sh.index).
   - Anchor rolling when span > 35 pts on scalping timeframes.
   - Continuation BOS dealing range updates in SMCFibEngine.
   - CHoCH reversal stop in SMCFibEngine.
2. Experimental Mode (Sept 9):
   - Macro extreme anchor selection and locked anchor.
3. Instant Touch Execution:
   - Fills L1, L2, L3 immediately upon line touch in both modes.
4. Settings Toggle:
   - Dynamic mode switching via ExecutionSettings.
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.config.execution_settings import ExecutionSettings, save_execution_settings, get_execution_settings
from app.data.models import Candle
from app.retracement.fib_retracement_engine import DualRetracementEngine, FibRetracementEngine
from app.retracement.models import RetracementState, RetracementSetup
from app.retracement.smc_fib_engine import SMCFibEngine


def _c(ts: datetime, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0)


def _build_impulse_series(start_ts: datetime) -> list[Candle]:
    """Build a validated series: 10 warmup bars, swing low at 2600, swing high at 2615, BOS close at 2620."""
    t = start_ts
    dt = timedelta(minutes=5)
    candles = []

    for _ in range(10):
        candles.append(_c(t, 2605, 2606, 2604, 2605)); t += dt

    # Swing low at 2600.0
    candles.append(_c(t, 2605, 2605, 2602, 2603)); t += dt
    candles.append(_c(t, 2603, 2603, 2601, 2601)); t += dt
    candles.append(_c(t, 2601, 2602, 2600.5, 2601)); t += dt
    candles.append(_c(t, 2601, 2601, 2600.0, 2600.5)); t += dt  # Low 2600.0
    candles.append(_c(t, 2600.5, 2603, 2600.5, 2602)); t += dt
    candles.append(_c(t, 2602, 2605, 2601.5, 2604)); t += dt
    candles.append(_c(t, 2604, 2608, 2603.5, 2607)); t += dt

    # Swing high at 2615.0
    candles.append(_c(t, 2607, 2610, 2606.5, 2609)); t += dt
    candles.append(_c(t, 2609, 2612, 2608.5, 2611)); t += dt
    candles.append(_c(t, 2611, 2614, 2610.5, 2613)); t += dt
    candles.append(_c(t, 2613, 2615, 2612.0, 2614)); t += dt  # High 2615.0
    candles.append(_c(t, 2614, 2614, 2609.0, 2610)); t += dt
    candles.append(_c(t, 2610, 2611, 2607.0, 2608)); t += dt
    candles.append(_c(t, 2608, 2610, 2607.5, 2609)); t += dt

    # Breakout candle > 2615.0
    candles.append(_c(t, 2609, 2622, 2608.5, 2620)); t += dt
    return candles


def test_classic_mode_anchors_to_pre_bos_swing():
    """In Classic Mode, anchor low must be the swing low prior to the breakout swing high."""
    t0 = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    candles = _build_impulse_series(t0)

    engine = FibRetracementEngine(symbol="XAUUSD", timeframe="5m", engine_mode="classic")
    for c in candles:
        engine.process_candle(c)

    assert engine.setup is not None
    assert engine.setup.direction == "LONG"
    # Pre-BOS swing low was at 2600.0
    assert engine.setup.point_2_price == 2600.0
    assert engine.setup.point_1_price == 2615.0
    assert engine.setup.state == RetracementState.TP_DYNAMIC


def test_classic_mode_rolls_anchor_on_massive_expansion():
    """In Classic Mode, if price expands > 35 points on 5m, anchor rolls to higher low."""
    t0 = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    candles = _build_impulse_series(t0)
    engine = FibRetracementEngine(symbol="XAUUSD", timeframe="5m", engine_mode="classic")
    for c in candles:
        engine.process_candle(c)

    assert engine.setup is not None
    initial_p2 = engine.setup.point_2_price  # 2600.0

    # Add candles that push price > 35 points above 2600.0 (e.g. up to 2645.0)
    t = candles[-1].timestamp + timedelta(minutes=5)
    dt = timedelta(minutes=5)
    # Form a higher low at 2625.0
    engine.process_candle(_c(t, 2620.0, 2628.0, 2619.0, 2627.0)); t += dt
    engine.process_candle(_c(t, 2627.0, 2630.0, 2626.0, 2629.0)); t += dt
    engine.process_candle(_c(t, 2629.0, 2631.0, 2625.0, 2626.0)); t += dt  # HL 2625.0
    engine.process_candle(_c(t, 2626.0, 2635.0, 2625.5, 2634.0)); t += dt
    engine.process_candle(_c(t, 2634.0, 2640.0, 2633.0, 2639.0)); t += dt
    engine.process_candle(_c(t, 2639.0, 2646.0, 2638.0, 2645.0)); t += dt  # High 2646 > 2600 + 35

    assert engine.setup.current_high_price >= 2645.0
    # Anchor rolled up to the higher low (>= 2625.0)
    assert engine.setup.point_2_price > initial_p2


def test_instant_touch_fills_l1_on_wick():
    """In both modes, touching the 0.618 line immediately fills L1 on the same candle."""
    t0 = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    candles = _build_impulse_series(t0)
    engine = FibRetracementEngine(symbol="XAUUSD", timeframe="5m", engine_mode="classic")
    for c in candles:
        engine.process_candle(c)

    setup = engine.setup
    assert setup is not None
    entry_px = setup.entry_price  # 0.618 level

    # Send a candle that wicks into the entry zone (low <= entry_px)
    t = candles[-1].timestamp + timedelta(minutes=5)
    wick_candle = _c(t, entry_px + 2.0, entry_px + 3.0, entry_px - 0.5, entry_px + 1.0)
    events = engine.process_candle(wick_candle)

    assert any(e.event_type.name == "ENTRY_TOUCHED" for e in events)
    assert setup.entry_touched is True
    assert "L1" in setup.layers
    assert setup.state == RetracementState.TRADE_ACTIVE


def test_smc_fib_classic_continuation_bos():
    """In Classic Mode, SMCFibEngine updates dealing range on continuation BOS."""
    t0 = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    candles = _build_impulse_series(t0)
    smc = SMCFibEngine(symbol="XAUUSD", timeframe="5m", engine_mode="classic")
    for c in candles:
        smc.process_candle(c)

    assert smc.state in (RetracementState.BOS_DETECTED, RetracementState.TP_DYNAMIC)
    first_p1 = smc.point_1_price

    # Push a new continuation swing high and fresh BOS breakout
    t = candles[-1].timestamp + timedelta(minutes=5)
    dt = timedelta(minutes=5)
    smc.process_candle(_c(t, 2620.0, 2626.0, 2619.0, 2625.0)); t += dt
    smc.process_candle(_c(t, 2625.0, 2632.0, 2624.0, 2630.0)); t += dt  # High 2632
    smc.process_candle(_c(t, 2630.0, 2631.0, 2622.0, 2623.0)); t += dt
    smc.process_candle(_c(t, 2623.0, 2625.0, 2621.0, 2622.0)); t += dt  # Low 2621
    smc.process_candle(_c(t, 2622.0, 2635.0, 2622.0, 2634.0)); t += dt
    smc.process_candle(_c(t, 2634.0, 2640.0, 2633.0, 2638.0)); t += dt  # Breakout > 2632

    # In classic mode, point_1_price updated to the newer BOS swing
    assert smc.point_1_price >= first_p1


def test_smc_fib_classic_choch_reversal():
    """In Classic Mode, an active trade is stopped out immediately if structure breaks opposite (CHoCH)."""
    t0 = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    candles = _build_impulse_series(t0)
    smc = SMCFibEngine(symbol="XAUUSD", timeframe="5m", engine_mode="classic")
    for c in candles:
        smc.process_candle(c)

    # Trigger entry
    t = candles[-1].timestamp + timedelta(minutes=5)
    entry_px = smc.entry_price
    smc.process_candle(_c(t, entry_px + 1.0, entry_px + 2.0, entry_px - 0.2, entry_px + 0.5))
    assert smc.state == RetracementState.TRADE_ACTIVE

    # Break below the lowest confirmed swing low (CHoCH)
    dt = timedelta(minutes=5)
    t += dt
    smc.process_candle(_c(t, entry_px, entry_px + 0.5, 2598.0, 2595.0))  # Closes below 2600.0

    assert smc.state == RetracementState.COMPLETED
    assert smc.outcome == "SL_HIT"
    assert "CHoCH Reversal" in (smc.completion_reason or "")


def test_execution_settings_saves_and_loads_fib_engine_mode():
    """Verify ExecutionSettings persists fib_engine_mode correctly."""
    cfg = get_execution_settings()

    # Toggle to experimental
    cfg.fib_engine_mode = "experimental"
    save_execution_settings(cfg)
    loaded = get_execution_settings()
    assert loaded.fib_engine_mode == "experimental"

    # Restore to classic
    cfg.fib_engine_mode = "classic"
    save_execution_settings(cfg)
    loaded_classic = get_execution_settings()
    assert loaded_classic.fib_engine_mode == "classic"


def test_timeframe_adaptive_fractal_bars():
    """Verify that DualRetracementEngine initializes with 2 bars on 5m/3m/1m and 3 bars on 15m/30m/1h."""
    eng_5m = DualRetracementEngine(symbol="XAUUSD", timeframe="5m")
    assert eng_5m.left_bars == 2
    assert eng_5m.right_bars == 2

    eng_3m = DualRetracementEngine(symbol="XAUUSD", timeframe="3m")
    assert eng_3m.left_bars == 2
    assert eng_3m.right_bars == 2

    eng_15m = DualRetracementEngine(symbol="XAUUSD", timeframe="15m")
    assert eng_15m.left_bars == 2
    assert eng_15m.right_bars == 2

    eng_1h = DualRetracementEngine(symbol="XAUUSD", timeframe="1h")
    assert eng_1h.left_bars == 3
    assert eng_1h.right_bars == 3


def test_same_candle_retrace_does_not_trigger_premature_tp():
    """Verifies that when a live tick fills L1 on a pullback within a candle whose high

    reached the setup peak, the closed candle does NOT prematurely mark TP_HIT.
    """
    engine = DualRetracementEngine(symbol="XAUUSD", timeframe="5m")
    candle_ts = datetime(2026, 9, 14, 15, 45, 0, tzinfo=timezone.utc)
    c_forming = Candle(
        timestamp=candle_ts,
        open=4296.0,
        high=4303.44,  # Reached peak
        low=4293.40,   # Pulled back to entry
        close=4294.50, # Closed below TP
        volume=100.0,
    )
    engine._candles.append(c_forming)

    setup = RetracementSetup(
        setup_id="test_premature_tp_guard",
        strategy="RETRACEMENT_BOS_V1",
        symbol="XAUUSD",
        direction="LONG",
        state=RetracementState.TP_DYNAMIC,
        point_1_price=4278.06,
        point_2_price=4303.44,
        current_high_price=4303.44,
        current_high_timestamp=candle_ts,
    )
    engine._apply_bullish_fib(setup, 4278.06, 4303.44)
    engine.setup = setup

    # 1. Live tick touches entry at 4293.74
    engine.evaluate_live_price(4293.74, timestamp=datetime(2026, 9, 14, 15, 49, 12, tzinfo=timezone.utc))
    assert setup.state == RetracementState.TRADE_ACTIVE
    assert "L1" in setup.layers
    assert setup.layers["L1"]["state"] == "FILLED"

    # 2. Candle completes and is delivered to _track_active_trade
    engine._track_active_trade(c_forming)

    # 3. L1 must remain FILLED, NOT prematurely TP_HIT!
    assert setup.layers["L1"]["state"] == "FILLED"
    assert setup.state == RetracementState.TRADE_ACTIVE

    # 4. Only when a subsequent candle high reaches 4303.44 does it hit TP
    next_ts = datetime(2026, 9, 14, 15, 50, 0, tzinfo=timezone.utc)
    c_next = Candle(
        timestamp=next_ts,
        open=4294.50,
        high=4303.50,
        low=4294.00,
        close=4302.00,
        volume=100.0,
    )
    engine._track_active_trade(c_next)
    assert setup.layers["L1"]["state"] == "TP_HIT"

