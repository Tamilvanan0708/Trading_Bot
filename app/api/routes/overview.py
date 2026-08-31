"""
Overview Dashboard API Route.

Aggregates all live trading dashboard data into a single payload:
market price, data status, market regime, MTF directions, SMC, Fibonacci,
signal, AI validation, trading safety, system health, and latest update.
"""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.config.settings import Settings, get_settings
from app.core.constants import MarketBias, TimeFrame
from app.data.live.binance_history import BinanceHistoryProvider
from app.data.live.service import get_live_service
from app.data.research_fallback import load_research_fallback_candles
from app.market_regime.detector import MarketRegimeDetector
from app.market_structure.detector import MarketStructureDetector
from app.services.status import get_status
from app.api.routes.analysis import get_live_analysis

router = APIRouter(prefix="/overview", tags=["Overview Dashboard"])


@router.get("/{symbol}")
async def overview_dashboard(symbol: str = "XAUUSD", request: Request = None):
    """Aggregated real-time overview payload for the command-center dashboard.

    Returns a single JSON object containing all sections:
    market price, data_status, regime, MTF, SMC, Fibonacci, signal,
    AI validation, safety, system health, and market update.
    """
    service = get_live_service()
    settings = get_settings()
    scheduler = getattr(request.app.state, "scheduler", None) if request is not None else None
    now = datetime.now(timezone.utc)

    # ── 1. Market snapshot + data_status (fallback chain) ──────────────────
    market = await _resolve_market(symbol, service, settings, timeout=2.5)
    data_status = market["data_status"]
    current_price = market["price"]

    # ── 2. Analysis (best effort; time-bounded so the dashboard never waits
    #       on the full pipeline. If the pipeline is slow or unavailable the
    #       analysis-dependent sections show WAITING / UNAVAILABLE — never
    #       NEUTRAL or 0.)
    analysis = None
    try:
        analysis = await asyncio.wait_for(
            get_live_analysis(symbol=symbol),
            timeout=3.0,
        )
    except (asyncio.TimeoutError, HTTPException):
        analysis = None  # sections will show WAITING / UNAVAILABLE

    # ── 3. Market regime ──────────────────────────────────────────────────
    regime = await _resolve_regime(symbol, service, analysis)

    # ── 4. MTF directions (5m/15m/30m/1h/4h) ──────────────────────────────
    mtf = await _resolve_mtf(symbol, service, analysis)

    # ── 5. SMC summary ────────────────────────────────────────────────────
    smc = _resolve_smc(analysis, data_status)

    # ── 6. Fibonacci summary ──────────────────────────────────────────────
    fib = _resolve_fibonacci(analysis)

    # ── 7. Signal summary ─────────────────────────────────────────────────
    signal = _resolve_signal(analysis, data_status, market)

    # ── 8. AI validation ──────────────────────────────────────────────────
    ai = _resolve_ai(analysis)

    # ── 9. Trading safety status ──────────────────────────────────────────
    safety = await _resolve_safety(settings, service, scheduler)

    # ── 10. System health ─────────────────────────────────────────────────
    health = await _resolve_health(service, settings, scheduler, analysis)

    # ── 11. Latest market update ──────────────────────────────────────────
    market_update = _resolve_market_update(market, data_status)

    return {
        "symbol": symbol,
        "generated_at": now.isoformat(),
        "data_status": data_status,
        "market": market,
        "regime": regime,
        "mtf": mtf,
        "smc": smc,
        "fibonacci": fib,
        "signal": signal,
        "ai_validation": ai,
        "safety": safety,
        "health": health,
        "market_update": market_update,
    }


# ═══════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════


