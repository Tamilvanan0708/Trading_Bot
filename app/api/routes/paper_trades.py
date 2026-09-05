"""
Paper Trades & Performance API Routes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.data.live.service import get_live_service
from app.database.connection import get_db_session
from app.database.repository import Repository
from app.paper_trading.sync import sync_strategy_paper_trades

router = APIRouter(tags=["Paper Trading & Performance"])


def _serialize_trade(t, live_price: float | None = None) -> dict:
    entry = float(t.actual_entry or t.target_entry or 0.0)
    cur_price = float(live_price) if live_price is not None else entry
    running_pts = 0.0
    unrealized_pnl = 0.0
    lots = float(t.lot_size or 0.01)

    if t.state == "OPEN" and entry > 0:
        if t.direction == "LONG":
            running_pts = round(cur_price - entry, 2)
        else:
            running_pts = round(entry - cur_price, 2)
        # Gold: 1 lot = 100 oz. 0.01 lot = 1 oz. 1 point = $1.00 per 0.01 lot
        unrealized_pnl = round(running_pts * lots * 100.0, 2)
    elif t.state == "CLOSED":
        unrealized_pnl = float(t.realized_pnl or 0.0)
        if t.exit_price and entry > 0:
            running_pts = round((float(t.exit_price) - entry) if t.direction == "LONG" else (entry - float(t.exit_price)), 2)

    sig = (t.signal_id or "").upper()
    logs_str = str(t.state_logs or "").upper()
    if "FIB_TREND" in sig or "TREND" in sig or "TREND" in logs_str:
        strat_name = "FIB GO WITH TREND"
        layer_name = "Breakout (0.618)"
    elif "SMC_FIB" in sig or "SMC" in sig or "SMC" in logs_str:
        strat_name = "SMC WITH FIB"
        layer_name = "Single (0.68)"
    elif "FIB_RETR" in sig or "RETR" in sig:
        strat_name = "FIB RETRACEMENT"
        layer_name = "L1 (0.618)" if "L1" in sig else ("L2 (0.500)" if "L2" in sig else ("L3 (0.382)" if "L3" in sig else "L1 (0.618)"))
    else:
        strat_name = "FIB RETRACEMENT"
        layer_name = "L1 (0.618)"

    target_tp = float(t.take_profit_2 or t.take_profit_1 or 0.0) if "TREND" in strat_name else float(t.take_profit_1 or 0.0)
    risk_pts = (float(t.risk_amount or 0.0) / max(0.01, lots * 100.0)) if (t.risk_amount and t.risk_amount > 1.0) else abs(entry - float(t.stop_loss or 0.0))
    reward_pts = abs(target_tp - entry)
    if risk_pts < 1.5:
        risk_pts = max(1.5, reward_pts / 1.8)
    risk_reward = round(reward_pts / risk_pts, 1) if (risk_pts > 0 and reward_pts > 0) else 1.8

    return {
        "id": t.id,
        "signal_id": t.signal_id,
        "symbol": t.symbol,
        "strategy": strat_name,
        "layer": layer_name,
        "direction": t.direction,
        "state": t.state,
        "status": t.state,
        "lot_size": lots,
        "risk_amount": t.risk_amount,
        "target_entry": t.target_entry,
        "actual_entry": t.actual_entry,
        "entry_price": entry,
        "current_price": float(t.exit_price) if (t.state == "CLOSED" and t.exit_price) else cur_price,
        "running_pts": running_pts,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": t.realized_pnl,
        "pnl_usd": t.realized_pnl if t.state == "CLOSED" else unrealized_pnl,
        "pnl_r": t.realized_r if t.state == "CLOSED" else (round(running_pts / max(0.1, abs(entry - float(t.stop_loss or 0.0))), 2) if t.stop_loss else 0.0),
        "stop_loss": t.stop_loss,
        "take_profit_1": t.take_profit_1,
        "take_profit_2": t.take_profit_2,
        "take_profit_3": t.take_profit_3,
        "risk_reward": risk_reward,
        "opened_at": t.opened_at or t.created_at,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "closed_at": t.closed_at,
        "created_at": t.created_at,
    }


@router.get("/paper-trades")
async def list_paper_trades(limit: int = 50, db: AsyncSession = Depends(get_db_session)):
    """Lists simulated paper trading positions from the database with live running points and PnL."""
    import asyncio
    try:
        await asyncio.wait_for(sync_strategy_paper_trades(db), timeout=1.5)
    except Exception:  # noqa: BLE001
        pass

    ls = get_live_service()
    try:
        live_price = await asyncio.wait_for(ls.get_latest_price("XAUUSD"), timeout=1.0)
    except Exception:  # noqa: BLE001
        live_price = None

    repo = Repository(db)
    db_trades = await repo.list_paper_trades(limit=limit)
    return {
        "database_trades": [_serialize_trade(t, live_price) for t in db_trades],
    }


@router.get("/paper-trades/{trade_id}")
async def get_paper_trade(trade_id: str, db: AsyncSession = Depends(get_db_session)):
    """Gets a single paper trade by id."""
    repo = Repository(db)
    trade = await repo.get_paper_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Paper trade not found.")
    return _serialize_trade(trade)


@router.get("/performance")
async def get_performance(db: AsyncSession = Depends(get_db_session)):
    """Returns aggregated performance statistics across paper trades and backtests."""
    repo = Repository(db)
    runs = await repo.list_backtest_runs(limit=5)
    active = await repo.list_active_paper_trades()
    closed = await repo.list_closed_paper_trades(limit=500)

    total_trades = len(closed)
    wins = [t for t in closed if (t.realized_pnl or 0.0) > 0]
    losses = [t for t in closed if (t.realized_pnl or 0.0) <= 0]
    gross_win = sum(t.realized_pnl or 0.0 for t in wins)
    gross_loss = abs(sum(t.realized_pnl or 0.0 for t in losses))

    win_rate = round((len(wins) / total_trades) * 100.0, 2) if total_trades else 0.0
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (999.99 if gross_win > 0 else 0.0)
    avg_win = round(gross_win / len(wins), 2) if wins else 0.0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0.0
    realized_pnl = round(sum(t.realized_pnl or 0.0 for t in closed), 2)

    initial_balance = get_settings().ACCOUNT_BALANCE

    return {
        "paper_account_balance": round(initial_balance + realized_pnl, 2),
        "active_open_trades_count": len(active),
        "closed_trades_count": total_trades,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "realized_pnl_usd": realized_pnl,
        "recent_backtest_runs_count": len(runs),
        "latest_backtest": {
            "id": runs[0].id,
            "win_rate": runs[0].win_rate,
            "profit_factor": runs[0].profit_factor,
            "net_profit": runs[0].net_profit,
            "total_trades": runs[0].total_trades,
        } if runs else None,
    }


@router.get("/performance/account")
async def get_account_statement(db: AsyncSession = Depends(get_db_session)):
    """Detailed account statement with balance, equity, drawdown, and trade statistics."""
    repo = Repository(db)
    active = await repo.list_active_paper_trades()
    closed = await repo.list_closed_paper_trades(limit=500)
    total_pnl = sum(t.realized_pnl or 0.0 for t in closed)
    initial_balance = get_settings().ACCOUNT_BALANCE
    balance = round(initial_balance + total_pnl, 2)
    net_profit = round(total_pnl, 2)
    total_trades = len(closed)
    wins = [t for t in closed if (t.realized_pnl or 0.0) > 0]
    losses = [t for t in closed if (t.realized_pnl or 0.0) <= 0]
    win_rate = round((len(wins) / total_trades) * 100.0, 2) if total_trades else 0.0

    gross_win = sum(t.realized_pnl or 0.0 for t in wins)
    gross_loss = abs(sum(t.realized_pnl or 0.0 for t in losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (999.99 if gross_win > 0 else 0.0)

    # Compute unrealized P&L from active positions using live price
    unrealized_pnl = 0.0
    from app.data.live.service import get_live_service
    live_service = get_live_service()
    try:
        current_price = await live_service.get_latest_price("XAUUSD")
    except Exception:
        current_price = None
    if current_price is not None:
        for t in active:
            entry = t.actual_entry or t.target_entry or 0.0
            if t.direction == "LONG":
                unrealized_pnl += (current_price - entry) * t.lot_size * 100.0
            elif t.direction == "SHORT":
                unrealized_pnl += (entry - current_price) * t.lot_size * 100.0
    avg_win = round(gross_win / len(wins), 2) if wins else 0.0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0.0
    expectancy = round((win_rate / 100.0 * avg_win) - ((1 - win_rate / 100.0) * avg_loss), 2) if total_trades else 0.0

    return {
        "initial_balance": initial_balance,
        "current_balance": balance,
        "equity": round(balance + unrealized_pnl, 2),
        "net_profit_usd": net_profit,
        "net_return_pct": round((net_profit / initial_balance) * 100.0, 2) if initial_balance > 0 else 0.0,
        "total_trades": total_trades,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "expectancy_usd": expectancy,
        "realized_pnl_usd": total_pnl,
        "unrealized_pnl_usd": unrealized_pnl,
        "active_positions": len(active),
    }


@router.post("/paper-trades/repair")
@router.get("/paper-trades/repair")
async def repair_paper_trades(db: AsyncSession = Depends(get_db_session)):
    """Repairs false stop loss executions on Fib Retracement L1 trades.
    
    If an L1 layer was stopped out at 0.236 while L2 in the same setup reached TP,
    L1's stop loss should have been trailed to 0.500 (canceling out L2 profit for breakeven),
    rather than taking a full 11+ pt loss.
    """
    from sqlalchemy import select
    from app.database.models import PaperTradeModel, SignalModel

    trades = (await db.execute(select(PaperTradeModel))).scalars().all()
    repaired_count = 0

    # Group by base setup (e.g. FIB_RETR_5M_L1_4420 -> base: 4420)
    for t in trades:
        sig = t.signal_id or ""
        if "FIB_RETR" in sig and "_L1_" in sig and t.state == "CLOSED" and t.exit_reason == "SL_HIT":
            base_anchor = sig.split("_L1_")[-1]
            l2_sig = f"FIB_RETR_5M_L2_{base_anchor}"
            l2_trade = next((other for other in trades if other.signal_id == l2_sig), None)

            if l2_trade and l2_trade.exit_reason == "TP_HIT":
                # L2 reached TP! L1's SL should have been trailed to L2's entry price (0.500 level)
                l2_entry = float(l2_trade.actual_entry or l2_trade.target_entry or 0.0)
                l1_entry = float(t.actual_entry or t.target_entry or 0.0)
                if l2_entry > 0 and l1_entry > 0:
                    # L1 SL trailed to L2 entry
                    t.stop_loss = l2_entry
                    t.exit_price = l2_entry
                    pts = (l2_entry - l1_entry) if t.direction == "LONG" else (l1_entry - l2_entry)
                    t.realized_pnl = round(pts * (t.lot_size or 0.01) * 100.0, 2)
                    t.realized_r = -1.0
                    repaired_count += 1

    if repaired_count > 0:
        await db.commit()

    return {
        "status": "SUCCESS",
        "repaired_trades": repaired_count,
        "message": f"Successfully repaired {repaired_count} trades with proper 0.500 Smart Shield trailing.",
    }