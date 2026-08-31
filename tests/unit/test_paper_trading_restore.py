"""
Unit tests for paper trading service restore_from_db and position_id mapping.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import Settings
from app.core.constants import SignalDirection, TradeState
from app.database.models import Base
from app.database.repository import Repository
from app.paper_trading.service import PaperTradingService, paper_position_to_dict


@pytest.mark.asyncio
async def test_restore_from_db_position_id_mapping(tmp_path):
    """Regression: restore_from_db must use position_id, not id."""
    from app.paper_trading.state_machine import PaperPosition

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/restore.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with maker() as session:
        repo = Repository(session)
        pos = PaperPosition(
            position_id="test-pos-1",
            signal_id="sig-1",
            symbol="XAUUSD",
            direction=SignalDirection.LONG,
            lot_size=0.1,
            risk_amount_usd=100.0,
            target_entry=2650.0,
            stop_loss=2640.0,
            take_profit_1=2665.0,
            take_profit_2=2680.0,
            take_profit_3=2700.0,
            state=TradeState.CLOSED,
            closed_at=datetime.now(timezone.utc),
            realized_pnl_usd=200.0,
            realized_r=2.0,
            exit_reason="TP2_HIT",
        )
        await repo.create_paper_trade(paper_position_to_dict(pos))
        await session.commit()

    settings = Settings()
    restored = PaperTradingService(initial_balance=10000.0, settings=settings)
    async with maker() as session2:
        await restored.restore_from_db(Repository(session2))

    assert len(restored.get_all_positions()) == 1
    restored_pos = restored.get_position("test-pos-1")
    assert restored_pos is not None
    assert restored_pos.position_id == "test-pos-1"
    assert restored_pos.realized_pnl_usd == 200.0
    assert restored_pos.exit_reason == "TP2_HIT"
    assert restored.balance == 10200.0  # 10000 + 200

    await engine.dispose()