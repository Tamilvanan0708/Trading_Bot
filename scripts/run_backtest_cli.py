"""
CLI Runner for Historical Backtesting.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import asyncio

from app.backtesting.engine import BacktestEngine
from app.core.constants import TimeFrame
from app.data.csv_provider import CsvMarketDataProvider


async def main():
    print("=" * 60)
    print("XAU/USD Quantitative Multi-Timeframe Strategy Backtest")
    print("=" * 60)

    provider = CsvMarketDataProvider("data/raw/xauusd_15m_sample.csv")
    candles = await provider.get_ohlcv("XAUUSD", TimeFrame.M15, limit=1200)
    print(f"Loaded {len(candles)} historical 15M candles.")

    engine = BacktestEngine()
    result = engine.run(candles, initial_balance=10000.0, risk_percent=1.0)
    summary = result.summary

    print("-" * 60)
    print(f"Initial Balance:     ${summary.initial_balance:,.2f}")
    print(f"Final Balance:       ${summary.final_balance:,.2f}")
    print(f"Net Profit:          ${summary.net_profit_usd:,.2f} ({summary.net_return_pct:+.2f}%)")
    print(f"Total Trades:        {summary.total_trades}")
    print(f"Winning Trades:      {summary.winning_trades} ({summary.win_rate_pct:.1f}%)")
    print(f"Losing Trades:       {summary.losing_trades}")
    print(f"Profit Factor:       {summary.profit_factor:.2f}")
    print(f"Max Drawdown:        ${summary.max_drawdown_usd:,.2f} ({summary.max_drawdown_pct:.2f}%)")
    print(f"Expectancy:          {summary.expectancy_r:+.2f}R")
    print(f"Long Win Rate:       {summary.long_win_rate_pct:.1f}% ({summary.long_trades_count} trades)")
    print(f"Short Win Rate:      {summary.short_win_rate_pct:.1f}% ({summary.short_trades_count} trades)")
    print("-" * 60)
    print("Strategy Breakdown:")
    for strat, data in summary.strategy_breakdown.items():
        print(f"  * {strat}: {data['total_trades']} trades | Win Rate: {data['win_rate_pct']}% | Net: ${data['net_pnl_usd']:,.2f}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
