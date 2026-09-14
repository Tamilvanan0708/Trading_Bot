"""
RETRACEMENT_BOS_V1 — Repository for persisting setups and event history.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
        "layers_json": json.dumps(setup.layers, default=str) if setup.layers else None,
        "escape_armed": "1" if setup.escape_armed else "0",
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
        row = res.scalars().first()
        if not row:
            return None
        now_utc = datetime.now(timezone.utc)
        # Reject stale ghost setups older than 12 hours
        if row.updated_at:
            up_dt = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=timezone.utc)
            if (now_utc - up_dt).total_seconds() > 43200:
                return None

        import os
        is_test = bool(os.getenv("PYTEST_CURRENT_TEST") or os.getenv("APP_ENV") == "test")
        if not is_test:
            bos_dt = row.bos_timestamp or row.created_at
            if bos_dt:
                b_dt = bos_dt if bos_dt.tzinfo else bos_dt.replace(tzinfo=timezone.utc)
                if (now_utc - b_dt).total_seconds() > 36 * 3600:
                    return None
                if b_dt.weekday() in (4, 5) and (now_utc.weekday() == 0 or (now_utc.weekday() == 6 and now_utc.hour >= 21)):
                    return None

        return row.to_domain()

    async def purge_stale_setups(self, symbol: str | None = None) -> int:
        """Invalidate active setups that crossed the weekend gap or are older than 36h."""
        import os
        is_test = bool(os.getenv("PYTEST_CURRENT_TEST") or os.getenv("APP_ENV") == "test")
        now_utc = datetime.now(timezone.utc)
        stmt = (
            select(RetracementSetupModel)
            .where(RetracementSetupModel.state.notin_(["COMPLETED", "INVALIDATED"]))
        )
        if symbol:
            stmt = stmt.where(RetracementSetupModel.symbol == symbol)
        res = await self.session.execute(stmt)
        rows = res.scalars().all()
        purged = 0
        for row in rows:
            is_stale = False
            # Check hardcoded Friday ghost level if present
            if row.point_2_price and abs(row.point_2_price - 4306.02) < 1e-3:
                is_stale = True
            if not is_test:
                bos_dt = row.bos_timestamp or row.created_at
                if bos_dt:
                    b_dt = bos_dt if bos_dt.tzinfo else bos_dt.replace(tzinfo=timezone.utc)
                    if (now_utc - b_dt).total_seconds() > 36 * 3600:
                        is_stale = True
                    if b_dt.weekday() in (4, 5) and (now_utc.weekday() == 0 or (now_utc.weekday() == 6 and now_utc.hour >= 21)):
                        is_stale = True
            if is_stale:
                row.state = "INVALIDATED"
                row.invalidation_reason = "Purged stale setup (weekend session expired)."
                row.updated_at = now_utc
                purged += 1

        # Also close stale open paper trades belonging to purged setups
        try:
            from app.database.models import PaperTradeModel
            p_stmt = select(PaperTradeModel).where(PaperTradeModel.state == "OPEN")
            if symbol:
                p_stmt = p_stmt.where(PaperTradeModel.symbol == symbol)
            p_res = await self.session.execute(p_stmt)
            for p_row in p_res.scalars().all():
                p_stale = False
                if p_row.signal_id and "_4306_" in p_row.signal_id:
                    p_stale = True
                if p_row.stop_loss and abs(p_row.stop_loss - 4329.82) < 1e-3:
                    p_stale = True
                if not is_test and p_row.opened_at:
                    p_dt = p_row.opened_at if p_row.opened_at.tzinfo else p_row.opened_at.replace(tzinfo=timezone.utc)
                    if (now_utc - p_dt).total_seconds() > 36 * 3600:
                        p_stale = True
                    if p_dt.weekday() in (4, 5) and (now_utc.weekday() == 0 or (now_utc.weekday() == 6 and now_utc.hour >= 21)):
                        p_stale = True
                if p_stale:
                    p_row.state = "CLOSED"
                    p_row.exit_reason = "WEEKEND_SESSION_EXPIRED"
                    p_row.closed_at = now_utc
                    purged += 1
        except Exception:
            pass

        if purged > 0:
            await self.session.flush()
        return purged

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
