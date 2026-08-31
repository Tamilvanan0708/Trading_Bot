"""
Base Strategy Interface & Candidate Models.
"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.core.constants import SignalDirection, StrategyType
from app.data.models import MultiTimeframeSnapshot


class StrategyCandidate(BaseModel):
    """Signal candidate emitted by an individual strategy engine."""
    strategy_type: StrategyType
    direction: SignalDirection
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk_reward: float
    confidence: float
    reasons: list[str]
    invalidation_level: float
    metadata: dict[str, Any] = {}


class BaseStrategy(ABC):
    """Abstract base class for trading strategies."""

    @abstractmethod
    def evaluate(self, snapshot: MultiTimeframeSnapshot) -> StrategyCandidate | None:
        """Evaluates multi-timeframe snapshot and emits strategy candidate if conditions are met."""
