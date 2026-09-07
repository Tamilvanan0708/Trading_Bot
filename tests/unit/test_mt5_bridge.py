import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.config.execution_settings import ExecutionSettings, get_execution_settings, save_execution_settings
from app.services.mt5_bridge_manager import get_mt5_bridge_manager


@pytest.fixture(autouse=True)
def reset_bridge():
    mgr = get_mt5_bridge_manager()
    mgr.clear_all()
    # Enable bridge in settings for test
    cfg = get_execution_settings()
    cfg.mt5_bridge_enabled = True
    save_execution_settings(cfg)
    yield
    mgr.clear_all()


def test_strict_strategy_filtering():
    """Verify that ONLY Fib Retracement orders are queued for MT5, and other strategies are blocked."""
    mgr = get_mt5_bridge_manager()

    # 1. Fib Retracement - MUST BE QUEUED
    res_fib = mgr.enqueue_order({
        "id": "test-fib-1",
        "strategy": "Fib Retracement",
        "layer": "L1",
        "direction": "LONG",
        "symbol": "XAUUSD",
        "lot_size": 0.30,
        "entry_price": 4435.0,
        "stop_loss": 4432.0,
        "take_profit_1": 4445.0,
    })
    assert res_fib["status"] == "queued"
    assert res_fib["order_id"] == "test-fib-1"

    # 2. SMC With Fib - MUST BE BLOCKED
    res_smc = mgr.enqueue_order({
        "id": "test-smc-1",
        "strategy": "SMC With Fib",
        "direction": "LONG",
        "symbol": "XAUUSD",
        "lot_size": 0.20,
    })
    assert res_smc["status"] == "filtered"
    assert "Fib Retracement only" in res_smc["reason"]

    # 3. Fib Go With Trend - MUST BE BLOCKED
    res_trend = mgr.enqueue_order({
        "id": "test-trend-1",
        "strategy": "Fib Go With Trend",
        "direction": "LONG",
        "symbol": "XAUUSD",
        "lot_size": 0.20,
    })
    assert res_trend["status"] == "filtered"

    # Only 1 order should be pending
    pending = mgr.pop_pending_orders(symbol="XAUUSD")
    assert len(pending) == 1
    assert pending[0]["strategy"] == "Fib Retracement"


def test_lot_size_clamping_on_mt5():
    """Verify that MT5 orders respect the 0.50 Lot ceiling."""
    mgr = get_mt5_bridge_manager()

    res = mgr.enqueue_order({
        "id": "test-clamp-1",
        "strategy": "Fib Retracement",
        "lot_size": 5.0,  # Runaway lot
        "direction": "BUY",
        "symbol": "XAUUSD",
    })
    assert res["status"] == "queued"
    assert res["payload"]["lot_size"] == 0.50  # Hard capped at 0.50


def test_heartbeat_and_status():
    """Verify heartbeat tracking and online/offline status."""
    mgr = get_mt5_bridge_manager()
    assert mgr.is_online is False

    # Receive EA heartbeat
    mgr.record_heartbeat({
        "account_login": 1305493,
        "server": "VTMarkets-Demo",
        "balance": 1000.0,
        "equity": 1000.0,
        "leverage": 500,
        "symbol": "XAUUSD",
    })

    assert mgr.is_online is True
    status = mgr.get_status()
    assert status["is_online"] is True
    assert status["connected_account"]["login"] == 1305493
    assert status["connected_account"]["server"] == "VTMarkets-Demo"
    assert status["allowed_strategy"] == "Fib Retracement"


def test_execution_reporting():
    """Verify execution report saves fill details."""
    mgr = get_mt5_bridge_manager()

    mgr.enqueue_order({
        "id": "order-123",
        "strategy": "Fib Retracement",
        "lot_size": 0.33,
        "direction": "BUY",
    })

    mgr.record_execution_report({
        "order_id": "order-123",
        "ticket": 987654321,
        "status": "FILLED",
        "fill_price": 4435.25,
    })

    status = mgr.get_status()
    assert len(status["recent_executions"]) == 1
    assert status["recent_executions"][0]["ticket"] == 987654321
    assert status["recent_executions"][0]["fill_price"] == 4435.25


