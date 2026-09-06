"""
Multi-Timeframe Strategy Simulator & Backtesting Engine.

Runs zero-lookahead chronological replay of historical candles for:
  1. Fib Go with Trend (15m, 30m, 1h, 2h, 4h)
  2. SMC with Fib (5m, 15m, 30m, 1h, 4h)
  3. Fib Retracement (5m, 15m, 30m, 1h, 4h)

Enforces exact Single Active Trade Lock across timeframes:
When a trade is open on one timeframe, other timeframes remain on Standby
until that trade completes (TP or SL hit).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any, Dict, List, Optional

from app.backtesting.data_loader import fetch_historical_candles
from app.core.constants import SignalDirection
from app.core.logging import logger
from app.data.models import Candle
from app.retracement.dual_engine import DualRetracementEngine
from app.retracement.fib_trend_engine import FibTrendEngine, FibTrendState
from app.retracement.models import RetracementState
from app.retracement.smc_fib_engine import SMCFibEngine


@dataclass
class BacktestTradeRecord:
    trade_id: str
    strategy: str
    timeframe: str
    direction: str  # "LONG" or "SHORT"
    zero_level: float  # P0 anchor origin
    entry_time: str
    entry_price: float
    sl_price: float
    tp_price: float
    exit_time: str
    exit_price: float
    exit_reason: str  # "TP_HIT" | "SL_HIT" | "EXPIRED"
    pnl_pts: float
    pnl_usd: float
    r_multiple: float
    status: str  # "WIN" | "LOSS" | "OPEN"


class StrategyBacktester:
    """Multi-timeframe chronological strategy backtester with single-active-trade lock."""

    def __init__(
        self,
        symbol: str = "XAUUSD",
        lot_size: float = 0.01,
        initial_capital: float = 1000.0,
    ) -> None:
        self.symbol = symbol
        self.lot_size = lot_size
        self.initial_capital = initial_capital

    async def run(
        self,
        strategy_name: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = "ALL",
    ) -> dict[str, Any]:
        """Run backtest for requested strategy over [start_date, end_date]."""
        # Ensure UTC timezone
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        # Include warmup period (7 days before start_date) for EMA and swing initialization
        warmup_start = start_date - timedelta(days=7)

        strat_key = strategy_name.upper().strip()
        tf_key = (timeframe or "ALL").strip().lower()
        all_trades: list[BacktestTradeRecord] = []

        if strat_key == "FIB_GO_WITH_TREND":
            all_trades = await self._run_fib_trend(warmup_start, start_date, end_date, selected_tf=tf_key)
        elif strat_key == "SMC_WITH_FIB":
            all_trades = await self._run_smc_fib(warmup_start, start_date, end_date, selected_tf=tf_key)
        elif strat_key == "FIB_WITH_RETRACEMENT":
            all_trades = await self._run_fib_retracement(warmup_start, start_date, end_date, selected_tf=tf_key)
        elif strat_key in ("ALL", "ALL_COMBINED"):
            t_trend = await self._run_fib_trend(warmup_start, start_date, end_date, selected_tf=tf_key)
            t_smc = await self._run_smc_fib(warmup_start, start_date, end_date, selected_tf=tf_key)
            t_retr = await self._run_fib_retracement(warmup_start, start_date, end_date, selected_tf=tf_key)
            all_trades = t_trend + t_smc + t_retr
            all_trades.sort(key=lambda t: t.entry_time)
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        summary = self._compute_summary(all_trades, start_date, end_date, selected_tf=timeframe)
        return {
            "summary": summary,
            "trades": [t.__dict__ for t in all_trades],
        }

    # -------------------------------------------------------------------------
    # Strategy 1: Fib Go with Trend (15m, 30m, 1h, 2h, 4h)
    # -------------------------------------------------------------------------
    async def _run_fib_trend(
        self,
        warmup_start: datetime,
        start_date: datetime,
        end_date: datetime,
        selected_tf: str = "all",
    ) -> list[BacktestTradeRecord]:
        allowed = ["15m", "30m", "1h", "2h", "4h"]
        if selected_tf != "all":
            if selected_tf not in allowed:
                return []
            timeframes = [selected_tf]
        else:
            timeframes = allowed

        engines = {tf: FibTrendEngine(symbol=self.symbol, timeframe=tf) for tf in timeframes}

        # Fetch candles for each timeframe
        tf_candles: dict[str, list[Candle]] = {}
        for tf in timeframes:
            candles = await fetch_historical_candles(self.symbol, tf, warmup_start, end_date)
            tf_candles[tf] = candles

        # Interleave all candles chronologically: (timestamp, tf, candle)
        timeline: list[tuple[datetime, str, Candle]] = []
        for tf, clist in tf_candles.items():
            for c in clist:
                timeline.append((c.timestamp, tf, c))
        timeline.sort(key=lambda x: x[0])

        trades: list[BacktestTradeRecord] = []
        active_trade: dict[str, Any] | None = None

        for ts, tf, candle in timeline:
            eng = engines[tf]

            # 1. If an active trade is currently open, check if this candle resolves it (TP / SL)
            if active_trade is not None:
                is_long = active_trade["direction"] == "LONG"
                sl = active_trade["sl_price"]
                tp = active_trade["tp_price"]

                closed = False
                exit_reason = ""
                exit_price = 0.0

                if is_long:
                    # SL checked first (conservative)
                    if candle.low <= sl:
                        closed = True
                        exit_reason = "SL_HIT"
                        exit_price = sl
                    elif candle.high >= tp:
                        closed = True
                        exit_reason = "TP_HIT"
                        exit_price = tp
                else:
                    # SHORT
                    if candle.high >= sl:
                        closed = True
                        exit_reason = "SL_HIT"
                        exit_price = sl
                    elif candle.low <= tp:
                        closed = True
                        exit_reason = "TP_HIT"
                        exit_price = tp

                if closed:
                    pts = round((exit_price - active_trade["entry_price"]) if is_long else (active_trade["entry_price"] - exit_price), 2)
                    pnl_usd = round(pts * self.lot_size * 100.0, 2)
                    risk_pts = max(0.1, abs(active_trade["entry_price"] - sl))
                    r_mult = round(pts / risk_pts, 2)

                    record = BacktestTradeRecord(
                        trade_id=active_trade["id"],
                        strategy="Fib Go with Trend",
                        timeframe=active_trade["timeframe"].upper(),
                        direction=active_trade["direction"],
                        zero_level=active_trade["zero_level"],
                        entry_time=active_trade["entry_time"],
                        entry_price=active_trade["entry_price"],
                        sl_price=sl,
                        tp_price=tp,
                        exit_time=candle.timestamp.isoformat(),
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        pnl_pts=pts,
                        pnl_usd=pnl_usd,
                        r_multiple=r_mult,
                        status="WIN" if pnl_usd > 0 else "LOSS",
                    )
                    trades.append(record)

                    # Reset active slot engine to COMPLETED and clear active lock
                    engines[active_trade["timeframe"]].state = FibTrendState.COMPLETED
                    active_trade = None

            # 2. Advance the slot engine with this candle
            prev_state = eng.state
            eng.process_candle(candle)

            # 3. Check for new trade trigger (ONLY if no active trade is open and candle >= start_date)
            if active_trade is None and candle.timestamp >= start_date:
                if eng.state == FibTrendState.TRADE_ACTIVE and prev_state != FibTrendState.TRADE_ACTIVE:
                    dir_str = "LONG" if eng.direction == SignalDirection.LONG else "SHORT"
                    entry_px = float(eng.entry_price or eng.trigger_breakout_price or candle.close)
                    sl_px = float(eng.sl_price or eng.fib_0_236 or (entry_px - 8.0 if dir_str == "LONG" else entry_px + 8.0))
                    tp_px = float(eng.tp_price or eng.fib_1_618 or (entry_px + 16.0 if dir_str == "LONG" else entry_px - 16.0))
                    zero_px = float(eng.point_0_price or 0.0)

                    active_trade = {
                        "id": f"FT_{tf.upper()}_{int(candle.timestamp.timestamp())}",
                        "timeframe": tf,
                        "direction": dir_str,
                        "entry_time": candle.timestamp.isoformat(),
                        "entry_price": entry_px,
                        "sl_price": sl_px,
                        "tp_price": tp_px,
                        "zero_level": zero_px,
                    }

        # Close any open trade at the end of the simulation window
        if active_trade is not None:
            last_c = timeline[-1][2] if timeline else None
            exit_px = last_c.close if last_c else active_trade["entry_price"]
            is_long = active_trade["direction"] == "LONG"
            pts = round((exit_px - active_trade["entry_price"]) if is_long else (active_trade["entry_price"] - exit_px), 2)
            pnl_usd = round(pts * self.lot_size * 100.0, 2)
            trades.append(BacktestTradeRecord(
                trade_id=active_trade["id"],
                strategy="Fib Go with Trend",
                timeframe=active_trade["timeframe"].upper(),
                direction=active_trade["direction"],
                zero_level=active_trade["zero_level"],
                entry_time=active_trade["entry_time"],
                entry_price=active_trade["entry_price"],
                sl_price=active_trade["sl_price"],
                tp_price=active_trade["tp_price"],
                exit_time=last_c.timestamp.isoformat() if last_c else active_trade["entry_time"],
                exit_price=exit_px,
                exit_reason="EXPIRED",
                pnl_pts=pts,
                pnl_usd=pnl_usd,
                r_multiple=round(pts / max(0.1, abs(active_trade["entry_price"] - active_trade["sl_price"])), 2),
                status="OPEN",
            ))

        return trades

    # -------------------------------------------------------------------------
    # Strategy 2: SMC with Fib (5m, 15m, 30m, 1h, 4h)
    # -------------------------------------------------------------------------
    async def _run_smc_fib(
        self,
        warmup_start: datetime,
        start_date: datetime,
        end_date: datetime,
        selected_tf: str = "all",
    ) -> list[BacktestTradeRecord]:
        allowed = ["5m", "15m", "30m", "1h", "4h"]
        if selected_tf != "all":
            if selected_tf not in allowed:
                return []
            timeframes = [selected_tf]
        else:
            timeframes = allowed

        engines = {tf: SMCFibEngine(symbol=self.symbol, timeframe=tf) for tf in timeframes}

        tf_candles: dict[str, list[Candle]] = {}
        for tf in timeframes:
            candles = await fetch_historical_candles(self.symbol, tf, warmup_start, end_date)
            tf_candles[tf] = candles

        timeline: list[tuple[datetime, str, Candle]] = []
        for tf, clist in tf_candles.items():
            for c in clist:
                timeline.append((c.timestamp, tf, c))
        timeline.sort(key=lambda x: x[0])

        trades: list[BacktestTradeRecord] = []
        active_trade: dict[str, Any] | None = None

        for ts, tf, candle in timeline:
            eng = engines[tf]

            # 1. Resolve active trade if open
            if active_trade is not None:
                is_long = active_trade["direction"] == "LONG"
                sl = active_trade["sl_price"]
                tp = active_trade["tp_price"]

                closed = False
                exit_reason = ""
                exit_price = 0.0

                if is_long:
                    if candle.low <= sl:
                        closed = True
                        exit_reason = "SL_HIT"
                        exit_price = sl
                    elif candle.high >= tp:
                        closed = True
                        exit_reason = "TP_HIT"
                        exit_price = tp
                else:
                    if candle.high >= sl:
                        closed = True
                        exit_reason = "SL_HIT"
                        exit_price = sl
                    elif candle.low <= tp:
                        closed = True
                        exit_reason = "TP_HIT"
                        exit_price = tp

                if closed:
                    pts = round((exit_price - active_trade["entry_price"]) if is_long else (active_trade["entry_price"] - exit_price), 2)
                    pnl_usd = round(pts * self.lot_size * 100.0, 2)
                    risk_pts = max(0.1, abs(active_trade["entry_price"] - sl))
                    r_mult = round(pts / risk_pts, 2)

                    record = BacktestTradeRecord(
                        trade_id=active_trade["id"],
                        strategy="SMC with Fib",
                        timeframe=active_trade["timeframe"].upper(),
                        direction=active_trade["direction"],
                        zero_level=active_trade["zero_level"],
                        entry_time=active_trade["entry_time"],
                        entry_price=active_trade["entry_price"],
                        sl_price=sl,
                        tp_price=tp,
                        exit_time=candle.timestamp.isoformat(),
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        pnl_pts=pts,
                        pnl_usd=pnl_usd,
                        r_multiple=r_mult,
                        status="WIN" if pnl_usd > 0 else "LOSS",
                    )
                    trades.append(record)
                    engines[active_trade["timeframe"]]._reset_setup()
                    active_trade = None

            # 2. Advance engine
            prev_touched = eng.entry_touched
            eng.process_candle(candle)

            # 3. Check for new 0.680 Golden Pocket entry touch
            if active_trade is None and candle.timestamp >= start_date:
                if eng.entry_touched and not prev_touched and eng.entry_price and eng.sl_price:
                    dir_str = "LONG" if eng.direction == SignalDirection.LONG else "SHORT"
                    entry_px = float(eng.entry_price)
                    sl_px = float(eng.sl_price)
                    tp_px = float(eng.target_tp_price or eng.locked_tp or (entry_px + 15.0 if dir_str == "LONG" else entry_px - 15.0))
                    zero_px = float(eng.point_2_price or 0.0)

                    active_trade = {
                        "id": f"SMC_{tf.upper()}_{int(candle.timestamp.timestamp())}",
                        "timeframe": tf,
                        "direction": dir_str,
                        "entry_time": candle.timestamp.isoformat(),
                        "entry_price": entry_px,
                        "sl_price": sl_px,
                        "tp_price": tp_px,
                        "zero_level": zero_px,
                    }

        if active_trade is not None:
            last_c = timeline[-1][2] if timeline else None
            exit_px = last_c.close if last_c else active_trade["entry_price"]
            is_long = active_trade["direction"] == "LONG"
            pts = round((exit_px - active_trade["entry_price"]) if is_long else (active_trade["entry_price"] - exit_px), 2)
            pnl_usd = round(pts * self.lot_size * 100.0, 2)
            trades.append(BacktestTradeRecord(
                trade_id=active_trade["id"],
                strategy="SMC with Fib",
                timeframe=active_trade["timeframe"].upper(),
                direction=active_trade["direction"],
                zero_level=active_trade["zero_level"],
                entry_time=active_trade["entry_time"],
                entry_price=active_trade["entry_price"],
                sl_price=active_trade["sl_price"],
                tp_price=active_trade["tp_price"],
                exit_time=last_c.timestamp.isoformat() if last_c else active_trade["entry_time"],
                exit_price=exit_px,
                exit_reason="EXPIRED",
                pnl_pts=pts,
                pnl_usd=pnl_usd,
                r_multiple=round(pts / max(0.1, abs(active_trade["entry_price"] - active_trade["sl_price"])), 2),
                status="OPEN",
            ))

        return trades

    # -------------------------------------------------------------------------
    # Strategy 3: Fib Retracement (5m, 15m, 30m, 1h, 4h)
    # -------------------------------------------------------------------------
    async def _run_fib_retracement(
        self,
        warmup_start: datetime,
        start_date: datetime,
        end_date: datetime,
        selected_tf: str = "all",
    ) -> list[BacktestTradeRecord]:
        allowed = ["5m", "15m", "30m", "1h", "4h"]
        if selected_tf != "all":
            if selected_tf not in allowed:
                return []
            timeframes = [selected_tf]
        else:
            timeframes = allowed

        engines = {tf: DualRetracementEngine(symbol=self.symbol, timeframe=tf) for tf in timeframes}

        tf_candles: dict[str, list[Candle]] = {}
        for tf in timeframes:
            candles = await fetch_historical_candles(self.symbol, tf, warmup_start, end_date)
            tf_candles[tf] = candles

        timeline: list[tuple[datetime, str, Candle]] = []
        for tf, clist in tf_candles.items():
            for c in clist:
                timeline.append((c.timestamp, tf, c))
        timeline.sort(key=lambda x: x[0])

        trades: list[BacktestTradeRecord] = []
        active_setup_tf: str | None = None
        resolved_layers: set[tuple[str, str]] = set()

        for ts, tf, candle in timeline:
            eng = engines[tf]

            # 1. Advance engine with this candle
            eng.process_candle(candle)
            setup = eng.setup

            # 2. Track layer fills and resolutions
            if setup is not None and setup.layers:
                # Acquire Single Active Trade Lock if free and inside date range
                if active_setup_tf is None and candle.timestamp >= start_date:
                    active_setup_tf = tf

                # Process layer outcomes if this timeframe holds the lock
                if active_setup_tf == tf:
                    for l_key in ("L1", "L2", "L3"):
                        if l_key not in setup.layers:
                            continue
                        l_data = setup.layers[l_key]
                        lid = (setup.setup_id, l_key)
                        if lid not in resolved_layers and l_data.get("state") in ("TP_HIT", "SL_HIT"):
                            resolved_layers.add(lid)
                            is_long = setup.direction == "LONG"
                            entry_px = float(l_data["entry_price"])
                            sl_px = float(l_data.get("sl") or setup.sl_price or (entry_px - 8.0 if is_long else entry_px + 8.0))
                            tp_px = float(l_data.get("tp") or (entry_px + 16.0 if is_long else entry_px - 16.0))
                            exit_reason = l_data["state"]
                            exit_px = tp_px if exit_reason == "TP_HIT" else float(setup.sl_price or sl_px)

                            pts = round((exit_px - entry_px) if is_long else (entry_px - exit_px), 2)
                            pnl_usd = round(pts * self.lot_size * 100.0, 2)
                            risk_pts = max(0.1, abs(entry_px - sl_px))
                            r_mult = round(pts / risk_pts, 2)

                            trades.append(BacktestTradeRecord(
                                trade_id=f"RETR_{tf.upper()}_{l_key}_{int(ts.timestamp())}",
                                strategy=f"Fib Retracement [{l_key}]",
                                timeframe=tf.upper(),
                                direction=setup.direction,
                                zero_level=float(setup.point_2_price or 0.0),
                                entry_time=l_data.get("filled_at") or candle.timestamp.isoformat(),
                                entry_price=entry_px,
                                sl_price=sl_px,
                                tp_price=tp_px,
                                exit_time=candle.timestamp.isoformat(),
                                exit_price=exit_px,
                                exit_reason=exit_reason,
                                pnl_pts=pts,
                                pnl_usd=pnl_usd,
                                r_multiple=r_mult,
                                status="WIN" if pnl_usd > 0 else "LOSS",
                            ))

            # 3. Setup completion / invalidation lifecycle
            if setup is not None and setup.state in (RetracementState.COMPLETED, RetracementState.INVALIDATED):
                if active_setup_tf == tf:
                    active_setup_tf = None
                eng.archive_completed()

        # Close any open layers at the end of the simulation window
        if active_setup_tf is not None:
            eng = engines[active_setup_tf]
            if eng.setup and eng.setup.layers:
                last_c = timeline[-1][2] if timeline else None
                for l_key in ("L1", "L2", "L3"):
                    if l_key not in eng.setup.layers:
                        continue
                    l_data = eng.setup.layers[l_key]
                    lid = (eng.setup.setup_id, l_key)
                    if lid not in resolved_layers and l_data.get("state") == "FILLED":
                        resolved_layers.add(lid)
                        is_long = eng.setup.direction == "LONG"
                        entry_px = float(l_data["entry_price"])
                        exit_px = float(last_c.close if last_c else entry_px)
                        pts = round((exit_px - entry_px) if is_long else (entry_px - exit_px), 2)
                        pnl_usd = round(pts * self.lot_size * 100.0, 2)
                        sl_px = float(l_data.get("sl") or eng.setup.sl_price or entry_px)
                        trades.append(BacktestTradeRecord(
                            trade_id=f"RETR_{active_setup_tf.upper()}_{l_key}_{int(timeline[-1][0].timestamp())}",
                            strategy=f"Fib Retracement [{l_key}]",
                            timeframe=active_setup_tf.upper(),
                            direction=eng.setup.direction,
                            zero_level=float(eng.setup.point_2_price or 0.0),
                            entry_time=l_data.get("filled_at") or (eng.setup.entry_timestamp.isoformat() if eng.setup.entry_timestamp else ""),
                            entry_price=entry_px,
                            sl_price=sl_px,
                            tp_price=float(l_data.get("tp") or entry_px),
                            exit_time=last_c.timestamp.isoformat() if last_c else "",
                            exit_price=exit_px,
                            exit_reason="EXPIRED",
                            pnl_pts=pts,
                            pnl_usd=pnl_usd,
                            r_multiple=round(pts / max(0.1, abs(entry_px - sl_px)), 2),
                            status="OPEN",
                        ))

        return trades

    # -------------------------------------------------------------------------
    # Metrics Calculator
    # -------------------------------------------------------------------------
    def _compute_summary(
        self,
        trades: list[BacktestTradeRecord],
        start_date: datetime,
        end_date: datetime,
        selected_tf: str = "ALL",
    ) -> dict[str, Any]:
        closed_trades = [t for t in trades if t.exit_reason in ("TP_HIT", "SL_HIT")]
        total = len(closed_trades)
        wins = len([t for t in closed_trades if t.status == "WIN"])
        losses = len([t for t in closed_trades if t.status == "LOSS"])

        win_rate = round((wins / total * 100.0), 1) if total > 0 else 0.0
        total_pts = round(sum(t.pnl_pts for t in closed_trades), 2)
        net_profit_usd = round(sum(t.pnl_usd for t in closed_trades), 2)

        gross_profit = sum(t.pnl_usd for t in closed_trades if t.pnl_usd > 0)
        gross_loss = abs(sum(t.pnl_usd for t in closed_trades if t.pnl_usd < 0))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 0.0)

        # Max drawdown calculation
        equity = self.initial_capital
        peak = equity
        max_dd_usd = 0.0
        max_dd_pct = 0.0

        for t in closed_trades:
            equity += t.pnl_usd
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd_usd:
                max_dd_usd = dd
                max_dd_pct = round((dd / peak) * 100.0, 2) if peak > 0 else 0.0

        final_balance = round(self.initial_capital + net_profit_usd, 2)

        return {
            "symbol": self.symbol,
            "timeframe": selected_tf.upper(),
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "initial_capital": self.initial_capital,
            "final_balance": final_balance,
            "lot_size": self.lot_size,
            "total_trades": total,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": win_rate,
            "total_pts": total_pts,
            "net_profit_usd": net_profit_usd,
            "profit_factor": profit_factor,
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_pct": max_dd_pct,
        }
