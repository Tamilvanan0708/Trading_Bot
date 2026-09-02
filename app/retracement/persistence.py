"""
RETRACEMENT_BOS_V1 — SQLAlchemy persistence for setups and event history.

Provides crash-safe storage so a setup (including the frozen TP) survives an
application restart.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, String, Text

from app.database.models import Base, get_utc_now
from app.retracement.models import (
    RetracementEvent,
    RetracementEventType,
    RetracementSetup,
    RetracementState,
    STRATEGY_VERSION,
)


class RetracementSetupModel(Base):
    __tablename__ = "retracement_setups"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    setup_id = Column(String(36), nullable=False, unique=True, index=True)
    strategy = Column(String(64), nullable=False, default=STRATEGY_VERSION)
    symbol = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False, default="LONG")
    timeframe = Column(String(10), nullable=False, default="15m")
    state = Column(String(30), nullable=False, default=RetracementState.NO_SETUP.value)

    point_1_timestamp = Column(DateTime, nullable=True)
    point_1_price = Column(Float, nullable=True)

    bos_timestamp = Column(DateTime, nullable=True)
    bos_price = Column(Float, nullable=True)

    point_2_timestamp = Column(DateTime, nullable=True)
    point_2_price = Column(Float, nullable=True)

    current_high_timestamp = Column(DateTime, nullable=True)
    current_high_price = Column(Float, nullable=True)

    fib_0 = Column(Float, nullable=True)
    fib_0_236 = Column(Float, nullable=True)
    fib_0_382 = Column(Float, nullable=True)
    fib_0_500 = Column(Float, nullable=True)
    fib_0_618 = Column(Float, nullable=True)
    fib_1_000 = Column(Float, nullable=True)
    fib_1_618 = Column(Float, nullable=True)

    entry_price = Column(Float, nullable=True)
    sl_price = Column(Float, nullable=True)

    dynamic_tp = Column(Float, nullable=True)
    locked_tp = Column(Float, nullable=True)
    tp_before_freeze = Column(Float, nullable=True)
    tp_locked = Column(DateTime, nullable=True)  # timestamp of TP freeze
    entry_touched = Column(DateTime, nullable=True)  # timestamp of entry touch
    entry_timestamp = Column(DateTime, nullable=True)

    validation_passed = Column(DateTime, nullable=True)  # set when setup validated
    insufficient_structure_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, default=get_utc_now, nullable=False)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False)
    invalidation_reason = Column(Text, nullable=True)
    completion_reason = Column(Text, nullable=True)
    strategy_version = Column(String(64), nullable=False, default=STRATEGY_VERSION)

    outcome = Column(String(20), nullable=True)
    max_r = Column(Float, nullable=True)
    mae_r = Column(Float, nullable=True)
    mfe_r = Column(Float, nullable=True)

    layers_json = Column(Text, nullable=True)  # 3/2-tranche layer state (JSON)
    escape_armed = Column(String(5), nullable=True)  # "1"/"0"

    # JSON column-free: event history stored separately
    def to_domain(self) -> RetracementSetup:
        return RetracementSetup(
            setup_id=self.setup_id,
            strategy=self.strategy,
            symbol=self.symbol,
            direction=self.direction,
            timeframe=self.timeframe,
            state=RetracementState(self.state) if self.state in RetracementState._value2member_map_ else RetracementState.NO_SETUP,
            point_1_timestamp=self.point_1_timestamp,
            point_1_price=self.point_1_price,
            bos_timestamp=self.bos_timestamp,
            bos_price=self.bos_price,
            point_2_timestamp=self.point_2_timestamp,
            point_2_price=self.point_2_price,
            current_high_timestamp=self.current_high_timestamp,
            current_high_price=self.current_high_price,
            fib_0=self.fib_0,
            fib_0_236=self.fib_0_236,
            fib_0_382=self.fib_0_382,
            fib_0_500=self.fib_0_500,
            fib_0_618=self.fib_0_618,
            fib_1_000=self.fib_1_000,
            fib_1_618=self.fib_1_618,
            entry_price=self.entry_price,
            sl_price=self.sl_price,
            dynamic_tp=self.dynamic_tp,
            locked_tp=self.locked_tp,
            tp_before_freeze=self.tp_before_freeze,
            tp_locked=self.tp_locked is not None,
            entry_touched=self.entry_touched is not None,
            entry_timestamp=self.entry_timestamp,
            validation_passed=self.validation_passed is not None,
            insufficient_structure_reason=self.insufficient_structure_reason or "",
            created_at=self.created_at,
            updated_at=self.updated_at,
            invalidation_reason=self.invalidation_reason or "",
            completion_reason=self.completion_reason or "",
            strategy_version=self.strategy_version,
            outcome=self.outcome,
            max_r=self.max_r,
            mae_r=self.mae_r,
            mfe_r=self.mfe_r,
        )


class RetracementEventModel(Base):
    __tablename__ = "retracement_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    setup_id = Column(String(36), nullable=False, index=True)
    event_id = Column(String(36), nullable=False, unique=True, index=True)
    event_type = Column(String(30), nullable=False)
    timestamp = Column(DateTime, default=get_utc_now, nullable=False)
    price = Column(Float, nullable=True)
    state_before = Column(String(30), nullable=True)
    state_after = Column(String(30), nullable=True)
    metadata_json = Column(Text, nullable=True)  # JSON-encoded metadata

    def to_domain(self) -> RetracementEvent:
        import json
        meta: dict[str, Any] = {}
        if self.metadata_json:
            try:
                meta = json.loads(self.metadata_json)
            except Exception:
                meta = {}
        return RetracementEvent(
            event_id=self.event_id,
            setup_id=self.setup_id,
            event_type=RetracementEventType(self.event_type),
            timestamp=self.timestamp,
            price=self.price,
            state_before=RetracementState(self.state_before) if self.state_before else RetracementState.NO_SETUP,
            state_after=RetracementState(self.state_after) if self.state_after else RetracementState.NO_SETUP,
            metadata=meta,
        )


def _encode_event(ev: RetracementEvent) -> dict[str, Any]:
    import json

    return {
        "setup_id": ev.setup_id,
        "event_id": ev.event_id,
        "event_type": ev.event_type.value,
        "timestamp": ev.timestamp,
        "price": ev.price,
        "state_before": ev.state_before.value,
        "state_after": ev.state_after.value,
        "metadata_json": json.dumps(ev.metadata, default=str),
    }
