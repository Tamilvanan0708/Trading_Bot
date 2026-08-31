"""
Market Data API Routes.
"""

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.constants import TimeFrame
from app.data.csv_provider import CsvMarketDataProvider
from app.data.live.service import get_live_service

router = APIRouter(prefix="/market", tags=["Market Data"])

# Global provider instance
_csv_provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")


def get_market_provider() -> CsvMarketDataProvider:
    return _csv_provider


# NOTE: static routes MUST be registered before the parameterized
# /market/{symbol} route to avoid path shadowing.


def _sse(event: str, data: object) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _resolve_chart_payload(
    symbol: str, tf: TimeFrame, *, include_forming: bool = True, fast: bool = False,
) -> dict:
    """Three-tier candle resolution: live in-memory → REST → research cache.

    ``fast=True`` skips the (potentially slow, network-bound) REST tier so the
    Live Market page can paint the persisted cache immediately without waiting
    for a dead/retrying Binance connection.  The SSE generator then upgrades to
    REST/live data in the background.

    Returns a dict with keys: ``candles``, ``closed``, ``forming``, ``data_status``,
    ``current_price``, ``timestamp``, ``candle_count``, ``timeframe``.
    """
    service = get_live_service()
    ts = None
    current_price = None
    data_status = "NO_DATA"

    # 1. Live in-memory snapshot (tick-aggregated + REST base).
    try:
        dq = await service.data_quality()
        feed_connected = dq.connected and not dq.degraded
        series = await service.get_chart_series(symbol, tf, limit=200)
        closed = series.get("closed", [])
        forming = series.get("forming")
        candles = series.get("candles", [])
        if candles:
            current_price = series["current_price"]
            ts = candles[-1].timestamp
            data_status = "HEALTHY" if feed_connected else "HISTORICAL"
            return {
                "symbol": symbol,
                "timeframe": tf.value,
                "timestamp": ts,
                "current_price": current_price,
                "candles": [c.model_dump(mode="json") for c in candles],
                "closed": [c.model_dump(mode="json") for c in closed],
                "forming": forming.model_dump(mode="json") if forming else None,
                "candle_count": len(candles),
                "data_status": data_status,
            }
    except Exception:  # noqa: BLE001
        pass

    # 2. REST historical data with a SHORT deadline (skipped in fast mode).
    if not fast:
        try:
            from app.data.live.binance_history import BinanceHistoryProvider
            from app.config.settings import get_settings
            provider = BinanceHistoryProvider(get_settings())

            async def _fetch_rest():
                return await provider.get_ohlcv(symbol, tf, limit=200)

            rest_candles = await asyncio.wait_for(_fetch_rest(), timeout=5.0)
            if rest_candles:
                candles = [c.model_dump(mode="json") for c in rest_candles]
                current_price = rest_candles[-1].close
                ts = rest_candles[-1].timestamp
                data_status = "HISTORICAL"
                return {
                    "symbol": symbol,
                    "timeframe": tf.value,
                    "timestamp": ts,
                    "current_price": current_price,
                    "candles": candles,
                    "closed": candles,
                    "forming": None,
                    "candle_count": len(candles),
                    "data_status": data_status,
                }
        except Exception:  # noqa: BLE001
            pass

    # 3. Persisted research cache (fast, local — used by fast mode so the page
    #    paints immediately, and as a final fallback in full mode).
    try:
        from app.data.research_fallback import load_research_fallback_candles
        rest_candles = load_research_fallback_candles(symbol, tf, limit=200)
        if rest_candles:
            candles = [c.model_dump(mode="json") for c in rest_candles]
            current_price = rest_candles[-1].close
            ts = rest_candles[-1].timestamp
            data_status = "HISTORICAL_CACHE"
            return {
                "symbol": symbol,
                "timeframe": tf.value,
                "timestamp": ts,
                "current_price": current_price,
                "candles": candles,
                "closed": candles,
                "forming": None,
                "candle_count": len(candles),
                "data_status": data_status,
            }
    except Exception:  # noqa: BLE001
        pass

    return {
        "symbol": symbol,
        "timeframe": tf.value,
        "timestamp": ts,
        "current_price": current_price,
        "candles": [],
        "closed": [],
        "forming": None,
        "candle_count": 0,
        "data_status": "NO_DATA",
    }


