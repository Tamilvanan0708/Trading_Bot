"""
Unit tests for the 4 Risk Directions:
1. Higher TF (15M, 30M, 1H) L1 ONLY (L2 & L3 disabled).
2. 5M keeps L1, L2, L3 multi-layer execution.
3. Higher TF L1 Stop Loss fixed at 0.236 (no Smart Shield / BE modification).
4. Spread Filter: Blocks MT5 order execution when live spread exceeds threshold (e.g. 2.0 pts).
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.data.models import Candle
from app.config.execution_settings import ExecutionSettings, get_execution_settings
from app.retracement.fib_retracement_engine import DualRetracementEngine
from app.services.mt5_bridge_manager import MT5BridgeManager


def _make_candle(ts: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        timestamp=datetime.fromisoformat(ts),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


def test_direction_1_and_2_higher_tf_l1_only_vs_5m_multi_layer():
    """Verify 30M fills L1 ONLY on deep pullback, while 5M fills L1, L2, L3."""
    with patch("app.retracement.fib_retracement_engine.get_execution_settings") as mock_cfg:
        settings = ExecutionSettings(
            higher_tf_l1_only=True,
            higher_tf_l1_only_timeframes=["15m", "30m", "1h"],
        )
        mock_cfg.return_value = settings

    # --- 1. Test 30M (Higher TF) -> Must fill L1 ONLY ---
        engine_30m = DualRetracementEngine(timeframe="30m")
        from app.retracement.models import RetracementSetup, RetracementState
        setup_30m = RetracementSetup(
            direction="LONG",
            timeframe="30m",
            state=RetracementState.TRADE_ACTIVE,
            point_2_price=4200.0,
            current_high_price=4300.0,
            fib_0_618=4261.80,
            fib_0_500=4250.00,
            fib_0_382=4238.20,
            fib_0_236=4223.60,
            sl_price=4223.60,
            fib_1_000=4300.00,
            layers={},
        )
        engine_30m.setup = setup_30m

        # Candle that retraces DEEP below 0.382 (low=4230)
        deep_pullback_candle = _make_candle("2026-09-24T01:00:00+00:00", 4290, 4290, 4230.0, 4240)
        fills_30m = engine_30m._fill_long_layers(deep_pullback_candle)

        # 30M should have ONLY filled L1! L2 and L3 must NOT be filled.
        filled_layer_names_30m = [f["layer"] for f in fills_30m]
        assert filled_layer_names_30m == ["L1"], f"Expected ['L1'] only on 30M, got {filled_layer_names_30m}"
        assert "L2" not in setup_30m.layers
        assert "L3" not in setup_30m.layers

        # --- 2. Test 5M (Lower TF) -> Must fill ALL layers L1, L2, L3 ---
        engine_5m = DualRetracementEngine(timeframe="5m")
        setup_5m = RetracementSetup(
            direction="LONG",
            timeframe="5m",
            state=RetracementState.TRADE_ACTIVE,
            point_2_price=4200.0,
            current_high_price=4300.0,
            fib_0_618=4261.80,
            fib_0_500=4250.00,
            fib_0_382=4238.20,
            fib_0_236=4223.60,
            sl_price=4223.60,
            fib_1_000=4300.00,
            layers={},
        )
        engine_5m.setup = setup_5m

        fills_5m = engine_5m._fill_long_layers(deep_pullback_candle)
        filled_layer_names_5m = [f["layer"] for f in fills_5m]
        assert filled_layer_names_5m == ["L1", "L2", "L3"], f"Expected ['L1', 'L2', 'L3'] on 5M, got {filled_layer_names_5m}"
        assert "L1" in setup_5m.layers
        assert "L2" in setup_5m.layers
        assert "L3" in setup_5m.layers


def test_direction_3_smart_shield_skipped_for_higher_tf():
    """Verify Smart Shield does NOT modify L1 SL for higher timeframes."""
    with patch("app.retracement.fib_retracement_engine.get_execution_settings") as mock_cfg:
        settings = ExecutionSettings(
            higher_tf_l1_only=True,
            higher_tf_l1_only_timeframes=["15m", "30m", "1h"],
            smart_shield_enabled=True,
        )
        mock_cfg.return_value = settings

        from app.retracement.models import RetracementSetup, RetracementState
        engine_30m = DualRetracementEngine(timeframe="30m")
        setup = RetracementSetup(
            direction="LONG",
            timeframe="30m",
            state=RetracementState.TRADE_ACTIVE,
            fib_0_618=4261.80,
            fib_0_500=4250.00,
            sl_price=4223.60,
            layers={
                "L1": {
                    "layer": "L1",
                    "state": "FILLED",
                    "entry_price": 4261.80,
                    "sl": 4223.60,
                    "tp": 4300.00,
                },
                # Artificial L2 for testing guard
                "L2": {
                    "layer": "L2",
                    "state": "FILLED",
                    "entry_price": 4250.00,
                    "sl": 4223.60,
                    "tp": 4261.80,
                },
            },
        )
        engine_30m.setup = setup

        # Candle that hits L2 TP
        tp_candle = _make_candle("2026-09-24T01:30:00+00:00", 4255, 4265.0, 4250, 4263)
        engine_30m._track_active_trade(tp_candle)

        # L1 Stop Loss must REMAIN at 4223.60 (0.236), NOT moved to 0.500
        assert setup.layers["L1"]["sl"] == 4223.60, "Higher TF L1 SL should NOT be modified by Smart Shield!"


def test_direction_4_spread_filter_blocks_native_mt5():
    """Verify MT5 native execution blocks order when live spread > max_spread_points."""
    mgr = MT5BridgeManager()

    mock_mt5 = MagicMock()
    mock_mt5.terminal_info.return_value = MagicMock(connected=True)

    # Ask: 4282.50, Bid: 4280.00 -> Spread = 2.50 pts (exceeds max 2.0 pts)
    mock_tick_high_spread = MagicMock()
    mock_tick_high_spread.ask = 4282.50
    mock_tick_high_spread.bid = 4280.00
    mock_mt5.symbol_info_tick.return_value = mock_tick_high_spread

    with patch.dict("sys.modules", {"MetaTrader5": mock_mt5}):
        with patch("app.services.mt5_bridge_manager.get_execution_settings") as mock_cfg:
            mock_cfg.return_value = ExecutionSettings(
                spread_filter_enabled=True,
                max_spread_points=2.0,
            )

            res = mgr._execute_native_mt5_order({
                "id": "order-test-1",
                "symbol": "XAUUSD-VIP",
                "action": "BUY",
                "lot_size": 0.50,
            })

            assert res is not None
            assert res["status"] == "BLOCKED_HIGH_SPREAD"
            assert res["spread"] == 2.50
            mock_mt5.order_send.assert_not_called()


def test_direction_4_spread_filter_allows_normal_spread():
    """Verify MT5 native execution proceeds when live spread <= max_spread_points."""
    mgr = MT5BridgeManager()

    mock_mt5 = MagicMock()
    mock_mt5.terminal_info.return_value = MagicMock(connected=True)

    # Ask: 4280.30, Bid: 4280.00 -> Spread = 0.30 pts (normal spread < 2.0 pts)
    mock_tick_normal = MagicMock()
    mock_tick_normal.ask = 4280.30
    mock_tick_normal.bid = 4280.00
    mock_mt5.symbol_info_tick.return_value = mock_tick_normal

    mock_send_res = MagicMock()
    mock_send_res.retcode = 10009  # TRADE_RETCODE_DONE
    mock_send_res.order = 999123
    mock_send_res.price = 4280.30
    mock_mt5.TRADE_RETCODE_DONE = 10009
    mock_mt5.order_send.return_value = mock_send_res
    mock_mt5.positions_get.return_value = []

    with patch.dict("sys.modules", {"MetaTrader5": mock_mt5}):
        with patch("app.services.mt5_bridge_manager.get_execution_settings") as mock_cfg:
            mock_cfg.return_value = ExecutionSettings(
                spread_filter_enabled=True,
                max_spread_points=2.0,
            )

            res = mgr._execute_native_mt5_order({
                "id": "order-test-2",
                "symbol": "XAUUSD-VIP",
                "action": "BUY",
                "lot_size": 0.50,
            })

            assert res is not None
            assert res["status"] == "FILLED"
            assert res["ticket"] == 999123
            mock_mt5.order_send.assert_called_once()
