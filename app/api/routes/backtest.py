"""
Backtesting API Routes.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting.engine import BacktestEngine
from app.core.constants import TimeFrame
from app.data.csv_provider import CsvMarketDataProvider
from app.database.connection import get_db_session
from app.database.repository import Repository

router = APIRouter(prefix="/backtest", tags=["Backtesting"])
_provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")


class BacktestRequest(BaseModel):
    symbol: str = "XAUUSD"
    initial_balance: float = Field(default=10000.0, ge=500.0)
    risk_percent: float = Field(default=1.0, ge=0.1, le=5.0)
    limit_bars: int = Field(default=1000, ge=200, le=5000)


@router.post("")
async def run_backtest(req: BacktestRequest, db: AsyncSession = Depends(get_db_session)):
    """Executes a full event-driven backtest over historical dataset and stores result."""
    candles = await _provider.get_ohlcv(req.symbol, TimeFrame.M15, limit=req.limit_bars)
    engine = BacktestEngine()
    result = await asyncio.to_thread(engine.run, candles, initial_balance=req.initial_balance, risk_percent=req.risk_percent)

    repo = Repository(db)
    run_dict = {
        "symbol": result.symbol,
        "start_date": result.start_time,
        "end_date": result.end_time,
        "initial_balance": result.summary.initial_balance,
        "final_balance": result.summary.final_balance,
        "total_trades": result.summary.total_trades,
        "winning_trades": result.summary.winning_trades,
        "losing_trades": result.summary.losing_trades,
        "win_rate": result.summary.win_rate_pct,
        "profit_factor": result.summary.profit_factor,
        "max_drawdown": result.summary.max_drawdown_usd,
        "net_profit": result.summary.net_profit_usd,
        "expectancy": result.summary.expectancy_r,
        "strategy_metrics": result.summary.strategy_breakdown,
        "timeframe_metrics": {},
        "trades_log": [t.model_dump(mode="json") for t in result.trades],
    }
    saved_run = await repo.save_backtest_run(run_dict)

    return {
        "backtest_id": saved_run.id,
        "summary": result.summary.model_dump(),
        "total_trades_count": len(result.trades),
        "recent_trades": [t.model_dump(mode="json") for t in result.trades[-10:]],
    }


@router.get("/data-range")
async def get_data_range():
    """Returns available historical Forex dataset dates and metadata."""
    from app.backtesting.data_loader import get_available_forex_data_range
    return get_available_forex_data_range()


@router.get("/{id}")
async def get_backtest(id: str, db: AsyncSession = Depends(get_db_session)):
    """Retrieves detailed backtest report by ID."""
    repo = Repository(db)
    run = await repo.get_backtest_run(id)
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found.")

    return {
        "id": run.id,
        "created_at": run.created_at,
        "symbol": run.symbol,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "initial_balance": run.initial_balance,
        "final_balance": run.final_balance,
        "total_trades": run.total_trades,
        "win_rate": run.win_rate,
        "profit_factor": run.profit_factor,
        "max_drawdown": run.max_drawdown,
        "net_profit": run.net_profit,
        "expectancy": run.expectancy,
        "strategy_metrics": run.strategy_metrics,
        "trades_log": run.trades_log,
    }


class StrategyBacktestRequest(BaseModel):
    strategy: str = Field(default="FIB_GO_WITH_TREND", description="FIB_GO_WITH_TREND | SMC_WITH_FIB | FIB_WITH_RETRACEMENT | ALL")
    timeframe: str = Field(default="ALL", description="ALL | 5m | 15m | 30m | 1h | 2h | 4h")
    start_date: str = Field(default="2026-07-01", description="YYYY-MM-DD")
    end_date: str = Field(default="2026-07-31", description="YYYY-MM-DD")
    symbol: str = Field(default="XAUUSD")
    lot_size: float = Field(default=0.01, ge=0.01, le=10.0)
    initial_capital: float = Field(default=10000.0, ge=100.0)
    sizing_mode: str = Field(default="broker_risk", description="fixed | broker_risk")
    target_risk_usd: float = Field(default=100.0, ge=1.0, le=5000.0)
    account_currency: str = Field(default="cent", description="cent | usd")
    risk_mode: str = Field(default="percent", description="percent | fixed_amount")
    risk_percent: float = Field(default=1.0, ge=0.1, le=10.0)
    engine_mode: str = Field(default="classic", description="classic | experimental")


@router.post("/run-strategy")
async def run_strategy_backtest(req: StrategyBacktestRequest):
    """Executes multi-timeframe strategy backtest with single active trade lock."""
    from datetime import datetime, timezone
    from app.backtesting.strategy_simulator import StrategyBacktester

    def _parse_dt(d_str: str, is_end: bool = False) -> datetime:
        from datetime import timedelta
        cleaned = d_str.strip().split("T")[0]
        # User inputs dates in Indian Standard Time (IST, UTC+5:30)
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        dt = datetime.strptime(cleaned, "%Y-%m-%d").replace(tzinfo=ist_tz)
        if is_end:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.astimezone(timezone.utc)

    try:
        s_dt = _parse_dt(req.start_date, is_end=False)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid start_date format, expected YYYY-MM-DD or ISO date")

    try:
        e_dt = _parse_dt(req.end_date, is_end=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid end_date format, expected YYYY-MM-DD or ISO date")

    if s_dt >= e_dt:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    backtester = StrategyBacktester(
        symbol=req.symbol,
        lot_size=req.lot_size,
        initial_capital=req.initial_capital,
        sizing_mode=req.sizing_mode,
        target_risk_usd=req.target_risk_usd,
        account_currency=req.account_currency,
        risk_mode=req.risk_mode,
        risk_percent=req.risk_percent,
        engine_mode=req.engine_mode,
    )

    try:
        result = await backtester.run(req.strategy, s_dt, e_dt, timeframe=req.timeframe)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backtest execution failed: {exc}")

