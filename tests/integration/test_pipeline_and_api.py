"""
Integration tests for FastAPI endpoints, Pipeline, and Paper Trading.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.core.constants import TimeFrame, TradeState
from app.data.csv_provider import CsvMarketDataProvider
from app.paper_trading.service import PaperTradingService
from app.services.pipeline import AnalysisPipeline


@pytest.mark.asyncio
async def test_fastapi_endpoints():
    from app.database.connection import init_db
    await init_db()

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "healthy"

        # 2. Market
        res = await client.get("/market/XAUUSD?timeframe=15m&limit=20")
        assert res.status_code == 200
        assert res.json()["candle_count"] == 20

        # 3. Analysis (read-only)
        res = await client.get("/analysis/XAUUSD")
        assert res.status_code == 200
        data = res.json()
        assert "confluence" in data
        assert "signal" in data
        assert "ai_validation" in data

        # 4. Structure / Fib / SMC routes
        res = await client.get("/structure/XAUUSD?timeframe=1h")
        assert res.status_code == 200
        assert "trend" in res.json()

        res = await client.get("/fibonacci/XAUUSD?timeframe=30m")
        assert res.status_code == 200

        res = await client.get("/smc/XAUUSD?timeframe=1h")
        assert res.status_code == 200

        # 5. Backtest endpoint
        bt_res = await client.post("/backtest", json={"symbol": "XAUUSD", "initial_balance": 10000.0, "risk_percent": 1.0, "limit_bars": 300})
        assert bt_res.status_code == 200
        bt_data = bt_res.json()
        assert "backtest_id" in bt_data
        assert "summary" in bt_data

        # 6. Dashboard (premium terminal)
        dash_res = await client.get("/dashboard")
        assert dash_res.status_code == 200
        assert "XAU AI" in dash_res.text
        assert "Signal Intelligence Terminal" in dash_res.text

        # 7. Live analysis endpoint: with no live data it must return an explicit
        #    degraded state (never the synthetic CSV sample price).
        from app.data.live.service import _live_service_instance
        if _live_service_instance is not None:
            _live_service_instance._closed_15m = []
            _live_service_instance._live_price = None
        live_res = await client.get("/analysis/live/XAUUSD")
        if live_res.status_code == 200:
            live_data = live_res.json()
            if live_data.get("degraded"):
                # Explicit degraded state — paper trading blocked, no synthetic price.
                assert "NO_TRADE" in live_data["signal"]["direction"]
                assert "degradation_reason" in live_data
            else:
                # Healthy live data must be a real XAUUSDT price (>$3000),
                # never the synthetic $2660s sample.
                assert live_data["current_price"] > 3000
        else:
            assert live_res.status_code == 404


@pytest.mark.asyncio
async def test_historical_analysis_endpoint_returns_sample_data():
    """The historical /analysis/XAUUSD endpoint serves the CSV sample (~$2660s)."""
    from app.database.connection import init_db
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/analysis/XAUUSD")
        assert res.status_code == 200
        data = res.json()
        assert "current_price" in data
        assert data["current_price"] < 3000  # sample data ~$2660s


@pytest.mark.asyncio
async def test_paper_trading_service():
    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    snapshot = await provider.get_multi_timeframe_snapshot("XAUUSD")
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=50)

    service = PaperTradingService(initial_balance=10000.0)
    pipeline = AnalysisPipeline(provider)
    res = await pipeline.run_full_analysis("XAUUSD")

    sig = pipeline.signal_engine.generate_signal(snapshot)
    if sig.is_tradable:
        pos = await service.open_position_from_signal(sig)
        assert pos is not None
        assert pos.state == TradeState.PENDING

        # Advance candles
        for c in candles:
            await service.on_candle(c)

        all_pos = service.get_all_positions()
        assert len(all_pos) > 0
