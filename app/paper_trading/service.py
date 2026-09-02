"""
Paper Trading Service managing active positions and persistence.

Positions are tracked in-memory for low-latency state transitions AND persisted
to the database through the Repository so they survive server restarts.  The
caller supplies an optional Repository; persistence is skipped when it is None
(e.g. during unit tests without a database).
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import Settings, get_settings
from app.core.constants import SignalDirection, TradeState
from app.core.logging import logger
from app.data.models import Candle
from app.database.repository import Repository
from app.paper_trading.state_machine import PaperPosition, PaperTradeStateMachine
from app.risk.manager import RiskManager
from app.risk.models import RiskCalculationRequest
from app.signals.models import SignalPayload


def paper_position_to_dict(pos: PaperPosition) -> dict[str, Any]:
    """Serializes a PaperPosition into the PaperTradeModel column layout."""
    return {
        "id": pos.position_id,
        "signal_id": pos.signal_id,
        "symbol": pos.symbol,
        "direction": pos.direction.value,
        "state": pos.state.value,
        "lot_size": pos.lot_size,
        "risk_amount": pos.risk_amount_usd,
        "target_entry": pos.target_entry,
        "actual_entry": pos.actual_entry,
        "stop_loss": pos.stop_loss,
        "take_profit_1": pos.take_profit_1,
        "take_profit_2": pos.take_profit_2,
        "take_profit_3": pos.take_profit_3,
        "opened_at": pos.opened_at,
        "exit_price": pos.exit_price,
        "exit_reason": pos.exit_reason,
        "realized_pnl": pos.realized_pnl_usd,
        "realized_r": pos.realized_r,
        "closed_at": pos.closed_at,
        "state_logs": [log.model_dump(mode="json") for log in pos.history_logs],
    }


class PaperTradingService:
    """Paper trading manager with optional database persistence."""

    def __init__(self, initial_balance: float = 10000.0, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.risk_manager = RiskManager(self.settings)
        self.positions: dict[str, PaperPosition] = {}

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------

    async def open_position_from_signal(
        self,
        signal: SignalPayload,
        repo: Repository | None = None,
        fixed_lot_size: float | None = None,
    ) -> PaperPosition | None:
        """Creates a pending paper position from a valid signal and persists it.

        When ``fixed_lot_size`` is set (e.g. 0.01 for strategy tranches), the
        position is opened at that exact lot size, bypassing the risk manager.
        """
        if not signal.is_tradable or signal.direction == SignalDirection.NO_TRADE:
            return None

        # Check active trades count
        active = [p for p in self.positions.values() if p.state not in [TradeState.CLOSED, TradeState.INVALIDATED]]
        if len(active) >= self.settings.MAX_OPEN_TRADES:
            logger.info("Max open trades (%s) reached; ignoring signal %s.", self.settings.MAX_OPEN_TRADES, signal.signal_id)
            return None

        if fixed_lot_size is not None and fixed_lot_size > 0:
            lot_size = fixed_lot_size
            risk_amount_usd = round(
                lot_size * abs(signal.entry - signal.stop_loss) * self.settings.LOT_CONTRACT_SIZE,
                2,
            )
        else:
            risk_req = RiskCalculationRequest(
                account_balance=max(100.0, self.balance),
                risk_percent=self.settings.RISK_PERCENT,
                entry_price=signal.entry,
                stop_loss=signal.stop_loss,
                take_profit_1=signal.take_profit_1,
                take_profit_2=signal.take_profit_2,
                take_profit_3=signal.take_profit_3,
                direction=signal.direction,
                contract_size=self.settings.LOT_CONTRACT_SIZE,
            )
            pos_size = self.risk_manager.calculate_position_size(risk_req)
            if not pos_size.is_valid or pos_size.lot_size <= 0:
                logger.info("Position sizing rejected signal %s: %s", signal.signal_id, pos_size.rejection_reason)
                return None
            lot_size = pos_size.lot_size
            risk_amount_usd = pos_size.risk_amount_usd

        pos_id = str(uuid.uuid4())
        pos = PaperPosition(
            position_id=pos_id,
            signal_id=signal.signal_id,
            symbol=signal.instrument,
            direction=signal.direction,
            lot_size=lot_size,
            risk_amount_usd=risk_amount_usd,
            target_entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            take_profit_3=signal.take_profit_3,
            state=TradeState.PENDING,
            fees_usd=self.settings.PAPER_FEES_USD,
            spread_points=self.settings.PAPER_SPREAD_POINTS,
            slippage_pct=self.settings.PAPER_SLIPPAGE_PCT,
        )
        pos.transition_to(TradeState.PENDING, signal.entry, "Order placed as PENDING")
        self.positions[pos_id] = pos

        if repo is not None:
            await self._persist_position(repo, pos)
        return pos

    async def on_candle(
        self,
        candle: Candle,
        repo: Repository | None = None,
    ) -> list[PaperPosition]:
        """Updates all active positions against an incoming closed candle."""
        costs = {
            "fees_usd": self.settings.PAPER_FEES_USD,
            "spread_points": self.settings.PAPER_SPREAD_POINTS,
            "slippage_pct": self.settings.PAPER_SLIPPAGE_PCT,
        }
        updated = []
        for pos_id, pos in self.positions.items():
            if pos.state == TradeState.CLOSED:
                continue
            old_state = pos.state
            PaperTradeStateMachine.update_position(pos, candle, costs=costs)
            if pos.state != old_state:
                updated.append(pos)
            if pos.state == TradeState.CLOSED:
                self.balance = round(self.balance + (pos.realized_pnl_usd or 0.0), 2)
                if repo is not None:
                    from app.paper_trading.limits import TradingLimits
                    await TradingLimits(self.settings).register_trade_result(repo, pos.realized_pnl_usd or 0.0)
                logger.info(
                    "Paper position %s closed via %s, P/L=%.2f USD, new balance=%.2f",
                    pos_id, pos.exit_reason, pos.realized_pnl_usd, self.balance,
                )
            if repo is not None and pos.state != old_state:
                await self._persist_position(repo, pos)
        return updated

    # ------------------------------------------------------------------
    # Account metrics
    # ------------------------------------------------------------------

    def unrealized_pnl(self, current_price: float | None = None) -> float:
        """Total unrealized P&L across all open positions."""
        total = 0.0
        for pos in self.get_active_positions():
            if pos.actual_entry is None:
                continue
            if pos.direction == SignalDirection.LONG:
                total += (current_price - pos.actual_entry) * pos.lot_size * 100.0 if current_price else 0.0
            else:
                total += (pos.actual_entry - current_price) * pos.lot_size * 100.0 if current_price else 0.0
        return round(total, 2)

    def account_summary(self, current_price: float | None = None) -> dict[str, float]:
        """Returns account balance, equity, and margin estimates."""
        unrealized = self.unrealized_pnl(current_price)
        equity = round(self.balance + unrealized, 2)
        return {
            "balance": round(self.balance, 2),
            "equity": equity,
            "unrealized_pnl": unrealized,
            "available_margin": round(equity, 2),  # simplified: no margin loans in paper mode
            "open_positions": len(self.get_active_positions()),
        }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _persist_position(self, repo: Repository, pos: PaperPosition) -> None:
        existing = await repo.get_paper_trade(pos.position_id)
        if existing is None:
            await repo.create_paper_trade(paper_position_to_dict(pos))
        else:
            await repo.update_paper_trade(pos.position_id, paper_position_to_dict(pos))

    async def restore_from_db(self, repo: Repository) -> None:
        """Reconstructs in-memory positions and balance from persisted records."""
        db_trades = await repo.list_paper_trades(limit=1000)
        restored = 0
        for t in db_trades:
            pos = PaperPosition(
                position_id=t.id,
                signal_id=t.signal_id or "",
                symbol=t.symbol,
                direction=SignalDirection(t.direction),
                lot_size=t.lot_size,
                risk_amount_usd=t.risk_amount or 0.0,
                target_entry=t.target_entry,
                actual_entry=t.actual_entry,
                stop_loss=t.stop_loss,
                take_profit_1=t.take_profit_1,
                take_profit_2=t.take_profit_2,
                take_profit_3=t.take_profit_3,
                state=TradeState(t.state),
                created_at=t.created_at or datetime.now(timezone.utc),
                opened_at=t.opened_at,
                closed_at=t.closed_at,
                exit_price=t.exit_price,
                fees_usd=self.settings.PAPER_FEES_USD,
                spread_points=self.settings.PAPER_SPREAD_POINTS,
                slippage_pct=self.settings.PAPER_SLIPPAGE_PCT,
                realized_pnl_usd=t.realized_pnl or 0.0,
                realized_r=t.realized_r or 0.0,
            )
            if isinstance(t.state_logs, list):
                from app.paper_trading.state_machine import StateTransitionLog
                logs = [StateTransitionLog(**log) for log in t.state_logs if isinstance(log, dict)]
                pos.history_logs = logs
            if t.exit_reason:
                pos.exit_reason = t.exit_reason
            self.positions[pos.position_id] = pos
            restored += 1

        realized = await repo.sum_realized_pnl()
        self.balance = round(self.initial_balance + realized, 2)
        logger.info("Restored %s paper positions from DB. Balance=%.2f", restored, self.balance)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_active_positions(self) -> list[PaperPosition]:
        return [p for p in self.positions.values() if p.state not in [TradeState.CLOSED, TradeState.INVALIDATED]]

    def get_all_positions(self) -> list[PaperPosition]:
        return list(self.positions.values())

    def get_position(self, position_id: str) -> PaperPosition | None:
        return self.positions.get(position_id)