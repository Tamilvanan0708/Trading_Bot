"""
RETRACEMENT_BOS_V1 — Repository for persisting setups and event history.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.retracement.models import RetracementEvent, RetracementSetup
from app.retracement.persistence import (
    RetracementEventModel,
    RetracementSetupModel,
    _encode_event,
)


def _setup_to_dict(setup: RetracementSetup) -> dict[str, Any]:
    return {
        "setup_id": setup.setup_id,
        "strategy": setup.strategy,
        "symbol": setup.symbol,
        "direction": setup.direction,
        "timeframe": setup.timeframe,
        "state": setup.state.value,
        "point_1_timestamp": setup.point_1_timestamp,
        "point_1_price": setup.point_1_price,
        "bos_timestamp": setup.bos_timestamp,
        "bos_price": setup.bos_price,
        "point_2_timestamp": setup.point_2_timestamp,
        "point_2_price": setup.point_2_price,
        "current_high_timestamp": setup.current_high_timestamp,
        "current_high_price": setup.current_high_price,
        "fib_0": setup.fib_0,
        "fib_0_236": setup.fib_0_236,
        "fib_0_382": setup.fib_0_382,
        "fib_0_500": setup.fib_0_500,
        "fib_0_618": setup.fib_0_618,
        "fib_1_000": setup.fib_1_000,
        "fib_1_618": setup.fib_1_618,
        "entry_price": setup.entry_price,
        "sl_price": setup.sl_price,
        "dynamic_tp": setup.dynamic_tp,
        "locked_tp": setup.locked_tp,
        "tp_before_freeze": setup.tp_before_freeze,
        "tp_locked": setup.entry_timestamp if setup.tp_locked else None,
        "entry_touched": setup.entry_timestamp if setup.entry_touched else None,
        "entry_timestamp": setup.entry_timestamp,
        "validation_passed": setup.updated_at if setup.validation_passed else None,
        "insufficient_structure_reason": setup.insufficient_structure_reason or None,
        "created_at": setup.created_at,
        "updated_at": setup.updated_at,
        "invalidation_reason": setup.invalidation_reason or None,
        "completion_reason": setup.completion_reason or None,
        "strategy_version": setup.strategy_version,
        "outcome": setup.outcome,
        "max_r": setup.max_r,
        "mae_r": setup.mae_r,
        "mfe_r": setup.mfe_r,
    }


class RetracementRepository:
    """Persistence layer for RETRACEMENT_BOS_V1 setups and events."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_setup(self, setup: RetracementSetup) -> None:
        """Upsert a setup (persist every important state transition)."""
        data = _setup_to_dict(setup)
        stmt = select(RetracementSetupModel).where(RetracementSetupModel.setup_id == setup.setup_id)
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing is None:
            self.session.add(RetracementSetupModel(**data))
        else:
            for k, v in data.items():
                setattr(existing, k, v)
        await self.session.flush()

    async def save_event(self, event: RetracementEvent) -> None:
        """Persist an event history entry."""
        self.session.add(RetracementEventModel(**_encode_event(event)))
        await self.session.flush()

    async def load_setup_by_id(self, setup_id: str) -> RetracementSetup | None:
        stmt = select(RetracementSetupModel).where(RetracementSetupModel.setup_id == setup_id)
        res = await self.session.execute(stmt)
        row = res.scalar_one_or_none()
        return row.to_domain() if row else None

    async def load_latest_active(self, symbol: str, strategy: str = "RETRACEMENT_BOS_V1",
                                  timeframe: str | None = None) -> RetracementSetup | None:
        """Load the most recent setup that is not COMPLETED/INVALIDATED.

        When ``timeframe`` is provided, only setups for that timeframe are
        returned — each timeframe has its own independent active setup.
        """
        stmt = (
            select(RetracementSetupModel)
            .where(RetracementSetupModel.symbol == symbol)
            .where(RetracementSetupModel.strategy == strategy)
        )
        if timeframe is not None:
            stmt = stmt.where(RetracementSetupModel.timeframe == timeframe)
        stmt = stmt.where(RetracementSetupModel.state.notin_(["COMPLETED", "INVALIDATED"]))
        stmt = stmt.order_by(RetracementSetupModel.updated_at.desc()).limit(1)
        res = await self.session.execute(stmt)
        row = res.scalar_one_or_none()
        return row.to_domain() if row else None

    async def load_all_setups(self, symbol: str | None = None, limit: int = 100) -> list[RetracementSetup]:
        stmt = select(RetracementSetupModel)
        if symbol:
            stmt = stmt.where(RetracementSetupModel.symbol == symbol)
        stmt = stmt.order_by(RetracementSetupModel.updated_at.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return [r.to_domain() for r in res.scalars().all()]

    async def load_events(self, setup_id: str, limit: int = 500) -> list[RetracementEvent]:
        stmt = (
            select(RetracementEventModel)
            .where(RetracementEventModel.setup_id == setup_id)
            .order_by(RetracementEventModel.timestamp)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return [r.to_domain() for r in res.scalars().all()]

    async def delete_setup(self, setup_id: str) -> None:
        await self.session.execute(delete(RetracementEventModel).where(RetracementEventModel.setup_id == setup_id))
        await self.session.execute(delete(RetracementSetupModel).where(RetracementSetupModel.setup_id == setup_id))
        await self.session.flush()
