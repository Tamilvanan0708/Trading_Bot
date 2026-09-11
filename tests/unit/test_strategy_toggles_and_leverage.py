import pytest
from app.config.execution_settings import (
    ExecutionSettings,
    calculate_lot_size,
    calculate_margin_required,
)


def test_execution_settings_defaults():
    cfg = ExecutionSettings()
    assert cfg.account_leverage == 500
    assert cfg.strategy_fib_retracement is True
    assert cfg.strategy_smc_fib is False
    assert cfg.strategy_fib_trend is False
    assert cfg.smart_shield_enabled is True
    assert cfg.smart_shield_level == "0.618"
    assert cfg.sizing_mode == "broker_risk"
    assert cfg.account_currency == "cent"
    assert cfg.risk_percent == 1.0


def test_margin_required_cent_mode():
    # In Cent mode (₹ INR equivalent):
    # 0.30 lots of Gold at $4435 with 1:500 leverage:
    # 0.30 * 100 * 4435 / 500 / 100 = $2.66 -> in cents: $2.66 * 100 = 266.10 cents (or ₹266.10)
    m_500 = calculate_margin_required(lot_size=0.30, gold_price=4435.0, leverage=500, account_currency="cent")
    assert round(m_500, 2) == 266.10

    # At 1:1000 leverage (half the margin):
    m_1000 = calculate_margin_required(lot_size=0.30, gold_price=4435.0, leverage=1000, account_currency="cent")
    assert round(m_1000, 2) == 133.05

    # At 1:2000 leverage:
    m_2000 = calculate_margin_required(lot_size=0.30, gold_price=4435.0, leverage=2000, account_currency="cent")
    assert round(m_2000, 2) == 66.53

    # At 1:100 leverage:
    m_100 = calculate_margin_required(lot_size=0.30, gold_price=4435.0, leverage=100, account_currency="cent")
    assert round(m_100, 2) == 1330.50


def test_margin_required_usd_mode():
    # In USD mode:
    # 0.01 lots of Gold at $4435 with 1:500 leverage:
    # 0.01 * 100 * 4435 / 500 = $8.87
    m_500 = calculate_margin_required(lot_size=0.01, gold_price=4435.0, leverage=500, account_currency="usd")
    assert round(m_500, 2) == 8.87


def test_dynamic_vs_fixed_lot_sizing():
    # Fixed lot mode
    lot_fixed = calculate_lot_size(
        entry_px=4435.0,
        sl_px=4425.0,
        sizing_mode="fixed",
        fixed_lot_size=0.05,
    )
    assert lot_fixed == 0.05

    # Dynamic cent compounding
    lot_dynamic = calculate_lot_size(
        entry_px=4435.0,
        sl_px=4432.0,
        sizing_mode="broker_risk",
        risk_mode="percent",
        risk_percent=1.0,
        account_balance=10000.0,
        account_currency="cent",
    )
    assert lot_dynamic == 0.33


def test_lot_size_tight_sl_clamp_and_max_ceiling():
    # Tight SL distance (0.1 pt gap) - should be clamped to 2.0 pt floor and capped at 0.50 lot max
    lot_tight = calculate_lot_size(
        entry_px=4435.0,
        sl_px=4434.9,
        sizing_mode="broker_risk",
        risk_mode="percent",
        risk_percent=1.0,
        account_balance=10000.0,
    )
    assert lot_tight == 0.50

    # Even with 0 pt or virtually zero difference
    lot_zero = calculate_lot_size(
        entry_px=4435.0,
        sl_px=4435.0,
        sizing_mode="broker_risk",
        risk_mode="percent",
        risk_percent=1.0,
        account_balance=10000.0,
    )
    assert lot_zero == 0.50


def test_fib_retracement_multi_slot_parallel_response():
    """Verify that Fib Retracement endpoint returns multi-slot timeframes without locking standby blanking."""
    from fastapi.testclient import TestClient
    from app.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        res = client.get("/retracement/strategy/fib-retracement/XAUUSD")
        assert res.status_code == 200
        data = res.json()
        assert "timeframes" in data
        assert "active_trade_tfs" in data
        tfs = data["timeframes"]
        for tf in ["5m", "15m", "30m", "1h"]:
            assert tf in tfs
            card = tfs[tf]
            # No timeframe should have locked by cascade set to true
            assert card.get("is_locked_by_cascade") is False
            assert "STANDBY" not in str(card.get("cascade_status", ""))


