import pytest
from unittest.mock import MagicMock
from app.services.mt5_bridge_manager import get_mt5_bridge_manager
from app.config.execution_settings import get_execution_settings


def test_mt5_order_payload_includes_timeframe_and_custom_comment():
    exec_cfg = get_execution_settings()
    exec_cfg.mt5_bridge_enabled = True

    manager = get_mt5_bridge_manager()
    order_data = {
        "id": "test-order-comment-1",
        "symbol": "XAUUSD-VIP",
        "strategy": "Fib Retracement",
        "layer": "L1",
        "timeframe": "30M",
        "comment": "XAU_30M_L1_07:25",
        "direction": "BUY",
        "lot_size": 0.16,
        "entry_price": 4340.76,
        "stop_loss": 4310.39,
        "take_profit_1": 4371.14,
        "paper_trade_id": "pt-test-1",
    }

    res = manager.enqueue_order(order_data)
    assert res["status"] in ("queued", "filled_native")
    payload = res.get("payload")
    assert payload["comment"] == "XAU_30M_L1_07:25"
    assert payload["timeframe"] == "30M"
    assert payload["layer"] == "L1"


def test_mt5_auto_generates_timeframe_comment_if_missing():
    exec_cfg = get_execution_settings()
    exec_cfg.mt5_bridge_enabled = True

    manager = get_mt5_bridge_manager()
    order_data = {
        "id": "test-order-comment-2",
        "symbol": "XAUUSD-VIP",
        "strategy": "Fib Retracement",
        "layer": "L2",
        "timeframe": "15M",
        "direction": "SELL",
        "lot_size": 0.50,
        "entry_price": 4363.47,
        "stop_loss": 4360.37,
        "take_profit_1": 4364.85,
        "paper_trade_id": "pt-test-2",
    }

    res = manager.enqueue_order(order_data)
    payload = res.get("payload")
    assert "XAU_15M_L2" in payload["comment"]
