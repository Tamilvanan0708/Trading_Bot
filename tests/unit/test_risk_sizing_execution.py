import pytest
from starlette.testclient import TestClient
from app.config.execution_settings import (
    ExecutionSettings,
    calculate_lot_size,
    get_execution_settings,
    save_execution_settings,
)
from app.backtesting.strategy_simulator import StrategyBacktester
from app.api.app import create_app


def test_execution_settings_defaults():
    settings = ExecutionSettings()
    assert settings.sizing_mode == "broker_risk"
    assert settings.target_risk_usd == 10.0
    assert settings.fixed_lot_size == 0.01
    assert "4h" not in settings.fib_retracement_timeframes
    assert "5m" in settings.fib_retracement_timeframes
    assert "15m" in settings.fib_retracement_timeframes
    assert "30m" in settings.fib_retracement_timeframes
    assert "1h" in settings.fib_retracement_timeframes
    assert settings.smart_shield_enabled is True


def test_calculate_lot_size():
    # 1. Fixed mode: always returns fixed_lot_size (e.g. 0.01)
    assert calculate_lot_size(entry_px=5000.0, sl_px=4995.0, sizing_mode="fixed", fixed_lot_size=0.01) == 0.01
    assert calculate_lot_size(entry_px=5000.0, sl_px=4970.0, sizing_mode="fixed", fixed_lot_size=0.02) == 0.02

    # 2. Broker risk mode: $10 risk, rounded to 0.01, min 0.01
    # SL distance = 4.0 pts -> 10 / (4 * 100) = 0.025 -> round to 0.03
    lot_5m = calculate_lot_size(entry_px=5000.0, sl_px=4996.0, sizing_mode="broker_risk", target_risk_usd=10.0)
    assert lot_5m == 0.02 or lot_5m == 0.03

    # SL distance = 25.0 pts (1H chart) -> 10 / (25 * 100) = 0.004 -> clamped to min_lot 0.01
    lot_1h = calculate_lot_size(entry_px=5000.0, sl_px=4975.0, sizing_mode="broker_risk", target_risk_usd=10.0)
    assert lot_1h == 0.01


def test_strategy_backtester_sizing_init():
    bt = StrategyBacktester(
        symbol="XAUUSD",
        lot_size=0.02,
        sizing_mode="broker_risk",
        target_risk_usd=15.0,
    )
    assert bt.sizing_mode == "broker_risk"
    assert bt.target_risk_usd == 15.0
    assert bt.lot_size == 0.02

    # Test _calculate_trade_pnl helper
    # 5 points win with 5 points SL at $15 risk
    lot, pnl, r = bt._calculate_trade_pnl(pts=5.0, entry_px=5000.0, sl_px=4995.0)
    assert lot == 0.03  # 15 / (5 * 100) = 0.03
    assert pnl == round(5.0 * 0.03 * 100.0, 2)  # 15.0
    assert r == 1.0


def test_execution_settings_api():
    app = create_app()
    with TestClient(app) as client:
        # 1. GET settings
        r = client.get("/settings/execution")
        assert r.status_code == 200
        data = r.json()
        assert "sizing_mode" in data
        assert "target_risk_usd" in data

        # 2. POST update settings
        r_post = client.post("/settings/execution", json={
            "sizing_mode": "broker_risk",
            "target_risk_usd": 12.5,
            "fixed_lot_size": 0.02,
            "fib_retracement_timeframes": ["5m", "15m", "30m", "1h"],
            "smart_shield_enabled": True
        })
        assert r_post.status_code == 200
        res = r_post.json()
        assert res["status"] == "success"
        assert res["settings"]["target_risk_usd"] == 12.5
        assert res["settings"]["fixed_lot_size"] == 0.02

        # 3. GET verify persistence
        r_after = client.get("/settings/execution")
        assert r_after.status_code == 200
        assert r_after.json()["target_risk_usd"] == 12.5