# NOTE: static routes MUST be registered before the parameterized
# /market/{symbol} route to avoid path shadowing.


@router.get("/system-status")
async def get_system_status(request: Request):
    """Returns the full runtime status of the trading agent."""
    from app.config.settings import get_settings
    from app.services.status import get_status
    settings = get_settings()
    status = await get_status().snapshot()
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        next_ts = await scheduler.next_analysis_time()
        if next_ts:
            status["next_analysis_at"] = next_ts.isoformat()
        status["scheduler_running"] = scheduler.is_running
    # Safety / mode summary (read-only, non-breaking extension)
    status["observation_mode"] = bool(settings.OBSERVATION_MODE)
    status["paper_trading_enabled"] = bool(settings.PAPER_TRADING_ENABLED)
    status["block_on_failed"] = bool(settings.BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY)
    status["real_money_execution"] = bool(getattr(settings, "REAL_MONEY_EXECUTION", False))
    status["strategy_grade"] = scheduler._read_strategy_grade() if scheduler is not None else None
    # AI provider status (read-only, no secrets)
    try:
        from app.ai.providers import get_provider_status
        providers = get_provider_status(settings)
        active = [p for p, d in providers.items() if d.get("configured")]
        any_healthy = any(d.get("status") == "AVAILABLE" for p, d in providers.items() if d.get("configured"))
        status["ai_provider"] = {
            "status": "AVAILABLE" if any_healthy else ("DEGRADED" if active else "UNAVAILABLE"),
            "configured_providers": active,
            "providers": providers,
            "advisory_only": True,
        }
    except Exception:  # noqa: BLE001
        pass
    return {"status": status}


@router.get("/data-quality")
async def get_data_quality():
    """Returns the current data-quality state of the live analysis pipeline."""
    service = get_live_service()
    dq = await service.data_quality()
    return dq.model_dump(mode="json")


@router.get("/live/health")
async def get_live_health():
    """Returns feed health status for all registered live feeds + AI provider state."""
    service = get_live_service()
    payload = {"feeds": await service.health()}
    try:
        from app.ai.providers import get_provider_status
        from app.config.settings import get_settings
        settings = get_settings()
        providers = get_provider_status(settings)
        active = [p for p, d in providers.items() if d.get("configured")]
        any_healthy = any(d.get("status") == "AVAILABLE" for p, d in providers.items() if d.get("configured"))
        payload["ai_provider"] = {
            "status": "AVAILABLE" if any_healthy else ("DEGRADED" if active else "UNAVAILABLE"),
            "configured_providers": active,
            "providers": providers,
            "advisory_only": True,
        }
    except Exception:  # noqa: BLE001
        pass
    return payload


