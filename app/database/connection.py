"""
Database Connection & Session Management.
"""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import get_settings
from app.core.logging import logger
from app.database.models import Base

settings = get_settings()

# Ensure directory for SQLite exists if sqlite is used
if "sqlite" in settings.DATABASE_URL:
    db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

# SQLite: use WAL journal mode + busy timeout to reduce lock contention and
# allow safe concurrent readers while a single worker writes.
sqlite_pragmas = (
    "PRAGMA journal_mode=WAL;"
    "PRAGMA busy_timeout=5000;"
    "PRAGMA synchronous=NORMAL;"
    "PRAGMA foreign_keys=ON;"
) if "sqlite" in settings.DATABASE_URL else ""

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+asyncpg://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

connect_args = {}
if "sqlite" in db_url:
    connect_args = {"timeout": 20}
elif "postgresql" in db_url:
    connect_args = {"statement_cache_size": 0}

engine: AsyncEngine = create_async_engine(
    db_url,
    echo=False,
    future=True,
    connect_args=connect_args,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Initializes the database schema and applies any missing column migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Apply SQLite WAL + busy timeout pragmas (safe on every startup)
        if "sqlite" in settings.DATABASE_URL:
            from sqlalchemy import text
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=5000"))
            await conn.execute(text("PRAGMA foreign_keys=ON"))
    # Apply column-level migrations for columns added after the initial schema
    # (SQLite's create_all does NOT add columns to existing tables).
    await _migrate_columns()
    logger.info("Database schema initialized successfully.")


_MIGRATIONS = [
    ("paper_trades", "opened_at", "DATETIME"),
    ("paper_trades", "exit_reason", "VARCHAR(100)"),
    ("signals", "strategy_version", "VARCHAR(64)"),
    ("signals", "outcome", "VARCHAR(20)"),
    ("signals", "max_favorable_excursion_r", "FLOAT"),
    ("signals", "max_adverse_excursion_r", "FLOAT"),
    ("signals", "tp1_hit", "BOOLEAN"),
    ("signals", "tp2_hit", "BOOLEAN"),
    ("signals", "tp3_hit", "BOOLEAN"),
    ("signals", "sl_hit", "BOOLEAN"),
    ("signals", "time_to_outcome_hours", "FLOAT"),
    ("signals", "max_r_achieved", "FLOAT"),
    ("signals", "final_r", "FLOAT"),
    ("signals", "regime", "VARCHAR(20)"),
    ("signals", "session", "VARCHAR(20)"),
    ("signals", "outcome_updated_at", "DATETIME"),
    # AI provider fields (added for multi-provider validation)
    ("ai_validations", "provider", "VARCHAR(40)"),
    ("ai_validations", "model", "VARCHAR(80)"),
    ("ai_validations", "reason_code", "VARCHAR(40)"),
    # Retracement BOS V1 tables (created via create_all, no migration needed)
    ("retracement_setups", "point_1_timestamp", "DATETIME"),
    ("retracement_setups", "point_1_price", "FLOAT"),
    ("retracement_setups", "fib_0_382", "FLOAT"),
    ("retracement_setups", "fib_0_500", "FLOAT"),
    ("retracement_setups", "validation_passed", "DATETIME"),
    ("retracement_setups", "insufficient_structure_reason", "TEXT"),
    ("retracement_setups", "tp_before_freeze", "FLOAT"),
    ("retracement_setups", "layers_json", "TEXT"),
    ("retracement_setups", "escape_armed", "VARCHAR(5)"),
    ("notification_logs", "signal_id", "VARCHAR(36)"),
]


async def _migrate_columns() -> None:
    """Add missing columns to existing tables (safe to run on every startup)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    for table, column, coltype in _MIGRATIONS:
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                )
            logger.info("Migration: added column %s.%s", table, column)
        except Exception as exc:
            # "duplicate column name" / "already exists" is expected when the column already exists
            err_msg = str(exc).lower()
            if "duplicate" in err_msg or "already exists" in err_msg:
                pass
            else:
                logger.debug("Migration notice for %s.%s: %s", table, column, exc)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency injection helper for database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
