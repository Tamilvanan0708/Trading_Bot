"""
Live vs Backtest Execution Parity Comparator.

Compares backtest simulation results against actual Live/Paper trades
executed in the same date range to verify execution parity, slippage,
and signal integrity.
"""

from datetime import datetime, timezone
import math
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PaperTradeModel


def _parse_ts(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _extract_tf(t: PaperTradeModel) -> str:
    sig = (t.signal_id or "").upper()
    if t.state_logs and isinstance(t.state_logs, list):
        for log in t.state_logs:
            if isinstance(log, dict) and log.get("timeframe"):
                return str(log["timeframe"]).upper()
    for candidate in ["15M", "30M", "1H", "2H", "4H", "5M", "1D"]:
        if f"_{candidate}_" in sig or f"_{candidate}" in sig or sig.startswith(f"{candidate}_"):
            return candidate
    return "5M"


def _extract_strategy(t: PaperTradeModel) -> str:
    sig = (t.signal_id or "").upper()
    logs_str = str(t.state_logs or "").upper()
    if "FIB_TREND" in sig or "TREND" in sig or "TREND" in logs_str:
        return "FIB_GO_WITH_TREND"
    if "SMC_FIB" in sig or "SMC" in sig or "SMC" in logs_str:
        return "SMC_WITH_FIB"
    return "FIB_WITH_RETRACEMENT"


async def compare_backtest_with_live_trades(
    backtest_trades: list[dict],
    start_dt: datetime,
    end_dt: datetime,
    db: AsyncSession,
    selected_strategy: str = "ALL",
    selected_timeframe: str = "ALL",
) -> dict:
    """
    Performs one-to-one or closest-neighbor alignment between backtest trades
    and actual live/paper trades in the requested date interval.
    """
    s_utc = start_dt if start_dt.tzinfo else start_dt.replace(tzinfo=timezone.utc)
    e_utc = end_dt if end_dt.tzinfo else end_dt.replace(tzinfo=timezone.utc)

    # Fetch paper trades overlapping with this window
    stmt = select(PaperTradeModel).where(
        PaperTradeModel.opened_at >= s_utc,
        PaperTradeModel.opened_at <= e_utc,
    ).order_by(PaperTradeModel.opened_at.asc())

    res = await db.execute(stmt)
    live_records = res.scalars().all()

    # Filter live records by strategy / timeframe if specified
    filtered_live: list[dict] = []
    for lt in live_records:
        lt_strat = _extract_strategy(lt)
        lt_tf = _extract_tf(lt)
        if selected_strategy != "ALL" and lt_strat != selected_strategy:
            continue
        if selected_timeframe.upper() != "ALL" and lt_tf != selected_timeframe.upper():
            continue

        entry_px = float(lt.actual_entry or lt.target_entry or 0.0)
        exit_px = float(lt.exit_price or entry_px)
        pnl = float(lt.realized_pnl if lt.realized_pnl is not None else 0.0)
        pts = 0.0
        if entry_px > 0 and exit_px > 0:
            pts = round((exit_px - entry_px) if lt.direction == "LONG" else (entry_px - exit_px), 2)

        filtered_live.append({
            "id": lt.id,
            "signal_id": lt.signal_id,
            "strategy": lt_strat,
            "timeframe": lt_tf,
            "direction": lt.direction,
            "entry_time": lt.opened_at.isoformat() if lt.opened_at else None,
            "entry_dt": _parse_ts(lt.opened_at),
            "entry_price": entry_px,
            "stop_loss": float(lt.stop_loss or 0.0),
            "take_profit": float(lt.take_profit_1 or 0.0),
            "exit_time": lt.closed_at.isoformat() if lt.closed_at else None,
            "exit_price": exit_px,
            "exit_reason": lt.exit_reason or "OPEN",
            "pnl": pnl,
            "pts": pts,
            "status": lt.state,
            "matched": False,
        })

    # Prepare backtest trades list
    bt_list: list[dict] = []
    for b in backtest_trades:
        bt_strat = str(b.get("strategy") or "").upper()
        bt_tf = str(b.get("timeframe") or "").upper()
        if selected_strategy != "ALL" and bt_strat != selected_strategy:
            continue
        if selected_timeframe.upper() != "ALL" and bt_tf != selected_timeframe.upper():
            continue

        bt_list.append({
            **b,
            "entry_dt": _parse_ts(b.get("entry_time")),
            "matched": False,
        })

    comparisons: list[dict] = []
    total_slippage = 0.0
    matched_count = 0
    outcome_agreement_count = 0

    # Match each live trade with the closest backtest trade
    for lt in filtered_live:
        best_bt = None
        best_diff_sec = float("inf")

        for bt in bt_list:
            if bt["matched"]:
                continue
            # Direction and timeframe must match
            if bt.get("direction") != lt["direction"]:
                continue
            if str(bt.get("timeframe", "")).upper() != lt["timeframe"].upper():
                continue

            # Compare entry timestamps (within 4 hours / 14400s)
            if lt["entry_dt"] and bt.get("entry_dt"):
                diff_sec = abs((lt["entry_dt"] - bt["entry_dt"]).total_seconds())
                if diff_sec < 14400 and diff_sec < best_diff_sec:
                    # Also check price neighborhood within 8 points
                    if abs(float(bt.get("entry_price", 0)) - lt["entry_price"]) <= 8.0:
                        best_diff_sec = diff_sec
                        best_bt = bt

        if best_bt is not None:
            best_bt["matched"] = True
            lt["matched"] = True
            matched_count += 1

            bt_entry = float(best_bt.get("entry_price") or 0.0)
            slippage = round(abs(lt["entry_price"] - bt_entry), 2)
            total_slippage += slippage

            bt_exit_reason = str(best_bt.get("exit_reason") or "").upper()
            lt_exit_reason = str(lt.get("exit_reason") or "").upper()

            outcome_match = (
                (bt_exit_reason == lt_exit_reason)
                or ("TP" in bt_exit_reason and "TP" in lt_exit_reason)
                or ("SL" in bt_exit_reason and "SL" in lt_exit_reason)
            )
            if outcome_match:
                outcome_agreement_count += 1

            status = "PARITY_100" if (slippage <= 0.8 and outcome_match) else ("SLIPPAGE_VARIANCE" if outcome_match else "DIVERGED")

            comparisons.append({
                "type": "MATCHED",
                "timeframe": lt["timeframe"],
                "strategy": lt["strategy"],
                "direction": lt["direction"],
                "live_time": lt["entry_time"],
                "backtest_time": best_bt.get("entry_time"),
                "live_entry": lt["entry_price"],
                "backtest_entry": bt_entry,
                "slippage_pts": slippage,
                "live_exit": lt["exit_price"],
                "backtest_exit": float(best_bt.get("exit_price") or 0.0),
                "live_outcome": lt_exit_reason,
                "backtest_outcome": bt_exit_reason,
                "live_pnl": lt["pnl"],
                "backtest_pnl": float(best_bt.get("pnl_usd") or 0.0),
                "status": status,
            })
        else:
            comparisons.append({
                "type": "LIVE_ONLY",
                "timeframe": lt["timeframe"],
                "strategy": lt["strategy"],
                "direction": lt["direction"],
                "live_time": lt["entry_time"],
                "backtest_time": None,
                "live_entry": lt["entry_price"],
                "backtest_entry": None,
                "slippage_pts": None,
                "live_exit": lt["exit_price"],
                "backtest_exit": None,
                "live_outcome": lt.get("exit_reason"),
                "backtest_outcome": None,
                "live_pnl": lt["pnl"],
                "backtest_pnl": None,
                "status": "LIVE_ONLY",
            })

    # Include backtest trades that were not executed in live
    for bt in bt_list:
        if not bt["matched"]:
            comparisons.append({
                "type": "BACKTEST_ONLY",
                "timeframe": str(bt.get("timeframe") or "").upper(),
                "strategy": bt.get("strategy"),
                "direction": bt.get("direction"),
                "live_time": None,
                "backtest_time": bt.get("entry_time"),
                "live_entry": None,
                "backtest_entry": float(bt.get("entry_price") or 0.0),
                "slippage_pts": None,
                "live_exit": None,
                "backtest_exit": float(bt.get("exit_price") or 0.0),
                "live_outcome": None,
                "backtest_outcome": bt.get("exit_reason"),
                "live_pnl": None,
                "backtest_pnl": float(bt.get("pnl_usd") or 0.0),
                "status": "BACKTEST_ONLY",
            })

    # Sort comparisons by time
    def _sort_key(c: dict) -> str:
        return str(c.get("live_time") or c.get("backtest_time") or "")

    comparisons.sort(key=_sort_key, reverse=True)

    total_live = len(filtered_live)
    total_bt = len(bt_list)
    parity_rate = round((matched_count / max(1, total_live)) * 100.0, 1) if total_live > 0 else 0.0
    agreement_rate = round((outcome_agreement_count / max(1, matched_count)) * 100.0, 1) if matched_count > 0 else 0.0
    avg_slippage = round(total_slippage / max(1, matched_count), 2) if matched_count > 0 else 0.0

    return {
        "enabled": True,
        "total_live_trades": total_live,
        "total_backtest_trades": total_bt,
        "matched_count": matched_count,
        "parity_rate_pct": parity_rate,
        "outcome_agreement_pct": agreement_rate,
        "avg_entry_slippage_pts": avg_slippage,
        "total_live_pnl": round(sum(lt["pnl"] for lt in filtered_live), 2),
        "total_backtest_pnl": round(sum(float(b.get("pnl_usd") or 0.0) for b in bt_list), 2),
        "comparisons": comparisons,
    }
