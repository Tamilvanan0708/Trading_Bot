"""
Signal Engine Pydantic Models.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.core.constants import MarketBias, SignalDirection, SignalQuality, StrategyType


class SignalPayload(BaseModel):
    """Centralized Standard Signal Object."""
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    instrument: str = "XAUUSD"
    direction: SignalDirection
    strategy: StrategyType
    timeframe: str = "15m"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward: float
    confidence_score: float
    signal_quality: SignalQuality
    market_bias: MarketBias
    strategy_version: str = ""
    reasons: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    detected_structures: dict[str, Any] = Field(default_factory=dict)
    fibonacci_levels: dict[str, float] = Field(default_factory=dict)
    liquidity_levels: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str = ""

    def build_explanation(self) -> str:
        """Generates the human-readable trade explanation."""
        lines = [
            f"{self.direction.value} {self.instrument}",
            f"Confluence: {self.confidence_score:.0f}/100",
            f"Strategy: {self.strategy.value}",
            f"Entry: {self.entry:.2f} | SL: {self.stop_loss:.2f} | "
            f"TP1: {self.take_profit_1:.2f} | TP2: {self.take_profit_2:.2f} | TP3: {self.take_profit_3:.2f}",
            f"R:R: 1:{self.risk_reward:.1f}",
            f"AI: {self.signal_quality.value}",
        ]
        if self.reasons:
            lines.append("Reasons: " + " | ".join(self.reasons[:5]))
        return "\n".join(lines)

    @property
    def is_tradable(self) -> bool:
        """A signal is tradable only when it passes the STRONG threshold.

        Thresholds are configurable via settings; the SignalQuality category
        is the single source of truth (STRONG/VERY_STRONG = tradable).
        """
        return (
            self.direction != SignalDirection.NO_TRADE
            and self.signal_quality in (SignalQuality.STRONG, SignalQuality.VERY_STRONG)
        )
