"""
Analysis API Routes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TimeFrame
from app.data.csv_provider import CsvMarketDataProvider
from app.data.live.service import LiveMarketDataAdaptor, get_live_service
from app.data.models import Candle
from app.data.research_fallback import load_research_fallback_candles
from app.database.connection import get_db_session
from app.fibonacci.calculator import FibonacciEngine
from app.market_structure.detector import MarketStructureDetector
from app.services.pipeline import AnalysisPipeline
from app.smc.detector import SMCEngine

router = APIRouter(tags=["Market Analysis"])
_provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
_pipeline = AnalysisPipeline(_provider)


@router.get("/analysis/live/{symbol}")
async def get_live_analysis(symbol: str = "XAUUSD", include_forming: bool = False):
    """Runs the full analysis pipeline on live data (closed candles by default).

    The response includes a ``live`` metadata block so the dashboard can
    distinguish live data from historical snapshots without a separate API call.

    When the data-quality gate is degraded (e.g. historical data unavailable),
    the endpoint returns an explicit degraded payload with a NO_TRADE signal
    instead of silently serving synthetic data.
    """
    live_service = get_live_service()

    # Data-quality gate first
    from datetime import datetime, timezone
    data_quality = await live_service.data_quality()
    live_block = await _build_live_block(live_service, symbol)
    live_block["last_analysis_at"] = datetime.now(timezone.utc).isoformat()

    if data_quality.degraded:
        current_price = data_quality.live_price
        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc),
            "current_price": current_price,
            "degraded": True,
            "degradation_reason": data_quality.degradation_reason,
            "data_quality": data_quality.model_dump(mode="json"),
            "market_bias": {
                "4h": {"trend": "NEUTRAL", "summary": "Blocked by data-quality gate."},
                "1h": {"trend": "NEUTRAL", "summary": "Blocked by data-quality gate."},
                "15m": {"trend": "NEUTRAL", "summary": "Blocked by data-quality gate."},
            },
            "fibonacci_setup": None,
            "smc_analysis": {"current_zone": "N/A", "equilibrium_price": 0, "active_fvgs": []},
            "confluence": {"total_score": 0.0, "quality": "NO_TRADE", "is_tradable": False},
            "signal": {
                "direction": "NO_TRADE",
                "strategy": "CONFLUENCE",
                "entry": current_price or 0.0,
                "stop_loss": current_price or 0.0,
                "take_profit_1": current_price or 0.0,
                "take_profit_2": current_price or 0.0,
                "take_profit_3": current_price or 0.0,
                "risk_reward": 0.0,
                "confidence_score": 0.0,
                "signal_quality": "NO_TRADE",
                "reasons": [
                    "Data Quality Gate: FAILED",
                    f"Reason: {data_quality.degradation_reason}",
                    "Live paper trading blocked.",
                ],
            },
            "ai_validation": {
                "status": "REJECT",
                "confidence": 100.0,
                "explanation": "Analysis blocked: historical market data unavailable or degraded.",
                "identified_risks": [data_quality.degradation_reason],
            },
            "explanation": f"NO TRADE — {data_quality.degradation_reason}",
            "live": live_block,
        }

    adaptor = LiveMarketDataAdaptor(live_service, symbol=symbol)
    pipeline = AnalysisPipeline(adaptor)
    try:
        res = await pipeline.run_full_analysis(symbol=symbol, db_session=None)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"Live analysis unavailable: {exc}")

    # Enrich with live feed status
    res["live"] = live_block
    res["live"]["last_analysis_at"] = datetime.now(timezone.utc).isoformat()
    res["data_quality"] = data_quality.model_dump(mode="json")
    return res


async def _build_live_block(live_service, symbol: str) -> dict:
    """Builds the ``live`` metadata block for the analysis response."""
    feed_connected = False
    provider_name = "none"
    bid = None
    ask = None
    try:
        feeds = await live_service.health()
        if feeds:
            f = feeds[0]
            feed_connected = f.get("connected", False)
            provider_name = f.get("provider", "unknown")
            lt = f.get("latest_tick")
            if lt:
                bid = lt.get("bid")
                ask = lt.get("ask")
    except Exception:
        pass

    from app.services.status import get_status
    last_tick_at = None
    try:
        last_tick_at = (await get_status().snapshot()).get("last_tick_at")
    except Exception:
        pass

    return {
        "feed_connected": feed_connected,
        "provider": provider_name,
        "bid": bid,
        "ask": ask,
        "last_tick_at": last_tick_at,
        "last_analysis_at": None,
        "history_fallback": False,
    }


@router.get("/analysis/{symbol}")
async def get_analysis(symbol: str = "XAUUSD"):
    """Runs read-only multi-timeframe analysis without persisting."""
    res = await _pipeline.run_full_analysis(symbol=symbol, db_session=None)
    return res


@router.post("/analysis/run")
async def trigger_analysis_run(symbol: str = "XAUUSD", db: AsyncSession = Depends(get_db_session)):
    """Triggers an active analysis run, validates with AI, notifies Telegram, and persists results."""
    res = await _pipeline.run_full_analysis(symbol=symbol, db_session=db)
    return {"status": "success", "result": res}


@router.get("/analysis/ai/providers")
async def get_ai_providers_status():
    """Returns the current status of all AI validation providers (no secrets).

    Example:
      {"providers": {"GROQ": {"configured": true, "status": "AVAILABLE"}, ...},
       "status": "AVAILABLE" | "DEGRADED" | "UNAVAILABLE",
       "advisory_only": true}
    """
    from app.ai.providers import get_provider_status
    from app.config.settings import get_settings
    settings = get_settings()
    providers = get_provider_status(settings)
    active = [p for p, d in providers.items() if d.get("configured")]
    any_healthy = any(d.get("status") == "AVAILABLE" for p, d in providers.items() if d.get("configured"))
    return {
        "status": "AVAILABLE" if any_healthy else ("DEGRADED" if active else "UNAVAILABLE"),
        "configured_providers": active,
        "providers": providers,
        "advisory_only": True,
    }


@router.get("/structure/{symbol}")
async def get_structure(symbol: str = "XAUUSD", timeframe: str = "1h"):
    """Fetches Market Structure (HH, HL, LH, LL, trend) for specific timeframe."""
    tf = TimeFrame(timeframe.lower())
    candles, data_status = await _fetch_candles_with_status(symbol, tf)
    if not candles:
        return {"data_status": "NO_DATA", "detail": "No candle data available for analysis."}
    detector = MarketStructureDetector()
    result = detector.analyze(candles, tf).model_dump()
    result["data_status"] = data_status
    return result


@router.get("/fibonacci/{symbol}")
async def get_fibonacci(symbol: str = "XAUUSD", timeframe: str = "30m"):
    """Fetches Fibonacci retracement and Golden Pocket levels."""
    tf = TimeFrame(timeframe.lower())
    candles, data_status = await _fetch_candles_with_status(symbol, tf)
    if not candles:
        return {"data_status": "NO_DATA", "detail": "No candle data available for analysis."}
    fib_eng = FibonacciEngine()
    setup = fib_eng.evaluate_setup(candles)
    if setup:
        result = setup.model_dump()
        result["data_status"] = data_status
        return result
    return {"data_status": data_status, "status": "No active Fibonacci setup found"}


@router.get("/smc/{symbol}")
async def get_smc(symbol: str = "XAUUSD", timeframe: str = "1h"):
    """Fetches SMC patterns (BOS, CHoCH, Order Blocks, FVGs, Liquidity Sweeps)."""
    tf = TimeFrame(timeframe.lower())
    candles, data_status = await _fetch_candles_with_status(symbol, tf)
    if not candles:
        return {"data_status": "NO_DATA", "detail": "No candle data available for analysis."}
    smc_eng = SMCEngine()
    result = smc_eng.analyze(candles, tf).model_dump()
    result["data_status"] = data_status
    return result


async def _fetch_candles_with_status(symbol: str, tf: TimeFrame, limit: int = 200) -> tuple[list[Candle], str]:
    """Fetches candles preferring live data, then research cache, then CSV."""
    try:
        adaptor = LiveMarketDataAdaptor(get_live_service(), symbol=symbol)
        candles = await adaptor.get_ohlcv(symbol, tf, limit=limit)
        if candles:
            return candles, "HEALTHY"
    except Exception:
        pass

    cached = load_research_fallback_candles(symbol, tf, limit=limit)
    if cached:
        return cached, "HISTORICAL_CACHE"

    try:
        candles = await _provider.get_ohlcv(symbol, tf, limit=limit)
        if candles:
            return candles, "HISTORICAL"
    except Exception:
        pass

    return [], "NO_DATA"
