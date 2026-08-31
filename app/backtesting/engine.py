"""
Event-Driven Backtesting Engine for XAU/USD.

Guarantees:
  - Zero look-ahead bias: only data up to and including the current bar is used.
  - Chronological processing: bars are iterated in strictly increasing time.
  - Same-candle SL priority: stop loss is checked before take profit.
  - Configurable spread, slippage and transaction costs.
"""

import asyncio
import uuid
from typing import Any

import numpy as np

from app.backtesting.models import BacktestResult, PerformanceSummary, SimulatedTrade
from app.config.settings import Settings, get_settings
from app.core.constants import SignalDirection, StrategyType, TimeFrame, TradeState
from app.core.logging import logger
from app.data.models import Candle, MultiTimeframeSnapshot
from app.data.timeframe_resampler import IncrementalResampler
from app.risk.manager import RiskManager
from app.risk.models import RiskCalculationRequest
from app.signals.engine import SignalEngine


class BacktestEngine:
    """
    Simulates strategy and confluence execution over historical 15M candles
    with zero look-ahead bias.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.signal_engine = SignalEngine(self.settings)
        self.risk_manager = RiskManager(self.settings)

    def run(
        self,
        candles_15m: list[Candle],
        initial_balance: float = 10000.0,
        risk_percent: float = 1.0,
        warmup_bars: int = 150,
        spread_points: float | None = None,
        slippage_pct: float | None = None,
        transaction_cost_usd: float | None = None,
        entry_on_next_open: bool | None = None,
        ai_validator: Any | None = None,
    ) -> BacktestResult:
        """
        Executes backtest over historical candle array.

        Args:
            candles_15m: Historical 15M candles (must be sorted by timestamp).
            initial_balance: Starting account balance.
            risk_percent: Percent of balance to risk per trade.
            warmup_bars: Number of initial candles skipped for indicator warmup.
            spread_points: Average spread in price points (widens SL, narrows TP).
            slippage_pct: Slippage as fraction of entry price (0.01 = 1%).
            transaction_cost_usd: Fixed cost deducted from every trade's P&L.
            entry_on_next_open: If True, trade is entered at the next bar's open
                instead of the signal bar's close.
            ai_validator: Optional AIValidator used to record per-trade AI status
                (research only; never alters deterministic trade logic).
        """
        spread_pts = spread_points if spread_points is not None else self.settings.BACKTEST_SPREAD_POINTS
        slippage = slippage_pct if slippage_pct is not None else self.settings.BACKTEST_SLIPPAGE_PCT
        txn_cost = transaction_cost_usd if transaction_cost_usd is not None else self.settings.BACKTEST_TRANSACTION_COST_USD
        entry_next_open = entry_on_next_open if entry_on_next_open is not None else self.settings.BACKTEST_ENTRY_ON_NEXT_OPEN
        if len(candles_15m) <= warmup_bars:
            raise ValueError(f"Need more than {warmup_bars} candles for backtest warmup (got {len(candles_15m)}).")

        balance = initial_balance
        peak_balance = initial_balance
        max_drawdown_usd = 0.0
        equity_curve: list[dict[str, Any]] = []

        open_trades: list[SimulatedTrade] = []
        closed_trades: list[SimulatedTrade] = []

        start_time = candles_15m[warmup_bars].timestamp
        end_time = candles_15m[-1].timestamp

        # Incremental MTF resampler: near-linear time, EXACT same series as
        # calling resample_candles(history_slice, TF) for every prefix.
        resampler = IncrementalResampler()
        for i in range(warmup_bars, len(candles_15m)):
            curr_bar = candles_15m[i]
            history_slice = candles_15m[: i + 1]
            resampler.add(curr_bar)

            # --- 1. Manage open trades (SL/TP check, same-candle SL priority) ---
            remaining_trades: list[SimulatedTrade] = []
            floating_pnl = 0.0
            for trade in open_trades:
                closed = False

                if trade.direction == SignalDirection.LONG:
                    # Adjust SL/TP for spread (conservative: narrow SL, narrow TP)
                    effective_sl = trade.stop_loss + spread_pts / 2.0
                    effective_tp1 = trade.take_profit_1 - spread_pts / 2.0
                    effective_tp2 = trade.take_profit_2 - spread_pts / 2.0
                    effective_tp3 = trade.take_profit_3 - spread_pts / 2.0

                    # Same-candle: SL checked first (conservative)
                    if curr_bar.low <= effective_sl:
                        trade.exit_price = effective_sl
                        trade.exit_time = curr_bar.timestamp
                        trade.exit_reason = "STOP_LOSS_HIT"
                        trade.state = TradeState.STOP_LOSS_HIT
                        loss_per_unit = trade.entry_price - effective_sl
                        trade.pnl_usd = round(trade.lot_size * loss_per_unit * 100.0 - txn_cost, 2)
                        trade.pnl_r = -1.0
                        closed = True
                    elif curr_bar.high >= effective_tp3:
                        trade.exit_price = effective_tp3
                        trade.exit_time = curr_bar.timestamp
                        trade.exit_reason = "TP3_HIT"
                        trade.state = TradeState.TP3_HIT
                        dist = trade.exit_price - trade.entry_price
                        trade.pnl_usd = round(trade.lot_size * dist * 100.0 - txn_cost, 2)
                        trade.pnl_r = round(dist / max(0.1, trade.entry_price - trade.stop_loss), 2)
                        closed = True
                    elif curr_bar.high >= effective_tp2 and trade.state != TradeState.TP2_HIT:
                        trade.state = TradeState.TP2_HIT
                    elif curr_bar.high >= effective_tp1 and trade.state == TradeState.ENTRY_HIT:
                        trade.state = TradeState.TP1_HIT

                elif trade.direction == SignalDirection.SHORT:
                    effective_sl = trade.stop_loss - spread_pts / 2.0
                    effective_tp1 = trade.take_profit_1 + spread_pts / 2.0
                    effective_tp2 = trade.take_profit_2 + spread_pts / 2.0
                    effective_tp3 = trade.take_profit_3 + spread_pts / 2.0

                    if curr_bar.high >= effective_sl:
                        trade.exit_price = effective_sl
                        trade.exit_time = curr_bar.timestamp
                        trade.exit_reason = "STOP_LOSS_HIT"
                        trade.state = TradeState.STOP_LOSS_HIT
                        loss_per_unit = effective_sl - trade.entry_price
                        trade.pnl_usd = round(trade.lot_size * loss_per_unit * 100.0 - txn_cost, 2)
                        trade.pnl_r = -1.0
                        closed = True
                    elif curr_bar.low <= effective_tp3:
                        trade.exit_price = effective_tp3
                        trade.exit_time = curr_bar.timestamp
                        trade.exit_reason = "TP3_HIT"
                        trade.state = TradeState.TP3_HIT
                        dist = trade.entry_price - trade.exit_price
                        trade.pnl_usd = round(trade.lot_size * dist * 100.0 - txn_cost, 2)
                        trade.pnl_r = round(dist / max(0.1, trade.stop_loss - trade.entry_price), 2)
                        closed = True
                    elif curr_bar.low <= effective_tp2 and trade.state != TradeState.TP2_HIT:
                        trade.state = TradeState.TP2_HIT
                    elif curr_bar.low <= effective_tp1 and trade.state == TradeState.ENTRY_HIT:
                        trade.state = TradeState.TP1_HIT

                if closed:
                    balance += trade.pnl_usd
                    peak_balance = max(peak_balance, balance)
                    dd = peak_balance - balance
                    max_drawdown_usd = max(max_drawdown_usd, dd)
                    closed_trades.append(trade)
                else:
                    remaining_trades.append(trade)
                    if trade.direction == SignalDirection.LONG:
                        floating_pnl += (curr_bar.close - trade.entry_price) * trade.lot_size * 100.0
                    else:
                        floating_pnl += (trade.entry_price - curr_bar.close) * trade.lot_size * 100.0

            open_trades = remaining_trades

            # Track equity curve
            equity = balance + floating_pnl
            equity_curve.append({
                "time": curr_bar.timestamp.isoformat(),
                "balance": round(balance, 2),
                "equity": round(equity, 2),
                "floating_pnl": round(floating_pnl, 2),
            })

            # --- 2. Signal generation + entry (if max trades not reached) ---
            if len(open_trades) < self.settings.MAX_OPEN_TRADES:
                m30 = resampler.series(TimeFrame.M30, 100)
                h1 = resampler.series(TimeFrame.H1, 80)
                h4 = resampler.series(TimeFrame.H4, 50)

                snapshot = MultiTimeframeSnapshot(
                    symbol="XAUUSD",
                    timestamp=curr_bar.timestamp,
                    current_price=curr_bar.close,
                    m15=history_slice[-150:],
                    m30=m30,
                    h1=h1,
                    h4=h4,
                )

                signal = self.signal_engine.generate_signal(snapshot)

                if signal.is_tradable and signal.direction != SignalDirection.NO_TRADE:
                    risk_req = RiskCalculationRequest(
                        account_balance=max(100.0, balance),
                        risk_percent=risk_percent,
                        entry_price=signal.entry,
                        stop_loss=signal.stop_loss,
                        take_profit_1=signal.take_profit_1,
                        take_profit_2=signal.take_profit_2,
                        take_profit_3=signal.take_profit_3,
                        direction=signal.direction,
                        contract_size=self.settings.LOT_CONTRACT_SIZE,
                    )
                    pos = self.risk_manager.calculate_position_size(risk_req)

                    if pos.is_valid and pos.lot_size > 0:
                        entry_price = signal.entry * (1 + slippage) if signal.direction == SignalDirection.LONG else signal.entry * (1 - slippage)
                        if entry_next_open and i + 1 < len(candles_15m):
                            entry_price = candles_15m[i + 1].open * (1 + slippage) if signal.direction == SignalDirection.LONG else candles_15m[i + 1].open * (1 - slippage)
                        entry_price = round(entry_price, 2)

                        # GEOMETRY SAFETY: after applying spread/slippage, the entry
                        # and trigger levels must still be geometrically valid.
                        # Spread-adjusted levels are used to stay consistent with
                        # the trade-management trigger checks (narrowed SL/TP).
                        if signal.direction == SignalDirection.LONG:
                            adj_sl = signal.stop_loss + spread_pts / 2.0
                            adj_tp1 = signal.take_profit_1 - spread_pts / 2.0
                            adj_tp2 = signal.take_profit_2 - spread_pts / 2.0
                            adj_tp3 = signal.take_profit_3 - spread_pts / 2.0
                            geom_ok = (adj_sl < entry_price
                                       <= adj_tp1 <= adj_tp2 <= adj_tp3)
                        else:
                            adj_sl = signal.stop_loss - spread_pts / 2.0
                            adj_tp1 = signal.take_profit_1 + spread_pts / 2.0
                            adj_tp2 = signal.take_profit_2 + spread_pts / 2.0
                            adj_tp3 = signal.take_profit_3 + spread_pts / 2.0
                            geom_ok = (adj_sl > entry_price
                                       >= adj_tp1 >= adj_tp2 >= adj_tp3)
                        if not geom_ok:
                            logger.warning(
                                "Skipping trade: slippage/spread inverted geometry at %s "
                                "(entry=%.2f sl=%.2f tp1=%.2f tp2=%.2f)",
                                curr_bar.timestamp, entry_price,
                                signal.stop_loss, signal.take_profit_1, signal.take_profit_2,
                            )
                            continue

                        # Research-only: record AI validation status without altering
                        # any deterministic trade logic.
                        ai_status = None
                        if ai_validator is not None:
                            try:
                                ai_result = self._run_ai_validation(ai_validator, signal)
                                ai_status = ai_result.status.value if ai_result else None
                            except Exception as ai_exc:  # noqa: BLE001
                                logger.warning("AI validation skipped for backtest trade: %s", ai_exc)

                        new_trade = SimulatedTrade(
                            trade_id=str(uuid.uuid4()),
                            symbol="XAUUSD",
                            direction=signal.direction,
                            strategy=signal.strategy,
                            entry_time=curr_bar.timestamp,
                            entry_price=round(entry_price, 2),
                            stop_loss=signal.stop_loss,
                            take_profit_1=signal.take_profit_1,
                            take_profit_2=signal.take_profit_2,
                            take_profit_3=signal.take_profit_3,
                            lot_size=pos.lot_size,
                            risk_usd=pos.risk_amount_usd,
                            state=TradeState.ENTRY_HIT,
                            confidence_score=signal.confidence_score,
                            signal_quality=signal.signal_quality.value if signal.signal_quality else None,
                            ai_status=ai_status,
                        )
                        open_trades.append(new_trade)

        # --- 3. Force-close remaining open trades at end if desired ---
        for trade in open_trades:
            trade.exit_price = candles_15m[-1].close
            trade.exit_time = candles_15m[-1].timestamp
            trade.exit_reason = "END_OF_BACKTEST"
            trade.state = TradeState.CLOSED
            closed_trades.append(trade)

        # --- 4. Calculate Performance Metrics ---
        summary = self._calculate_metrics(initial_balance, balance, peak_balance, max_drawdown_usd, closed_trades)

        return BacktestResult(
            symbol="XAUUSD",
            start_time=start_time,
            end_time=end_time,
            summary=summary,
            trades=closed_trades,
            equity_curve=equity_curve,
        )

    def _run_ai_validation(self, ai_validator, signal) -> Any:
        """Runs the async AI validator safely from the synchronous backtest loop."""
        coro = ai_validator.validate_signal(signal)
        try:
            asyncio.get_running_loop()
            # A loop is already running (e.g. API context) — run in a fresh thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(coro)).result()
        except RuntimeError:
            return asyncio.run(coro)

    def _calculate_metrics(
        self,
        initial_balance: float,
        final_balance: float,
        peak_balance: float,
        max_dd_usd: float,
        trades: list[SimulatedTrade],
    ) -> PerformanceSummary:
        total_trades = len(trades)
        if total_trades == 0:
            return PerformanceSummary(
                initial_balance=initial_balance,
                final_balance=final_balance,
                net_profit_usd=0.0,
                net_return_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                max_drawdown_usd=0.0,
                max_drawdown_pct=0.0,
                expectancy_r=0.0,
                avg_win_usd=0.0,
                avg_loss_usd=0.0,
                long_trades_count=0,
                long_win_rate_pct=0.0,
                short_trades_count=0,
                short_win_rate_pct=0.0,
                strategy_breakdown={},
            )

        winning_trades = [t for t in trades if t.pnl_usd > 0]
        losing_trades = [t for t in trades if t.pnl_usd <= 0]

        total_win_usd = sum(t.pnl_usd for t in winning_trades)
        total_loss_usd = abs(sum(t.pnl_usd for t in losing_trades))

        win_rate = round((len(winning_trades) / total_trades) * 100.0, 2)
        profit_factor = round(total_win_usd / total_loss_usd, 2) if total_loss_usd > 0 else (999.99 if total_win_usd > 0 else 0.0)
        net_profit = round(final_balance - initial_balance, 2)
        net_return_pct = round((net_profit / initial_balance) * 100.0, 2)
        max_dd_pct = round((max_dd_usd / peak_balance) * 100.0, 2) if peak_balance > 0 else 0.0

        avg_win = round(total_win_usd / len(winning_trades), 2) if winning_trades else 0.0
        avg_loss = round(total_loss_usd / len(losing_trades), 2) if losing_trades else 0.0
        expectancy_r = round(float(np.mean([t.pnl_r for t in trades])), 2)

        longs = [t for t in trades if t.direction == SignalDirection.LONG]
        shorts = [t for t in trades if t.direction == SignalDirection.SHORT]
        long_wins = [t for t in longs if t.pnl_usd > 0]
        short_wins = [t for t in shorts if t.pnl_usd > 0]

        long_wr = round((len(long_wins) / len(longs)) * 100.0, 2) if longs else 0.0
        short_wr = round((len(short_wins) / len(shorts)) * 100.0, 2) if shorts else 0.0

        strat_breakdown: dict[str, dict[str, float]] = {}
        for stype in StrategyType:
            s_trades = [t for t in trades if t.strategy == stype]
            if s_trades:
                s_wins = [t for t in s_trades if t.pnl_usd > 0]
                s_pnl = sum(t.pnl_usd for t in s_trades)
                strat_breakdown[stype.value] = {
                    "total_trades": len(s_trades),
                    "win_rate_pct": round((len(s_wins) / len(s_trades)) * 100.0, 2),
                    "net_pnl_usd": round(s_pnl, 2),
                }

        return PerformanceSummary(
            initial_balance=initial_balance,
            final_balance=round(final_balance, 2),
            net_profit_usd=net_profit,
            net_return_pct=net_return_pct,
            total_trades=total_trades,
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            max_drawdown_usd=round(max_dd_usd, 2),
            max_drawdown_pct=max_dd_pct,
            expectancy_r=expectancy_r,
            avg_win_usd=avg_win,
            avg_loss_usd=avg_loss,
            long_trades_count=len(longs),
            long_win_rate_pct=long_wr,
            short_trades_count=len(shorts),
            short_win_rate_pct=short_wr,
            strategy_breakdown=strat_breakdown,
        )