def test_signals_endpoint_dynamic_lots():
    """Verify that /signals returns lot_size for signals."""
    from fastapi.testclient import TestClient
    from app.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        res = client.get("/signals")
        assert res.status_code == 200
        sigs = res.json()
        assert isinstance(sigs, list)
        for s in sigs:
            assert "lot_size" in s
            assert float(s["lot_size"]) > 0
            assert "timeframe" in s
            assert s["timeframe"] is not None

        # Confirm multiple timeframes are represented in signals
        tfs = {s["timeframe"].lower() for s in sigs if s.get("timeframe")}
        assert len(tfs) > 1, f"Expected multiple timeframes, got: {tfs}"


def test_fib_retracement_dashboard_paper_trade_sync():
    """Verify that Fib Retracement dashboard provides accurate paper_trade telemetry."""
    from fastapi.testclient import TestClient
    from app.api.app import create_app

    app = create_app()
    with TestClient(app) as client:
        res = client.get("/retracement/strategy/fib-retracement/XAUUSD")
        assert res.status_code == 200
        data = res.json()
        assert "timeframes" in data
        assert "active_paper_trade_tfs" in data
        assert isinstance(data["active_paper_trade_tfs"], list)

        for tf in ["5m", "15m", "30m", "1h"]:
            assert tf in data["timeframes"]
            card = data["timeframes"][tf]
            assert "paper_trade" in card
            assert "is_open" in card["paper_trade"]


@pytest.mark.asyncio
async def test_fib_retracement_dashboard_reflects_active_order(in_memory_db):
    """Verify that an active open trade in the DB is properly surfaced on its timeframe."""
    import uuid
    from datetime import datetime, timezone
    from app.database.models import PaperTradeModel
    from app.api.routes.retracement import get_fib_retracement_dashboard

    # Insert a 15M open paper trade
    trade = PaperTradeModel(
        id=str(uuid.uuid4()),
        signal_id="FIB_RETR_15M_L1_4393_123456",
        symbol="XAUUSD",
        direction="SHORT",
        state="OPEN",
        lot_size=0.03,
        risk_amount=30.0,
        target_entry=4378.28,
        actual_entry=4378.28,
        stop_loss=4415.68,
        take_profit_1=4340.87,
        take_profit_2=4340.87,
        take_profit_3=4340.87,
        opened_at=datetime.now(timezone.utc),
    )
    in_memory_db.add(trade)
    await in_memory_db.commit()

    res = await get_fib_retracement_dashboard(symbol="XAUUSD", db=in_memory_db)
    assert "15m" in res["active_paper_trade_tfs"]
    assert res["primary_paper_trade_tf"] == "15m"

    # 15M card should show paper_trade.is_open == True
    card_15m = res["timeframes"]["15m"]
    assert card_15m["paper_trade"]["is_open"] is True
    assert card_15m["paper_trade"]["lot_size"] == 0.03
    assert card_15m["paper_trade"]["direction"] == "SHORT"

    # 1H and 30M cards should show paper_trade.is_open == False
    assert res["timeframes"]["1h"]["paper_trade"]["is_open"] is False
    assert res["timeframes"]["30m"]["paper_trade"]["is_open"] is False


def test_execution_settings_mtime_reload(tmp_path, monkeypatch):
    """Test that modifying execution_settings.json dynamically updates get_execution_settings()."""
    import json
    import time
    from app.config import execution_settings as es_module

    test_file = tmp_path / "execution_settings.json"
    monkeypatch.setattr(es_module, "CONFIG_FILE", test_file)
    monkeypatch.setattr(es_module, "_CURRENT_SETTINGS", None)
    monkeypatch.setattr(es_module, "_LAST_CONFIG_MTIME", 0.0)

    # Initial save
    initial = es_module.ExecutionSettings(strategy_fib_retracement=True, strategy_smc_fib=False, strategy_fib_trend=False)
    es_module.save_execution_settings(initial)

    loaded_1 = es_module.get_execution_settings()
    assert loaded_1.strategy_fib_retracement is True
    assert loaded_1.strategy_smc_fib is False

    # Simulate modifying file from UI/external process
    time.sleep(0.05)  # ensure distinct mtime timestamp
    with open(test_file, "w", encoding="utf-8") as f:
        json.dump({"strategy_fib_retracement": True, "strategy_smc_fib": True, "strategy_fib_trend": False}, f)

    # Calling get_execution_settings() without restarting must pick up the new value
    loaded_2 = es_module.get_execution_settings()
    assert loaded_2.strategy_smc_fib is True

