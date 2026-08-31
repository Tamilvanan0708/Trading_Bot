"""
Backtesting API Routes.
"""

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
    result = engine.run(candles, initial_balance=req.initial_balance, risk_percent=req.risk_percent)

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
