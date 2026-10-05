import asyncio
from datetime import datetime, timezone
from app.backtesting.strategy_simulator import StrategyBacktester

async def main():
    bt = StrategyBacktester(
        symbol="XAUUSD",
        lot_size=0.01,
        initial_capital=10000.0,
        sizing_mode="broker_risk",
        target_risk_usd=100.0,
        account_currency="cent",
        risk_mode="fixed_amount",
        engine_mode="classic",
    )
    # Last week: 2026-09-18 to 2026-09-25
    start = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 23, 59, tzinfo=timezone.utc)
    res = await bt.run(
        strategy_name="FIB_WITH_RETRACEMENT",
        start_date=start,
        end_date=end,
        timeframe="ALL",
    )
    trades = res.get("trades", [])
    summary = res.get("summary", {})
    print(f"Total trades: {len(trades)}")
    print(f"Summary: Win Rate={summary.get('win_rate')}%, Net PnL=${summary.get('net_pnl_usd')}, Profit Factor={summary.get('profit_factor')}")
    for t in trades:
        print(f"{t['entry_time']} | {t['timeframe']} {t['direction']} | Entry: {t['entry_price']:.2f} | SL: {t['sl_price']:.2f} | TP: {t['tp_price']:.2f} | Exit: {t['exit_price']:.2f} ({t['exit_reason']}) | PnL: ${t['pnl_usd']:.2f} ({t['pnl_pts']:.2f} pts)")

if __name__ == "__main__":
    asyncio.run(main())
