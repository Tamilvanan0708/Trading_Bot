"""
Backtesting Models and Performance Metrics.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.constants import SignalDirection, StrategyType, TradeState


class SimulatedTrade(BaseModel):
    """Simulated execution of a signal during backtest."""
    trade_id: str
    symbol: str = "XAUUSD"
    direction: SignalDirection
    strategy: StrategyType
    entry_time: datetime
    entry_price: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    lot_size: float
    risk_usd: float
    pnl_usd: float = 0.0
    pnl_r: float = 0.0
    exit_reason: str = ""
    state: TradeState = TradeState.CLOSED
    confidence_score: float | None = None
    signal_quality: str | None = None
    ai_status: str | None = None  # APPROVE | CAUTION | REJECT | None


class PerformanceSummary(BaseModel):
    """Statistical summary of backtest performance."""
    initial_balance: float
    final_balance: float
    net_profit_usd: float
    net_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    expectancy_r: float
    avg_win_usd: float
    avg_loss_usd: float
    long_trades_count: int
    long_win_rate_pct: float
    short_trades_count: int
    short_win_rate_pct: float
    strategy_breakdown: dict[str, dict[str, float]] = Field(default_factory=dict)


class BacktestResult(BaseModel):
    """Complete Backtest Output payload."""
    symbol: str = "XAUUSD"
    start_time: datetime
    end_time: datetime
    summary: PerformanceSummary
    trades: list[SimulatedTrade]
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
