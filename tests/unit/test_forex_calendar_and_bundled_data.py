"""
Unit tests for Forex 5-day market calendar filter, bundled data loading, and data-range API.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.backtesting.data_loader import (
    filter_forex_trading_days,
    get_available_forex_data_range,
    _load_bundled_master_dataset,
)
from app.data.models import Candle


def _make_candle(dt_iso: str, price: float = 2000.0) -> Candle:
    dt = datetime.fromisoformat(dt_iso).replace(tzinfo=timezone.utc)
    return Candle(
        timestamp=dt,
        open=price,
        high=price + 2.0,
        low=price - 2.0,
        close=price + 1.0,
        volume=100.0,
    )


def test_filter_forex_trading_days_saturday_purged():
    # Saturday 2026-07-11 at 10:00 UTC
    sat_candle = _make_candle("2026-07-11T10:00:00")
    # Friday 2026-07-10 at 14:00 UTC (valid trading hour)
    fri_candle = _make_candle("2026-07-10T14:00:00")
    # Sunday 2026-07-12 at 12:00 UTC (daytime closed)
    sun_day_candle = _make_candle("2026-07-12T12:00:00")
    # Sunday 2026-07-12 at 22:30 UTC (Sydney open, valid)
    sun_night_candle = _make_candle("2026-07-12T22:30:00")
    # Friday 2026-07-10 at 22:15 UTC (after New York close, closed)
    fri_night_candle = _make_candle("2026-07-10T22:15:00")

    candles = [sat_candle, fri_candle, sun_day_candle, sun_night_candle, fri_night_candle]
    filtered = filter_forex_trading_days(candles)

    # Strictly only fri_candle (14:00) should remain. Saturday, Sunday daytime & Sunday night are completely purged!
    assert len(filtered) == 1
    assert fri_candle in filtered
    assert sun_night_candle not in filtered
    assert sat_candle not in filtered
    assert sun_day_candle not in filtered
    assert fri_night_candle not in filtered


def test_get_available_forex_data_range():
    dr = get_available_forex_data_range()
    assert dr["symbol"] == "XAUUSD"
    assert dr["market"] == "FOREX_5DAY"
    assert "2025-12-11" in dr["min_date"]
    assert "2026-09-11" in dr["max_date"]
    assert "15m" in dr["timeframes"]


def test_bundled_master_dataset_loading_and_resampling():
    candles_15m = _load_bundled_master_dataset("15m")
    assert len(candles_15m) > 1000

    candles_30m = _load_bundled_master_dataset("30m")
    assert len(candles_30m) > 500

    candles_1h = _load_bundled_master_dataset("1h")
    assert len(candles_1h) > 200


def test_data_range_api_endpoint():
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/backtest/data-range")
        assert resp.status_code == 200
        data = resp.json()
        assert data["market"] == "FOREX_5DAY"
        assert data["min_date"] == "2025-12-11"
        assert data["max_date"] == "2026-09-11"


@pytest.mark.asyncio
async def test_fib_retracement_lot_size_uses_initial_sl():
    from app.backtesting.strategy_simulator import StrategyBacktester
    b = StrategyBacktester(initial_capital=10000.0, risk_percent=1.0)
    s = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    e = datetime(2026, 7, 1, 23, 59, tzinfo=timezone.utc)
    res = await b.run("FIB_WITH_RETRACEMENT", s, e, timeframe="5m")
    assert len(res["trades"]) > 0
    for t in res["trades"]:
        # Verify that no loss trade exceeds ~1.2% (₹120) of initial capital due to runaway 0.50 lot
        if t["status"] == "LOSS":
            assert abs(t["pnl_usd"]) <= 125.0, f"Trade {t['trade_id']} loss {t['pnl_usd']} exceeded risk limit!"
        # Verify sl_price is never equal to entry_price in record
        assert t["sl_price"] != t["entry_price"]


@pytest.mark.asyncio
async def test_daily_breakdown_zero_sundays():
    from app.backtesting.strategy_simulator import StrategyBacktester
    b = StrategyBacktester(initial_capital=10000.0, risk_percent=1.0)
    s = datetime(2026, 7, 1, tzinfo=timezone.utc)
    e = datetime(2026, 7, 31, 23, 59, tzinfo=timezone.utc)
    res = await b.run("FIB_WITH_RETRACEMENT", s, e, timeframe="5m")
    for d in res["summary"]["daily_breakdown"]:
        dt = datetime.strptime(d["date"], "%Y-%m-%d")
        assert dt.weekday() != 6, f"Sunday found in daily breakdown: {d['date']}"