@router.get("/{symbol}/stream")
async def stream_live_market(symbol: str, timeframe: str = "15m", request: Request = None):
    """Server-Sent Events stream of live candle updates.

    Emits a ``snapshot`` event with the full series immediately, then emits
    ``update`` events carrying only the latest candle(s) as they change (ticks
    mutate the forming candle; a new closed candle appends to the series).  The
    client therefore never needs to reload the full 200-candle series to stay
    live.  Falls back to HISTORICAL / HISTORICAL_CACHE data when the live feed
    is unavailable — the connection and badge never freeze the page.

    The loop reads the in-memory live series (no per-second network calls) and
    only hits REST / research-cache once as a seed when in-memory is empty.
    """
    try:
        tf = TimeFrame(timeframe.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}. Supported: 5m, 15m, 30m, 1h, 4h.")

    async def _live_series_payload() -> dict | None:
        """In-memory live series only (cheap, no network)."""
        service = get_live_service()
        try:
            series = await service.get_chart_series(symbol, tf, limit=200)
            if not series["candles"]:
                return None
            dq = await service.data_quality()
            status = "HEALTHY" if (dq.connected and not dq.degraded) else "HISTORICAL"
            closed = series.get("closed", [])
            forming = series.get("forming")
            candles = series.get("candles", [])
            return {
                "symbol": symbol,
                "timeframe": tf.value,
                "timestamp": candles[-1].timestamp,
                "current_price": series["current_price"],
                "candles": [c.model_dump(mode="json") for c in candles],
                "closed": [c.model_dump(mode="json") for c in closed],
                "forming": forming.model_dump(mode="json") if forming else None,
                "candle_count": len(candles),
                "data_status": status,
            }
        except Exception:  # noqa: BLE001
            return None

    async def event_generator():
        last_key = None
        sent_snapshot = False
        last_sent_status = None
        rest_upgrade_task = None
        while True:
            if request is not None and await request.is_disconnected():
                break
            try:
                # 1. Fast in-memory series (cheap, local).
                payload = await _live_series_payload()

                # 2. Fast seed: research cache only (no network).  Paints the
                #    chart immediately even before the feed/REST is reachable.
                if payload is None and not sent_snapshot:
                    payload = await _resolve_chart_payload(
                        symbol, tf, include_forming=True, fast=True,
                    )

                # 3. One-time background REST upgrade (network-bound, ~5s).
                if payload is not None and payload["candles"] and rest_upgrade_task is None:
                    if payload["data_status"] in ("HISTORICAL_CACHE", "HISTORICAL"):
                        async def _bg_rest():
                            return await _resolve_chart_payload(
                                symbol, tf, include_forming=True, fast=False,
                            )
                        rest_upgrade_task = asyncio.create_task(_bg_rest())

                if rest_upgrade_task is not None and rest_upgrade_task.done():
                    try:
                        rest_payload = rest_upgrade_task.result()
                        if rest_payload and rest_payload["candles"] and rest_payload["data_status"] not in ("NO_DATA", "HISTORICAL_CACHE"):
                            # Send upgraded snapshot only if the data source
                            # changed (e.g. HISTORICAL_CACHE → HISTORICAL).
                            prev_key = last_key
                            payload = rest_payload
                            last_key = None  # force snapshot in next iteration
                    except Exception:  # noqa: BLE001
                        pass
                    rest_upgrade_task = None

                if payload is None or not payload["candles"]:
                    if not sent_snapshot and payload:
                        yield _sse("snapshot", payload)
                        sent_snapshot = True
                        last_sent_status = payload.get("data_status")
                    await asyncio.sleep(2.0)
                    continue

                candles = payload["candles"]
                last = candles[-1]
                key = (
                    payload["data_status"],
                    last["timestamp"],
                    last["open"], last["high"], last["low"], last["close"],
                )
                if key != last_key:
                    last_key = key
                    status_changed = last_sent_status is not None and payload["data_status"] != last_sent_status
                    last_sent_status = payload["data_status"]
                    if not sent_snapshot or status_changed:
                        yield _sse("snapshot", payload)
                        sent_snapshot = True
                    else:
                        # Delta: only the last two candles (newly-closed + forming).
                        yield _sse("update", {
                            "symbol": symbol,
                            "timeframe": tf.value,
                            "data_status": payload["data_status"],
                            "current_price": payload["current_price"],
                            "timestamp": payload["timestamp"],
                            "candles": candles[-2:],
                        })
                await asyncio.sleep(0.5)
            except Exception:  # noqa: BLE001
                await asyncio.sleep(2.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{symbol}/cache")
async def get_cached_market_data(symbol: str, timeframe: str = "15m"):
    """Dedicated LOCAL-CACHE endpoint (never touches the network).

    Serves the persisted real research candles immediately for the Live Market
    chart.  Used by the frontend as the final fallback tier so a temporary SSE
    / REST / Binance failure can NEVER produce a blank ``DATA UNAVAILABLE``
    chart when real historical data is available locally.

    Response mirrors the /live contract (candles, current_price, data_status,
    timestamp, timeframe, candle_count) with data_status = HISTORICAL_CACHE.
    """
    try:
        tf = TimeFrame(timeframe.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}. Supported: 5m, 15m, 30m, 1h, 4h.")

    from app.data.research_fallback import load_research_fallback_candles

    candles = load_research_fallback_candles(symbol, tf, limit=200)
    if not candles:
        raise HTTPException(
            status_code=404,
            detail=f"No cached candle data available for {symbol} {timeframe}. "
                   f"Research dataset is missing or unreadable.",
        )

    current_price = candles[-1].close
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": candles[-1].timestamp,
        "current_price": current_price,
        "candles": [c.model_dump(mode="json") for c in candles],
        "closed": [c.model_dump(mode="json") for c in candles],
        "forming": None,
        "candle_count": len(candles),
        "data_status": "HISTORICAL_CACHE",
    }


