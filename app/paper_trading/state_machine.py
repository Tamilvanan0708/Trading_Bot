"""
Paper Trading State Machine and Order Lifecycle.

Supports realistic fills: PENDING -> OPEN (ENTRY_HIT) -> PARTIAL TP (TP1/TP2)
-> SL / TP3 -> CLOSED, with configurable fees, spread, and slippage applied
to the actual fill and exit prices.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.core.constants import SignalDirection, TradeState
from app.data.models import Candle


class StateTransitionLog(BaseModel):
    """Log record for each state transition."""
    from_state: TradeState
    to_state: TradeState
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trigger_price: float
    message: str


class PaperPosition(BaseModel):
    """Active or Closed Paper Trading Position."""
    position_id: str
    signal_id: str
    symbol: str = "XAUUSD"
    direction: SignalDirection
    lot_size: float
    risk_amount_usd: float
    target_entry: float
    actual_entry: float | None = None
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    state: TradeState = TradeState.SIGNAL_GENERATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    fees_usd: float = 0.0
    spread_points: float = 0.0
    slippage_pct: float = 0.0
    unrealized_pnl_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    realized_r: float = 0.0
    history_logs: list[StateTransitionLog] = Field(default_factory=list)

    def transition_to(self, new_state: TradeState, trigger_price: float, message: str) -> None:
        """Transitions state and appends to audit log."""
        log = StateTransitionLog(
            from_state=self.state,
            to_state=new_state,
            trigger_price=trigger_price,
            message=message,
        )
        self.history_logs.append(log)
        self.state = new_state

    def mark_to_market(self, current_price: float) -> float:
        """Updates and returns unrealized P&L at the given price."""
        if self.actual_entry is None or self.state == TradeState.CLOSED:
            self.unrealized_pnl_usd = 0.0
            return 0.0
        if self.direction == SignalDirection.LONG:
            pnl = (current_price - self.actual_entry) * self.lot_size * 100.0
        else:
            pnl = (self.actual_entry - current_price) * self.lot_size * 100.0
        self.unrealized_pnl_usd = round(pnl, 2)
        return self.unrealized_pnl_usd


class PaperTradeStateMachine:
    """State machine advancing paper positions as new candles arrive."""

    @staticmethod
    def update_position(
        pos: PaperPosition,
        candle: Candle,
        costs: dict[str, float] | None = None,
    ) -> PaperPosition:
        """Updates paper position state given a new market candle.

        Args:
            costs: dict with optional `fees_usd`, `spread_points`, `slippage_pct`.
        """
        if pos.state == TradeState.CLOSED:
            return pos

        costs = costs or {}
        fees = float(costs.get("fees_usd", pos.fees_usd))
        spread = float(costs.get("spread_points", pos.spread_points))
        slippage = float(costs.get("slippage_pct", pos.slippage_pct))

        # --- State 1: PENDING / SIGNAL_GENERATED -> awaiting entry fill ---
        if pos.state in [TradeState.SIGNAL_GENERATED, TradeState.PENDING]:
            entry_hit = False
            if pos.direction == SignalDirection.LONG or pos.direction == SignalDirection.SHORT:
                if candle.low <= pos.target_entry <= candle.high:
                    entry_hit = True

            if entry_hit:
                # Apply spread + slippage to the actual fill (realistic entry)
                if pos.direction == SignalDirection.LONG:
                    fill = round(pos.target_entry + spread / 2.0 + pos.target_entry * slippage, 2)
                else:
                    fill = round(pos.target_entry - spread / 2.0 - pos.target_entry * slippage, 2)

                # GEOMETRY SAFETY: costs must not invert the SL/TP relationship.
                if pos.direction == SignalDirection.LONG:
                    geom_ok = pos.stop_loss < fill <= pos.take_profit_1 <= pos.take_profit_2 <= pos.take_profit_3
                else:
                    geom_ok = pos.stop_loss > fill >= pos.take_profit_1 >= pos.take_profit_2 >= pos.take_profit_3
                if not geom_ok:
                    # Costs are excessive relative to the setup — cancel the fill.
                    pos.transition_to(
                        TradeState.CLOSED, pos.target_entry,
                        f"Fill rejected: costs invert geometry (fill {fill}).",
                    )
                    pos.exit_reason = "FILL_REJECTED"
                    pos.closed_at = candle.timestamp
                    pos.exit_price = pos.target_entry
                    pos.realized_pnl_usd = 0.0
                    pos.realized_r = 0.0
                    return pos

                pos.actual_entry = fill
                pos.fees_usd = fees
                pos.opened_at = candle.timestamp
                pos.transition_to(
                    TradeState.ENTRY_HIT, pos.actual_entry,
                    f"Entry filled at {pos.actual_entry} (target {pos.target_entry})",
                )

        # --- State 2: Active Trade (ENTRY_HIT or TP1_HIT / TP2_HIT) ---
        if pos.state in [TradeState.ENTRY_HIT, TradeState.TP1_HIT, TradeState.TP2_HIT]:
            entry_ref = pos.actual_entry or pos.target_entry
            # Spread-adjusted trigger levels (conservative: SL easier to hit,
            # TP harder to hit) — consistent with the backtest engine.
            if pos.direction == SignalDirection.LONG:
                eff_sl = pos.stop_loss + spread / 2.0
                eff_tp1 = pos.take_profit_1 - spread / 2.0
                eff_tp2 = pos.take_profit_2 - spread / 2.0
                eff_tp3 = pos.take_profit_3 - spread / 2.0
            else:
                eff_sl = pos.stop_loss - spread / 2.0
                eff_tp1 = pos.take_profit_1 + spread / 2.0
                eff_tp2 = pos.take_profit_2 + spread / 2.0
                eff_tp3 = pos.take_profit_3 + spread / 2.0

            if pos.direction == SignalDirection.LONG:
                if candle.low <= eff_sl:
                    pos.exit_price = eff_sl
                    pos.closed_at = candle.timestamp
                    pos.exit_reason = "STOP_LOSS_HIT"
                    loss_per_unit = entry_ref - eff_sl
                    pos.realized_pnl_usd = round(-(pos.lot_size * loss_per_unit * 100.0) - fees, 2)
                    pos.realized_r = -1.0
                    pos.transition_to(TradeState.STOP_LOSS_HIT, eff_sl, f"Stop loss triggered at {eff_sl}")
                    pos.transition_to(TradeState.CLOSED, eff_sl, "Position closed on Stop Loss")
                elif candle.high >= eff_tp3:
                    pos.exit_price = eff_tp3
                    pos.closed_at = candle.timestamp
                    pos.exit_reason = "TP3_HIT"
                    dist = eff_tp3 - entry_ref
                    pos.realized_pnl_usd = round(pos.lot_size * dist * 100.0 - fees, 2)
                    pos.realized_r = round(dist / max(0.1, entry_ref - pos.stop_loss), 2)
                    pos.transition_to(TradeState.TP3_HIT, eff_tp3, f"Final Take Profit 3 hit at {eff_tp3}")
                    pos.transition_to(TradeState.CLOSED, eff_tp3, "Position fully closed on TP3")
                elif candle.high >= eff_tp2 and pos.state != TradeState.TP2_HIT:
                    pos.transition_to(TradeState.TP2_HIT, eff_tp2, f"Take Profit 2 hit at {eff_tp2}")
                elif candle.high >= eff_tp1 and pos.state == TradeState.ENTRY_HIT:
                    pos.transition_to(TradeState.TP1_HIT, eff_tp1, f"Take Profit 1 hit at {eff_tp1}")

            elif pos.direction == SignalDirection.SHORT:
                if candle.high >= eff_sl:
                    pos.exit_price = eff_sl
                    pos.closed_at = candle.timestamp
                    pos.exit_reason = "STOP_LOSS_HIT"
                    loss_per_unit = eff_sl - entry_ref
                    pos.realized_pnl_usd = round(-(pos.lot_size * loss_per_unit * 100.0) - fees, 2)
                    pos.realized_r = -1.0
                    pos.transition_to(TradeState.STOP_LOSS_HIT, eff_sl, f"Stop loss triggered at {eff_sl}")
                    pos.transition_to(TradeState.CLOSED, eff_sl, "Position closed on Stop Loss")
                elif candle.low <= eff_tp3:
                    pos.exit_price = eff_tp3
                    pos.closed_at = candle.timestamp
                    pos.exit_reason = "TP3_HIT"
                    dist = entry_ref - eff_tp3
                    pos.realized_pnl_usd = round(pos.lot_size * dist * 100.0 - fees, 2)
                    pos.realized_r = round(dist / max(0.1, pos.stop_loss - entry_ref), 2)
                    pos.transition_to(TradeState.TP3_HIT, eff_tp3, f"Final Take Profit 3 hit at {eff_tp3}")
                    pos.transition_to(TradeState.CLOSED, eff_tp3, "Position fully closed on TP3")
                elif candle.low <= eff_tp2 and pos.state != TradeState.TP2_HIT:
                    pos.transition_to(TradeState.TP2_HIT, eff_tp2, f"Take Profit 2 hit at {eff_tp2}")
                elif candle.low <= eff_tp1 and pos.state == TradeState.ENTRY_HIT:
                    pos.transition_to(TradeState.TP1_HIT, eff_tp1, f"Take Profit 1 hit at {eff_tp1}")

        # Update unrealized P&L for active positions
        if pos.state in [TradeState.ENTRY_HIT, TradeState.TP1_HIT, TradeState.TP2_HIT]:
            pos.mark_to_market(candle.close)

        return pos