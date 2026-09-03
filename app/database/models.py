"""
SQLAlchemy Database Models.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def generate_uuid() -> str:
    return str(uuid.uuid4())


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SignalModel(Base):
    __tablename__ = "signals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategy = Column(String(50), nullable=False)
    strategy_version = Column(String(64), nullable=True, index=True)
    direction = Column(String(10), nullable=False)
    timeframe = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit_1 = Column(Float, nullable=False)
    take_profit_2 = Column(Float, nullable=False)
    take_profit_3 = Column(Float, nullable=False)
    risk_reward = Column(Float, nullable=False)
    confidence_score = Column(Float, nullable=False)
    signal_quality = Column(String(20), nullable=False)
    market_bias = Column(String(20), nullable=False)
    reasons = Column(JSON, nullable=False, default=list)
    invalidation_conditions = Column(JSON, nullable=False, default=list)
    metadata_payload = Column(JSON, nullable=True, default=dict)

    # Forward-outcome tracking (filled after the signal is observed)
    outcome = Column(String(20), nullable=True, index=True)          # OPEN | TP1 | TP2 | TP3 | SL | EXPIRED
    max_favorable_excursion_r = Column(Float, nullable=True)
    max_adverse_excursion_r = Column(Float, nullable=True)
    tp1_hit = Column(Boolean, nullable=True)
    tp2_hit = Column(Boolean, nullable=True)
    tp3_hit = Column(Boolean, nullable=True)
    sl_hit = Column(Boolean, nullable=True)
    time_to_outcome_hours = Column(Float, nullable=True)
    max_r_achieved = Column(Float, nullable=True)
    final_r = Column(Float, nullable=True)
    regime = Column(String(20), nullable=True)
    session = Column(String(20), nullable=True)
    outcome_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    ai_validation = relationship("AIValidationModel", back_populates="signal", uselist=False, cascade="all, delete-orphan")
    paper_trades = relationship("PaperTradeModel", back_populates="signal", cascade="all, delete-orphan")


class AIValidationModel(Base):
    __tablename__ = "ai_validations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    signal_id = Column(String(36), ForeignKey("signals.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    status = Column(String(20), nullable=False)  # APPROVE, REJECT, CAUTION, UNAVAILABLE
    confidence = Column(Float, nullable=False)
    explanation = Column(Text, nullable=False)
    identified_risks = Column(JSON, nullable=False, default=list)
    missing_confirmations = Column(JSON, nullable=False, default=list)
    raw_response = Column(Text, nullable=True)
    provider = Column(String(40), nullable=True)
    model = Column(String(80), nullable=True)
    reason_code = Column(String(40), nullable=True)

    signal = relationship("SignalModel", back_populates="ai_validation")


class PaperTradeModel(Base):
    __tablename__ = "paper_trades"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    signal_id = Column(String(36), ForeignKey("signals.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, index=True)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)
    state = Column(String(30), nullable=False, default="SIGNAL_GENERATED", index=True)
    lot_size = Column(Float, nullable=False)
    risk_amount = Column(Float, nullable=False)
    target_entry = Column(Float, nullable=False)
    actual_entry = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=False)
    take_profit_1 = Column(Float, nullable=False)
    take_profit_2 = Column(Float, nullable=False)
    take_profit_3 = Column(Float, nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    exit_price = Column(Float, nullable=True)
    exit_reason = Column(String(100), nullable=True)
    realized_pnl = Column(Float, nullable=True)
    realized_r = Column(Float, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    state_logs = Column(JSON, nullable=False, default=list)

    signal = relationship("SignalModel", back_populates="paper_trades")


class BacktestRunModel(Base):
    __tablename__ = "backtest_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    symbol = Column(String(20), nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    initial_balance = Column(Float, nullable=False)
    final_balance = Column(Float, nullable=False)
    total_trades = Column(Integer, nullable=False)
    winning_trades = Column(Integer, nullable=False)
    losing_trades = Column(Integer, nullable=False)
    win_rate = Column(Float, nullable=False)
    profit_factor = Column(Float, nullable=False)
    max_drawdown = Column(Float, nullable=False)
    net_profit = Column(Float, nullable=False)
    expectancy = Column(Float, nullable=False)
    strategy_metrics = Column(JSON, nullable=False, default=dict)
    timeframe_metrics = Column(JSON, nullable=False, default=dict)
    trades_log = Column(JSON, nullable=False, default=list)


class NotificationLogModel(Base):
    __tablename__ = "notification_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    channel = Column(String(20), nullable=False)  # TELEGRAM, DISCORD, WEBHOOK
    recipient = Column(String(100), nullable=False)
    message_content = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)  # SENT, FAILED, SKIPPED
    error_message = Column(Text, nullable=True)
    signal_id = Column(String(36), nullable=True, index=True)


class SystemStateModel(Base):
    """Key/value store for restart-safe runtime state (limits, dedup, last processed)."""
    __tablename__ = "system_state"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now)