def test_api_endpoints():
    """Test FastAPI routes for MT5 Bridge."""
    app = create_app()
    client = TestClient(app)

    # 1. Heartbeat
    hb_res = client.post("/api/mt5/heartbeat", json={
        "account_login": 1305493,
        "server": "VTMarkets-Demo",
        "balance": 1000.0,
        "equity": 1000.0,
        "leverage": 500,
        "symbol": "XAUUSD"
    })
    assert hb_res.status_code == 200
    assert hb_res.json()["status"] == "ok"

    # 2. Status
    st_res = client.get("/api/mt5/status")
    assert st_res.status_code == 200
    assert st_res.json()["is_online"] is True
    assert st_res.json()["allowed_strategy"] == "Fib Retracement"

    # 3. Test Trade Trigger
    tt_res = client.post("/api/mt5/test-trade", json={
        "action": "BUY",
        "lots": 0.01,
        "sl_points": 3.0,
        "tp_points": 5.0
    })
    assert tt_res.status_code == 200
    assert tt_res.json()["status"] == "queued"
    order_id = tt_res.json()["order_id"]

    # 4. Pending Orders (consumed by EA)
    po_res = client.get("/api/mt5/pending-orders?symbol=XAUUSD")
    assert po_res.status_code == 200
    assert po_res.json()["orders_count"] == 1
    assert po_res.json()["orders"][0]["id"] == order_id
    assert po_res.json()["orders"][0]["strategy"] == "Fib Retracement"

    # 5. Execution Report
    er_res = client.post("/api/mt5/execution-report", json={
        "order_id": order_id,
        "ticket": 55667788,
        "status": "FILLED",
        "fill_price": 4436.10
    })
    assert er_res.status_code == 200
    assert er_res.json()["ticket"] == 55667788

    # 6. Download EA file
    ea_res = client.get("/api/mt5/download-ea")
    assert ea_res.status_code == 200
    assert "XAU_AI_Bridge" in ea_res.text


def test_enqueue_close_and_modify():
    """Verify that close and modify orders can be enqueued and tracked by paper trade id or ticket."""
    mgr = get_mt5_bridge_manager()

    # 1. Enqueue Open Order with paper_trade_id
    res_open = mgr.enqueue_order({
        "id": "ord-open-1",
        "strategy": "Fib Retracement",
        "paper_trade_id": "pt-xyz-123",
        "direction": "SELL",
        "symbol": "XAUUSD",
        "lot_size": 0.15,
        "entry_price": 4392.06,
        "stop_loss": 4398.82,
        "take_profit_1": 4385.31,
    })
    assert res_open["status"] == "queued"

    # 2. Simulate MT5 filling with ticket 672226252
    mgr.record_execution_report({
        "order_id": "ord-open-1",
        "ticket": 672226252,
        "status": "FILLED",
        "fill_price": 4391.12,
    })
    assert mgr._paper_trade_to_ticket["pt-xyz-123"] == 672226252

    # 3. Enqueue Modify (Smart Shield Breakeven)
    res_mod = mgr.enqueue_modify(
        paper_trade_id="pt-xyz-123",
        symbol="XAUUSD",
        new_sl=4392.06,
    )
    assert res_mod["status"] == "queued"
    assert res_mod["payload"]["action"] == "MODIFY"
    assert res_mod["payload"]["ticket"] == 672226252
    assert res_mod["payload"]["stop_loss"] == 4392.06

    # 4. Enqueue Close (TP Hit)
    res_close = mgr.enqueue_close(
        paper_trade_id="pt-xyz-123",
        symbol="XAUUSD",
        reason="TP_HIT",
    )
    assert res_close["status"] == "queued"
    assert res_close["payload"]["action"] == "CLOSE"
    assert res_close["payload"]["ticket"] == 672226252
    assert res_close["payload"]["reason"] == "TP_HIT"

    # 5. Verify pending orders returned to EA
    pending = mgr.pop_pending_orders(symbol="XAUUSD")
    assert len(pending) == 3
    assert pending[0]["action"] == "SELL"
    assert pending[1]["action"] == "MODIFY"
    assert pending[2]["action"] == "CLOSE"

