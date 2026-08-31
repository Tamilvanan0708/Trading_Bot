"""
RETRACEMENT_BOS_V1 — Exact Bullish BOS Retracement Strategy.

Exact Fibonacci level mapping (NEVER swapped):
    1.618 -> BLACK LINE 1 (extension reference)
    1.000 -> TP  (dynamic before entry, frozen at entry touch)
    0.618 -> ENTRY
    0.236 -> SL
    0.000 -> BLACK LINE 2 (Point 2 anchor)
"""

from app.retracement.engine import RetracementBOSEngine
from app.retracement.models import (
    STRATEGY_VERSION,
    RetracementEvent,
    RetracementEventType,
    RetracementSetup,
    RetracementState,
    to_spec_state,
)

__all__ = [
    "STRATEGY_VERSION",
    "RetracementBOSEngine",
    "RetracementSetup",
    "RetracementEvent",
    "RetracementEventType",
    "RetracementState",
    "to_spec_state",
]