async def _resolve_market(
    symbol: str, service, settings: Settings, timeout: float = 2.5
) -> dict:
    """Market-price snapshot with 3-tier fallback (live → REST → research cache)."""
    tf = TimeFrame.M15
    candles = []
    data_status = "NO_DATA"
    current_price = None
    ts = None
    bid = ask = None
    provider = "none"

    # 1. Live in-memory snapshot
    try:
        snap = await service.get_multi_timeframe_snapshot(symbol, include_forming=False)
        series = snap.get_series(tf)
        if series:
            candles = [{"timestamp": c.timestamp.isoformat(), "open": c.open,
                        "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
                       for c in series[-200:]]
            current_price = snap.current_price
            ts = snap.timestamp.isoformat()
            data_status = "HEALTHY"
    except Exception:
        pass

    # Bid/ask from feed health (live tick)
    try:
        feeds = await service.health()
        if feeds:
            f = feeds[0]
            provider = f.get("provider", "none")
            lt = f.get("latest_tick")
            if lt:
                bid = lt.get("bid")
                ask = lt.get("ask")
    except Exception:
        pass

    # 2. Research cache fallback (fast, local — the dashboard must paint
    #    immediately when the feed is offline, not wait for a REST timeout).
    if not candles:
        try:
            rest = load_research_fallback_candles(symbol, tf, limit=200)
            if rest:
                current_price = rest[-1].close
                ts = rest[-1].timestamp.isoformat()
                candles = [{"timestamp": c.timestamp.isoformat(), "open": c.open,
                            "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
                           for c in rest[-200:]]
                data_status = "HISTORICAL_CACHE"
        except Exception:
            pass

    # 3. REST fallback (only if no cache either — avoids a multi-second wait
    #    on a dead network for every dashboard refresh).
    if not candles:
        try:
            rest_provider = BinanceHistoryProvider(settings)

            async def _fetch():
                return await rest_provider.get_ohlcv(symbol, tf, limit=200)

            rest = await asyncio.wait_for(_fetch(), timeout=timeout)
            if rest:
                current_price = rest[-1].close
                ts = rest[-1].timestamp.isoformat()
                candles = [{"timestamp": c.timestamp.isoformat(), "open": c.open,
                            "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
                           for c in rest[-200:]]
                data_status = "HISTORICAL"
        except Exception:
            pass

    # Price change vs previous candle close
    change = change_pct = None
    if current_price is not None and candles:
        prev_close = candles[-2]["close"] if len(candles) >= 2 else candles[-1]["close"]
        if prev_close and prev_close > 0:
            change = round(current_price - prev_close, 2)
            change_pct = round((change / prev_close) * 100, 2)

    # Spread
    spread = round(ask - bid, 2) if bid is not None and ask is not None else None

    return {
        "price": current_price,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "change": change,
        "change_pct": change_pct,
        "timestamp": ts,
        "candles": len(candles),
        "last_candle": candles[-1]["timestamp"] if candles else None,
        "timeframe": "15m",
        "provider": provider,
        "data_status": data_status,
    }


async def _resolve_regime(symbol: str, service, analysis: dict | None) -> dict:
    """Market regime from the regime detector engine on m15 data."""
    updated_at = None
    regime = None
    trend = None
    volatility_pct = None
    details = None

    # Try to get regime from the scheduler's cached result (analysis)
    h4_bias = MarketBias.NEUTRAL
    if analysis is not None:
        mb = analysis.get("market_bias", {})
        h4 = (mb.get("4h", {}) or {}).get("trend", "NEUTRAL") if mb else "NEUTRAL"
        try:
            h4_bias = MarketBias(h4)
        except ValueError:
            h4_bias = MarketBias.NEUTRAL

    m15_series = None
    try:
        snap = await service.get_multi_timeframe_snapshot(symbol, include_forming=False)
        m15_series = snap.m15
        updated_at = snap.timestamp.isoformat()
    except Exception:
        # Fall back to persisted research cache (real data).
        try:
            m15_series = load_research_fallback_candles(symbol, TimeFrame.M15, limit=400)
        except Exception:
            m15_series = None

    if m15_series:
        try:
            detector = MarketRegimeDetector()
            reg = detector.analyze(m15_series, trend=h4_bias)
            regime = reg.regime.value
            trend = reg.trend.value
            volatility_pct = reg.volatility_pct
            details = reg.details
        except Exception:
            pass

    if not regime:
        return {
            "regime": "UNKNOWN",
            "trend": "NEUTRAL",
            "volatility_pct": None,
            "details": None,
            "timeframe": "15m",
            "updated_at": updated_at,
        }

    return {
        "regime": regime,
        "trend": trend,
        "volatility_pct": volatility_pct,
        "details": details,
        "timeframe": "15m",
        "updated_at": updated_at,
    }


async def _resolve_mtf(symbol: str, service, analysis: dict | None) -> dict:
    """Multi-timeframe direction for 5m/15m/30m/1h/4h via MarketStructureDetector.

    Prefers the live snapshot series; falls back to persisted research-cache
    candles (real data) when the live series is unavailable.
    """
    series_map: dict[str, tuple] = {}
    result = {}
    try:
        snap = await service.get_multi_timeframe_snapshot(symbol, include_forming=False)
        series_map = {
            "15m": (TimeFrame.M15, snap.m15),
            "30m": (TimeFrame.M30, snap.m30),
            "1h": (TimeFrame.H1, snap.h1),
            "4h": (TimeFrame.H4, snap.h4),
        }
    except Exception:
        pass

    # Fill any timeframe missing from the live snapshot with research cache.
    for tf_label, tf_enum in (("5m", TimeFrame.M5), ("15m", TimeFrame.M15),
                              ("30m", TimeFrame.M30), ("1h", TimeFrame.H1),
                              ("4h", TimeFrame.H4)):
        if tf_label not in series_map:
            try:
                fb = load_research_fallback_candles(symbol, tf_enum, limit=200)
                if fb:
                    series_map[tf_label] = (tf_enum, fb)
            except Exception:
                pass

    detector = MarketStructureDetector()
    for tf_label, (tf_enum, candles) in series_map.items():
        if not candles:
            result[tf_label] = {"trend": "NO_DATA", "summary": "No candle data."}
            continue
        try:
            struct = detector.analyze(candles, tf_enum)
            result[tf_label] = {
                "trend": struct.trend.value,
                "summary": struct.summary,
                "atr": struct.atr,
                "ema_trend": struct.ema_trend.value if struct.ema_trend else None,
            }
        except Exception:
            result[tf_label] = {"trend": "NO_DATA", "summary": "Analysis failed."}

    return result


def _resolve_smc(analysis: dict | None, data_status: str) -> dict:
    """SMC summary from the analysis pipeline's smc_analysis section."""
    if analysis is None:
        return {"status": "NO_DATA", "setup": False}

    smc_raw = analysis.get("smc_analysis")
    if not smc_raw or not isinstance(smc_raw, dict):
        return {"status": "NO_VALID_SMC_SETUP", "setup": False}

    if data_status not in ("HEALTHY", "HISTORICAL"):
        return {"status": "SMC_DATA_STALE", "setup": False,
                "detail": "Market data is not fresh enough for SMC analysis."}

    # Extract key SMC fields
    current_zone = smc_raw.get("current_zone", "N/A")
    eq_price = smc_raw.get("equilibrium_price")
    latest_break_raw = smc_raw.get("latest_break")
    latest_break = None
    if latest_break_raw and isinstance(latest_break_raw, dict):
        latest_break = {
            "break_type": latest_break_raw.get("break_type"),
            "broken_level": latest_break_raw.get("broken_level"),
            "break_price": latest_break_raw.get("break_price"),
            "description": latest_break_raw.get("description"),
        }
    active_fvgs = smc_raw.get("active_fvgs", []) or []
    active_obs = smc_raw.get("active_order_blocks", []) or []
    liquidity_pools = smc_raw.get("liquidity_pools", []) or []
    recent_sweeps = smc_raw.get("recent_sweeps", []) or []

    buy_side = sum(1 for p in liquidity_pools if p.get("pool_type") in ("BUY_SIDE_LIQUIDITY", "EQUAL_HIGHS"))
    sell_side = sum(1 for p in liquidity_pools if p.get("pool_type") in ("SELL_SIDE_LIQUIDITY", "EQUAL_LOWS"))

    return {
        "status": "ACTIVE",
        "setup": True,
        "current_zone": current_zone,
        "equilibrium_price": eq_price,
        "latest_break": latest_break,
        "active_fvg_count": len(active_fvgs),
        "active_ob_count": len(active_obs),
        "recent_sweep_count": len(recent_sweeps),
        "buy_side_liquidity_pools": buy_side,
        "sell_side_liquidity_pools": sell_side,
        "summary": smc_raw.get("summary", ""),
    }


def _resolve_fibonacci(analysis: dict | None) -> dict:
    """Fibonacci summary from the analysis pipeline's fibonacci_setup section."""
    if analysis is None:
        return {"status": "NO_DATA", "setup": False}

    fib_raw = analysis.get("fibonacci_setup")
    if not fib_raw or not isinstance(fib_raw, dict):
        return {"status": "NO_RETRACEMENT_SETUP", "setup": False}

    if not fib_raw.get("valid", False):
        return {"status": "NO_RETRACEMENT_SETUP", "setup": False,
                "reason": fib_raw.get("reason", "Setup not valid.")}

    in_golden = fib_raw.get("in_golden_pocket", False)
    levels = fib_raw.get("levels", {})
    current_price = fib_raw.get("current_price")

    return {
        "status": "GOLDEN_ZONE_ACTIVE" if in_golden else "ACTIVE",
        "setup": True,
        "direction": fib_raw.get("direction"),
        "in_golden_pocket": in_golden,
        "entry_zone_min": fib_raw.get("entry_zone_min"),
        "entry_zone_max": fib_raw.get("entry_zone_max"),
        "current_price": current_price,
        "level_50": levels.get(0.5) if isinstance(levels, dict) else None,
        "level_618": levels.get(0.618) if isinstance(levels, dict) else None,
        "level_786": levels.get(0.786) if isinstance(levels, dict) else None,
        "swing_high": fib_raw.get("swing_high"),
        "swing_low": fib_raw.get("swing_low"),
        "reason": fib_raw.get("reason", ""),
    }


def _resolve_signal(analysis: dict | None, data_status: str, market: dict) -> dict:
    """Signal summary from the analysis pipeline."""
    if analysis is None:
        return {
            "status": "DATA_UNAVAILABLE",
            "direction": "NO_TRADE",
            "reasons": ["No analysis data available. Market data status: " + data_status],
        }

    sig = analysis.get("signal")
    if not sig:
        return {"status": "DATA_UNAVAILABLE", "direction": "NO_TRADE"}

    direction = sig.get("direction", "NO_TRADE")
    deg = analysis.get("degraded", False)
    if deg:
        return {
            "status": "NO_TRADE",
            "direction": "NO_TRADE",
            "reasons": sig.get("reasons", ["Data quality gate: degraded."]),
            "data_quality": analysis.get("data_quality"),
        }

    confidence = sig.get("confidence_score", 0)
    quality = sig.get("signal_quality", "NO_TRADE")

    if direction == "NO_TRADE":
        return {
            "status": "NO_TRADE",
            "direction": "NO_TRADE",
            "confidence_score": confidence,
            "signal_quality": quality,
            "reasons": sig.get("reasons", []),
        }

    return {
        "status": direction,
        "direction": direction,
        "entry": sig.get("entry"),
        "stop_loss": sig.get("stop_loss"),
        "take_profit_1": sig.get("take_profit_1"),
        "take_profit_2": sig.get("take_profit_2"),
        "take_profit_3": sig.get("take_profit_3"),
        "risk_reward": sig.get("risk_reward"),
        "confidence_score": confidence,
        "signal_quality": quality,
        "strategy": sig.get("strategy"),
        "reasons": sig.get("reasons", []),
        "explanation": sig.get("explanation", ""),
    }


def _resolve_ai(analysis: dict | None) -> dict:
    """AI validation summary from the analysis pipeline."""
    if analysis is None:
        return {"status": "WAITING", "confidence": None, "explanation": "No analysis available."}

    ai = analysis.get("ai_validation")
    if not ai:
        return {"status": "WAITING", "confidence": None, "explanation": "AI validation not yet run."}

    return {
        "status": ai.get("status", "WAITING"),
        "confidence": ai.get("confidence"),
        "explanation": ai.get("explanation", ""),
        "identified_risks": ai.get("identified_risks", []),
        "missing_confirmations": ai.get("missing_confirmations", []),
        "provider": ai.get("provider", "HEURISTIC"),
        "model": ai.get("model", ""),
        "reason_code": ai.get("reason_code", ""),
        "advisory_only": ai.get("advisory_only", True),
    }


async def _resolve_safety(settings: Settings, service, scheduler) -> dict:
    """Trading safety status from settings + system state."""
    st = await get_status().snapshot()
    dq = await service.data_quality()
    grade = None
    if scheduler is not None:
        try:
            grade = scheduler._read_strategy_grade()
        except Exception:
            grade = "INCONCLUSIVE"

    # Hard gates
    real_money_enabled = settings.REAL_MONEY_EXECUTION
    paper_trading_enabled = settings.PAPER_TRADING_ENABLED
    observation_mode = settings.OBSERVATION_MODE
    block_on_failed = settings.BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY
    strategy_failed = (grade == "FAILED") if block_on_failed else False
    data_degraded = dq.degraded
    scheduler_running = bool(scheduler.is_running) if scheduler is not None else False

    gates = [
        {"gate": "REAL_MONEY_EXECUTION", "pass": not real_money_enabled,
         "label": "Real Money Execution", "status": "DISABLED" if not real_money_enabled else "ENABLED"},
        {"gate": "PAPER_TRADING", "pass": paper_trading_enabled,
         "label": "Paper Trading", "status": "ENABLED" if paper_trading_enabled else "DISABLED"},
        {"gate": "OBSERVATION_MODE", "pass": not observation_mode,
         "label": "Observation Mode", "status": "ACTIVE" if observation_mode else "OFF"},
        {"gate": "STRATEGY_GRADE", "pass": not strategy_failed,
         "label": "Strategy Grade", "status": grade or "INCONCLUSIVE"},
        {"gate": "DATA_QUALITY", "pass": not data_degraded,
         "label": "Data Quality", "status": "DEGRADED" if data_degraded else "HEALTHY"},
        {"gate": "SCHEDULER", "pass": scheduler_running,
         "label": "Scheduler", "status": "RUNNING" if scheduler_running else "STOPPED"},
    ]

    all_pass = all(g["pass"] for g in gates)

    if real_money_enabled:
        headline = "REAL_MONEY_ENABLED"
    elif observation_mode:
        headline = "OBSERVATION_MODE"
    elif data_degraded:
        headline = "SIGNALS_BLOCKED"
    elif strategy_failed or not paper_trading_enabled:
        headline = "PAPER_TRADING_BLOCKED"
    elif all_pass:
        headline = "SIGNALS_ENABLED"
    else:
        headline = "SIGNALS_BLOCKED"

    return {
        "status": headline,
        "all_gates_pass": all_pass,
        "gates": gates,
        "strategy_grade": grade,
    }


async def _resolve_health(service, settings: Settings, scheduler, analysis: dict | None) -> dict:
    """System health for all services."""
    st = await get_status().snapshot()
    dq = await service.data_quality()
    feeds = []
    try:
        feeds = await service.health()
    except Exception:
        pass

    feed = feeds[0] if feeds else {}

    # DB health (lightweight ping)
    db_healthy = False
    try:
        from sqlalchemy import text
        from app.database.connection import async_session_factory
        async with async_session_factory() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=2.0)
            db_healthy = True
    except Exception:
        db_healthy = False

    feed_connected = feed.get("connected", False)
    scheduler_running = bool(scheduler.is_running) if scheduler is not None else False
    analysis_ok = st.get("last_analysis_at") is not None

    services = [
        {"name": "BINANCE", "status": "HEALTHY" if feed_connected else "OFFLINE",
         "detail": f"{feed.get('ticks_cached', 0)} ticks cached" if feed_connected else "disconnected",
         "healthy": feed_connected},
        {"name": "DATABASE", "status": "HEALTHY" if db_healthy else "OFFLINE",
         "detail": "SQLite reachable" if db_healthy else "unreachable",
         "healthy": db_healthy},
        {"name": "SCHEDULER", "status": "RUNNING" if scheduler_running else "STOPPED",
         "detail": f"last analysis: {st.get('last_analysis_at', 'never')}",
         "healthy": scheduler_running},
        {"name": "DATA_PIPELINE", "status": "HEALTHY" if not dq.degraded else "DEGRADED",
         "detail": f"{dq.candle_count} candles, gaps={dq.gap_count}",
         "healthy": not dq.degraded},
        {"name": "MTF_ANALYSIS", "status": "HEALTHY" if analysis_ok else "WAITING",
         "detail": f"last score: {st.get('last_analysis_score', '—')}",
         "healthy": analysis_ok},
        {"name": "SMC_ENGINE", "status": "HEALTHY" if analysis_ok else "WAITING",
         "detail": "engine available" if analysis_ok else "first analysis pending",
         "healthy": analysis_ok},
        {"name": "FIBONACCI_ENGINE", "status": "HEALTHY" if analysis_ok else "WAITING",
         "detail": "engine available" if analysis_ok else "first analysis pending",
         "healthy": analysis_ok},
        {"name": "SIGNAL_ENGINE", "status": "HEALTHY" if analysis_ok else "WAITING",
         "detail": f"last signal: {st.get('last_signal_at', 'none')}",
         "healthy": analysis_ok},
        {"name": "AI_VALIDATION", "status": "HEALTHY" if analysis_ok else "WAITING",
         "detail": f"provider: {settings.AI_PROVIDER}",
         "healthy": analysis_ok},
        {"name": "OUTCOME_TRACKING", "status": "HEALTHY" if db_healthy else "OFFLINE",
         "detail": "DB-backed" if db_healthy else "unavailable",
         "healthy": db_healthy},
    ]

    return {
        "services": services,
        "all_healthy": all(s["healthy"] for s in services),
        "last_tick_at": st.get("last_tick_at"),
        "last_error": st.get("last_error"),
    }


def _resolve_market_update(market: dict, data_status: str) -> dict:
    """Latest market data update info."""
    return {
        "data_source": data_status,
        "last_candle": market.get("last_candle"),
        "last_update": market.get("timestamp"),
        "timeframe": "15m",
        "candle_count": market.get("candles", 0),
        "provider": market.get("provider"),
    }