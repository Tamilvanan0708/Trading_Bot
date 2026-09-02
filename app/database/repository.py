"""
Database Repository for CRUD operations.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    AIValidationModel,
    BacktestRunModel,
    NotificationLogModel,
    PaperTradeModel,
    SignalModel,
    SystemStateModel,
)


class Repository:
    """Repository handling all database interactions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_signal(self, signal_dict: dict[str, Any]) -> SignalModel:
        """Stores a generated signal in the database."""
        signal = SignalModel(**signal_dict)
        self.session.add(signal)
        await self.session.flush()
        return signal

    async def get_signal_by_id(self, signal_id: str) -> SignalModel | None:
        """Fetches signal by ID with AI validation eagerly loaded."""
        stmt = (
            select(SignalModel)
            .where(SignalModel.id == signal_id)
            .options(selectinload(SignalModel.ai_validation))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_recent_signals(self, limit: int = 50) -> list[SignalModel]:
        """Lists recent signals ordered by creation timestamp."""
        stmt = (
            select(SignalModel)
            .options(selectinload(SignalModel.ai_validation))
            .order_by(desc(SignalModel.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def save_ai_validation(self, validation_dict: dict[str, Any]) -> AIValidationModel:
        """Stores AI validation result."""
        # Only pass known model columns so older DB schemas (without provider/
        # model/reason_code) don't fail on write.
        allowed = {
            "signal_id", "status", "confidence", "explanation",
            "identified_risks", "missing_confirmations", "raw_response",
            "provider", "model", "reason_code",
        }
        data = {k: v for k, v in validation_dict.items() if k in allowed}
        validation = AIValidationModel(**data)
        self.session.add(validation)
        await self.session.flush()
        return validation

    async def create_paper_trade(self, trade_dict: dict[str, Any]) -> PaperTradeModel:
        """Creates a new paper trade entry."""
        trade = PaperTradeModel(**trade_dict)
        self.session.add(trade)
        await self.session.flush()
        return trade

    async def update_paper_trade(self, trade_id: str, updates: dict[str, Any]) -> PaperTradeModel | None:
        """Updates paper trade record."""
        stmt = (
            update(PaperTradeModel)
            .where(PaperTradeModel.id == trade_id)
            .values(**updates)
            .returning(PaperTradeModel)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_paper_trade(self, trade_id: str) -> PaperTradeModel | None:
        """Fetches a single paper trade by id."""
        stmt = select(PaperTradeModel).where(PaperTradeModel.id == trade_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_paper_trades(self, limit: int = 50) -> list[PaperTradeModel]:
        """Lists recent paper trades."""
        stmt = (
            select(PaperTradeModel)
            .order_by(desc(PaperTradeModel.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_active_paper_trades(self) -> list[PaperTradeModel]:
        """Lists active paper trades."""
        stmt = (
            select(PaperTradeModel)
            .where(PaperTradeModel.state.in_(["OPEN", "PENDING", "ENTRY_HIT", "TP1_HIT", "TP2_HIT"]))
            .order_by(desc(PaperTradeModel.created_at))
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_closed_paper_trades(self, limit: int = 500) -> list[PaperTradeModel]:
        """Lists closed/invalidated paper trades for balance reconstruction."""
        stmt = (
            select(PaperTradeModel)
            .where(PaperTradeModel.state.in_(["CLOSED", "INVALIDATED", "STOP_LOSS_HIT", "TP3_HIT"]))
            .order_by(desc(PaperTradeModel.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def sum_realized_pnl(self) -> float:
        """Sums realized P/L across all closed paper trades.

        Uses the same terminal-state set as ``list_closed_paper_trades`` so the
        reconstructed balance never disagrees with the closed-trade listing.
        """
        from sqlalchemy import func
        stmt = select(func.coalesce(func.sum(PaperTradeModel.realized_pnl), 0.0)).where(
            PaperTradeModel.state.in_(["CLOSED", "INVALIDATED", "STOP_LOSS_HIT", "TP3_HIT"])
        )
        res = await self.session.execute(stmt)
        return float(res.scalar_one())

    async def save_backtest_run(self, backtest_dict: dict[str, Any]) -> BacktestRunModel:
        """Stores a completed backtest run."""
        run = BacktestRunModel(**backtest_dict)
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_backtest_run(self, run_id: str) -> BacktestRunModel | None:
        """Gets backtest result by ID."""
        stmt = select(BacktestRunModel).where(BacktestRunModel.id == run_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_backtest_runs(self, limit: int = 20) -> list[BacktestRunModel]:
        """Lists historical backtest runs."""
        stmt = (
            select(BacktestRunModel)
            .order_by(desc(BacktestRunModel.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def log_notification(self, log_dict: dict[str, Any]) -> NotificationLogModel:
        """Logs an outbound notification."""
        log = NotificationLogModel(**log_dict)
        self.session.add(log)
        await self.session.flush()
        return log

    async def list_notifications(self, limit: int = 50) -> list[NotificationLogModel]:
        """Lists recent notification log entries."""
        stmt = select(NotificationLogModel).order_by(desc(NotificationLogModel.created_at)).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    # ------------------------------------------------------------------
    # System state (restart-safe key/value store)
    # ------------------------------------------------------------------

    async def get_system_state(self, key: str, default: str = "") -> str:
        stmt = select(SystemStateModel).where(SystemStateModel.key == key)
        res = await self.session.execute(stmt)
        row = res.scalar_one_or_none()
        return row.value if row else default

    async def set_system_state(self, key: str, value: str) -> None:
        stmt = select(SystemStateModel).where(SystemStateModel.key == key)
        res = await self.session.execute(stmt)
        row = res.scalar_one_or_none()
        if row is None:
            self.session.add(SystemStateModel(key=key, value=value))
        else:
            row.value = value
            row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def list_recent_signals_by_candle(self, candle_ts: datetime) -> list[SignalModel]:
        """Finds signals generated for a specific candle timestamp (dedup support)."""
        stmt = (
            select(SignalModel)
            .where(SignalModel.created_at >= candle_ts - timedelta(minutes=1))
            .where(SignalModel.created_at <= candle_ts + timedelta(minutes=1))
            .order_by(desc(SignalModel.created_at))
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_open_signals_for_tracking(self, limit: int = 500) -> list[SignalModel]:
        """Lists LONG/SHORT signals that still need forward-outcome tracking."""
        stmt = (
            select(SignalModel)
            .where(SignalModel.direction.in_(["LONG", "SHORT"]))
            .where(
                (SignalModel.outcome.is_(None))
                | (SignalModel.outcome == "OPEN")
            )
            .order_by(desc(SignalModel.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def update_signal_outcome(self, signal_id: str, updates: dict[str, Any]) -> SignalModel | None:
        """Updates forward-outcome columns on a signal row."""
        stmt = (
            update(SignalModel)
            .where(SignalModel.id == signal_id)
            .values(**updates)
            .returning(SignalModel)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def stamp_signal_metadata(self, signal_id: str, extra: dict[str, Any]) -> SignalModel | None:
        """Merges extra keys into a signal's metadata_payload JSON column."""
        sig = await self.get_signal_by_id(signal_id)
        if sig is None:
            return None
        current = dict(sig.metadata_payload or {})
        current.update(extra)
        stmt = (
            update(SignalModel)
            .where(SignalModel.id == signal_id)
            .values(metadata_payload=current)
            .returning(SignalModel)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def count_trades_since(self, since: datetime) -> int:
        """Counts paper trades created since a given time (daily trade limit)."""
        from sqlalchemy import func
        stmt = (
            select(func.count(PaperTradeModel.id))
            .where(PaperTradeModel.created_at >= since)
        )
        res = await self.session.execute(stmt)
        return int(res.scalar_one())

    async def list_ai_validation_history(self, limit: int = 50) -> list[dict]:
        """Returns recent signals with their AI validation for the decision-audit
        history table.  Each entry includes signal direction, AI decision, AI
        confidence, confluence, risk, and outcome."""
        stmt = (
            select(SignalModel)
            .options(selectinload(SignalModel.ai_validation))
            .order_by(desc(SignalModel.created_at))
            .limit(min(max(limit, 1), 500))
        )
        res = await self.session.execute(stmt)
        rows = list(res.scalars().all())
        out = []
        for s in rows:
            ai = s.ai_validation
            out.append({
                "time": s.created_at,
                "direction": s.direction,
                "signal_quality": s.signal_quality,
                "confidence_score": s.confidence_score,
                "risk_reward": s.risk_reward,
                "ai_decision": ai.status if ai else "NONE",
                "ai_confidence": ai.confidence if ai else 0.0,
                "provider": ai.provider if ai else None,
                "model": ai.model if ai else None,
                "reason_code": ai.reason_code if ai else None,
                "outcome": s.outcome,
            })
        return out
