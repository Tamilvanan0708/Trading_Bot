"""
FastAPI Main Application Factory.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    ai,
    analysis,
    backtest,
    dashboard,
    market,
    overview,
    paper_trades,
    research,
    retracement,
    signals,
    terminal,
)
from app.config.settings import get_settings
from app.core.logging import logger
from app.data.live.service import get_live_service
from app.data.live.tradingview_webhook import router as webhook_router
from app.database.connection import init_db
from app.services.scheduler import AnalysisScheduler
from app.services.status import get_status


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize Database tables + live service + analysis scheduler
    await init_db()
    live_service = get_live_service()
    await live_service.start()
    scheduler = AnalysisScheduler(live_service)
    app.state.scheduler = scheduler
    await scheduler.start()
    await get_status().mark_started()
    logger.info("SCHEDULER: single-worker deployment required — run uvicorn with --workers 1.")
    logger.info("Application startup complete.")
    yield
    # Shutdown
    await scheduler.stop()
    await live_service.stop()
    logger.info("Application shutdown complete.")


def create_app() -> FastAPI:
    """Creates and configures the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="XAU/USD Multi-Timeframe Trading AI Agent API",
        description="Deterministic Multi-Timeframe Quantitative Trading Engine with AI Sanity & Confluence Validation for Gold (XAU/USD).",
        version="1.0.0",
        lifespan=lifespan,
    )

    origins = settings.CORS_ALLOW_ORIGINS or ["*"]
    allow_credentials = "*" not in origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    async def root_index():
        from app.api.routes.terminal import serve_terminal_index
        return await serve_terminal_index()

    @app.api_route("/health", methods=["GET", "HEAD"], tags=["Health"])
    async def health_check(request: Request):
        import asyncio

        from app.data.live.service import get_live_service

        live_service = get_live_service()
        db_ok = False
        feed_connected = False
        scheduler_running = False
        data_status = "NO_DATA"

        # Database reachability (time-bounded so a dead DB never hangs /health)
        try:
            from sqlalchemy import text
            from app.database.connection import async_session_factory
            async with async_session_factory() as session:
                await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=2.0)
                db_ok = True
        except Exception:  # noqa: BLE001
            db_ok = False

        # Live feed connection
        try:
            feeds = await live_service.health()
            if feeds:
                feed_connected = bool(feeds[0].get("connected", False))
        except Exception:  # noqa: BLE001
            feed_connected = False

        # Scheduler + data quality
        scheduler_running = False
        try:
            sched = getattr(request.app.state, "scheduler", None)
            if sched is not None:
                scheduler_running = sched.is_running
        except Exception:  # noqa: BLE001
            scheduler_running = False
        try:
            dq = await live_service.data_quality()
            data_status = ("HEALTHY" if (dq.connected and not dq.degraded)
                           else "HISTORICAL" if dq.connected else "HISTORICAL_CACHE" if dq.historical_available else "NO_DATA")
        except Exception:  # noqa: BLE001
            data_status = "NO_DATA"

        healthy = db_ok and feed_connected
        services = [
            {"name": "DATABASE", "status": "HEALTHY" if db_ok else "OFFLINE",
             "detail": "SQLite reachable" if db_ok else "unreachable", "healthy": db_ok},
            {"name": "BINANCE", "status": "HEALTHY" if feed_connected else "OFFLINE",
             "detail": "connected" if feed_connected else "disconnected", "healthy": feed_connected},
            {"name": "SCHEDULER", "status": "RUNNING" if scheduler_running else "STOPPED",
             "detail": "running" if scheduler_running else "stopped", "healthy": scheduler_running},
            {"name": "DATA_PIPELINE", "status": "DEGRADED" if data_status != "HEALTHY" else "HEALTHY",
             "detail": f"data_status={data_status}", "healthy": data_status == "HEALTHY"},
        ]
        return {
            "status": "healthy" if db_ok else "degraded",
            "symbol": settings.DEFAULT_SYMBOL,
            "environment": settings.APP_ENV,
            "version": "1.0.0",
            "db_ok": db_ok,
            "feed_connected": feed_connected,
            "scheduler_running": scheduler_running,
            "data_status": data_status,
            "services": services,
            "all_healthy": all(s["healthy"] for s in services),
            "ai_provider": _ai_provider_summary(),
        }

    # Register Routers
    app.include_router(dashboard.router)
    app.include_router(market.router)
    app.include_router(analysis.router)
    app.include_router(ai.router)
    app.include_router(signals.router)
    app.include_router(backtest.router)
    app.include_router(paper_trades.router)
    app.include_router(research.router)
    app.include_router(retracement.router)
    app.include_router(webhook_router)
    app.include_router(overview.router)
    app.include_router(terminal.router)

    return app


def _ai_provider_summary() -> dict:
    """Build a read-only AI provider status summary (no secrets)."""
    try:
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
    except Exception:  # noqa: BLE001
        return {"status": "UNAVAILABLE", "reason": "UNAVAILABLE"}


app = create_app()