@router.get("/{symbol}/quote")
async def get_quote(symbol: str):
    """Lightweight latest-quote endpoint (price/bid/ask/status) for the topbar.

    Avoids transferring the full candle series on every poll.  Independent from
    the chart-data request — if this fails the chart still renders and the
    price simply shows a fallback / dash.  Falls back to the last known close
    from the local research cache so the topbar always has a real price.
    """
    service = get_live_service()
    price = None
    bid = None
    ask = None
    status = "OFFLINE"
    try:
        price = await service.get_latest_price(symbol)
    except Exception:  # noqa: BLE001
        pass
    try:
        tick = await service.registry.latest_tick(symbol)
        if tick is not None:
            bid = tick.bid
            ask = tick.ask
    except Exception:  # noqa: BLE001
        pass
    try:
        feeds = await service.health()
        if feeds and feeds[0].get("connected"):
            status = "HEALTHY"
    except Exception:  # noqa: BLE001
        pass

    # Fall back to the last known close from the local research cache so the
    # topbar always shows a real (non-live) price instead of "—".
    if price is None or price <= 0:
        try:
            from app.data.research_fallback import load_research_fallback_candles
            cached = load_research_fallback_candles(symbol, TimeFrame.M15, limit=1)
            if cached:
                price = cached[-1].close
                if status != "HEALTHY":
                    status = "HISTORICAL_CACHE"
        except Exception:  # noqa: BLE001
            pass

    if price is None or price <= 0:
        raise HTTPException(status_code=404, detail="No market quote available.")
    return {
        "symbol": symbol,
        "price": price,
        "bid": bid,
        "ask": ask,
        "status": status,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/{symbol}/live")
async def get_live_market_data(symbol: str, timeframe: str = "15m", include_forming: bool = False):
    """Retrieves live multi-timeframe candle data.

    ``timeframe`` selects the candle series to return (5m / 15m / 30m / 1h / 4h).
    Defaults to 15m.  Invalid timeframe returns a 400 error.

    Uses the live tick-aggregated snapshot when available.  Falls back to the
    REST historical provider when the feed is offline or the in-memory series
    is empty — so the Live Market chart always has real candles whenever
    Binance REST is reachable.  A final fallback serves REAL persisted research
    candles (HISTORICAL_CACHE) so the chart never shows blank.
    """
    try:
        tf = TimeFrame(timeframe.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}. Supported: 5m, 15m, 30m, 1h, 4h.")

    payload = await _resolve_chart_payload(symbol, tf, include_forming=include_forming)
    candles = payload["candles"]
    if not candles:
        raise HTTPException(status_code=404,
                            detail=f"No candle data available for {symbol} {timeframe}.")
    if not include_forming:
        # Strip the forming candle for callers that require closed-only series.
        payload["candles"] = payload["closed"]

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": payload["timestamp"],
        "current_price": payload["current_price"],
        "candles": payload["candles"],
        "candle_count": len(payload["candles"]),
        "data_status": payload["data_status"],
    }


@router.get("/{symbol}")
async def get_market_data(symbol: str, timeframe: str = "15m", limit: int = 100, provider: CsvMarketDataProvider = Depends(get_market_provider)):
    """Retrieves recent OHLCV candle bars for the requested symbol and timeframe."""
    try:
        tf = TimeFrame(timeframe.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    price = await provider.get_latest_price(symbol)
    candles = await provider.get_ohlcv(symbol, tf, limit=limit)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "latest_price": price,
        "candle_count": len(candles),
        "candles": [c.model_dump() for c in candles],
    }