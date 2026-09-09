"""
RETRACEMENT_BOS_V1 — Exact Bullish BOS Retracement Strategy Models.

Exact Fibonacci level semantics (NEVER swapped):
    1.618 -> BLACK LINE 1  (extension/reference, NOT normal TP)
    1.000 -> TP            (dynamic before entry, frozen after entry touch)
    0.618 -> ENTRY         (entry touch event)
    0.500 -> FIB           (normal level)
    0.382 -> FIB           (normal level)
    0.236 -> SL            (stop loss)
    0.000 -> BLACK LINE 2  (Point 2 anchor, NOT SL)

For a bullish setup the Fibonacci high endpoint is the current VALID HIGH;
the 0.000 anchor is Point 2 (the low of the BOS move).  Point 1 is the
previous important swing high that price breaks to confirm the bullish BOS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

STRATEGY_VERSION = "RETRACEMENT_BOS_V1"

# Fibonacci ratios used by this exact structure (ascending order)
FIB_RATIOS = [0.000, 0.236, 0.382, 0.500, 0.618, 1.000, 1.618]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RetracementState(str, Enum):
    """Internal engine states (backward compatible).

    These map to the specification's state machine:
        WAITING_FOR_BOS   <- NO_SETUP
        BOS_CONFIRMED     <- BOS_DETECTED
        POINT_2_IDENTIFIED
        FIB_ACTIVE
        WAITING_FOR_ENTRY <- TP_DYNAMIC
        ENTRY_TOUCHED
        TP_FROZEN
        TRADE_ACTIVE      <- COMPLETED (active trade), outcome step
        INVALIDATED
    """
    NO_SETUP = "NO_SETUP"
    BOS_DETECTED = "BOS_DETECTED"
    POINT_2_IDENTIFIED = "POINT_2_IDENTIFIED"
    FIB_ACTIVE = "FIB_ACTIVE"
    TP_DYNAMIC = "TP_DYNAMIC"
    ENTRY_TOUCHED = "ENTRY_TOUCHED"
    TP_FROZEN = "TP_FROZEN"
    COMPLETED = "COMPLETED"
    INVALIDATED = "INVALIDATED"

    # Specification state names (aliases for display / API)
    WAITING_FOR_BOS = "WAITING_FOR_BOS"
    BOS_CONFIRMED = "BOS_CONFIRMED"
    WAITING_FOR_ENTRY = "WAITING_FOR_ENTRY"
    TRADE_ACTIVE = "TRADE_ACTIVE"


SPEC_STATE_MAP: dict[str, str] = {
    "NO_SETUP": "WAITING_FOR_BOS",
    "BOS_DETECTED": "BOS_CONFIRMED",
    "POINT_2_IDENTIFIED": "POINT_2_IDENTIFIED",
    "FIB_ACTIVE": "FIB_ACTIVE",
    "TP_DYNAMIC": "WAITING_FOR_ENTRY",
    "ENTRY_TOUCHED": "ENTRY_TOUCHED",
    "TP_FROZEN": "TP_FROZEN",
    "TRADE_ACTIVE": "TRADE_ACTIVE",
    "COMPLETED": "COMPLETED",
    "INVALIDATED": "INVALIDATED",
}


def to_spec_state(state: RetracementState | str) -> str:
    """Map an internal engine state to the specification's state name."""
    key = state.value if isinstance(state, RetracementState) else str(state)
    return SPEC_STATE_MAP.get(key, key)


class RetracementEventType(str, Enum):
    BOS_DETECTED = "BOS_DETECTED"
    POINT_2_FOUND = "POINT_2_FOUND"
    NEW_VALID_HIGH = "NEW_VALID_HIGH"
    TP_UPDATED = "TP_UPDATED"
    ENTRY_TOUCHED = "ENTRY_TOUCHED"
    TP_LOCKED = "TP_LOCKED"
    POST_ENTRY_HIGH_IGNORED = "POST_ENTRY_HIGH_IGNORED"
    SL_HIT = "SL_HIT"
    TP_HIT = "TP_HIT"
    INVALIDATED = "INVALIDATED"
    COMPLETED = "COMPLETED"
    SETUP_CREATED = "SETUP_CREATED"
    INSUFFICIENT_STRUCTURE = "INSUFFICIENT_STRUCTURE"
    LEVEL_ORDER_INVALID = "LEVEL_ORDER_INVALID"


class RetracementLevel(BaseModel):
    """A single Fibonacci level with its fixed semantic meaning."""
    ratio: float
    price: float
    label: str          # e.g. "ENTRY", "SL", "TP", "BLACK_LINE_1", "BLACK_LINE_2", "FIB"


class RetracementSetup(BaseModel):
    """Persistent, versioned retracement setup (in-memory + DB representation)."""
    setup_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy: str = STRATEGY_VERSION
    symbol: str = "XAUUSD"
    direction: str = "LONG"          # this module implements the bullish setup only
    timeframe: str = "15m"

    state: RetracementState = RetracementState.NO_SETUP

    # Point 1 = previous important swing high that was broken by the BOS
    point_1_timestamp: datetime | None = None
    point_1_price: float | None = None

    bos_timestamp: datetime | None = None
    bos_price: float | None = None

    point_2_timestamp: datetime | None = None
    point_2_price: float | None = None

    current_high_timestamp: datetime | None = None
    current_high_price: float | None = None

    fib_0: float | None = None
    fib_0_236: float | None = None
    fib_0_382: float | None = None
    fib_0_500: float | None = None
    fib_0_618: float | None = None
    fib_1_000: float | None = None
    fib_1_618: float | None = None

    entry_price: float | None = None
    sl_price: float | None = None

    dynamic_tp: float | None = None
    locked_tp: float | None = None
    tp_before_freeze: float | None = None
    tp_locked: bool = False
    entry_touched: bool = False
    entry_timestamp: datetime | None = None

    # 3-Tranche Scaling System (Fib With Retracement) + 2-Tranche (SMC With Fib)
    # Each layer: {"layer","entry_ratio","entry_price","tp","sl","lots",
    #              "state": PENDING|FILLED|TP_HIT|SL_HIT|ESCAPE_CLOSED,
    #              "filled_at"}
    layers: dict[str, dict] = Field(default_factory=dict)
    escape_armed: bool = False

    # Validation / structure adequacy
    validation_passed: bool = False
    insufficient_structure_reason: str = ""

    # Multimodal Vision AI Verification
    ai_status: str | None = "EVALUATING"
    ai_confidence: float | None = 88.0
    ai_reason: str = "Monitoring 0.618 Retracement"
    ai_timestamp: datetime | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    invalidation_reason: str = ""
    completion_reason: str = ""
    strategy_version: str = STRATEGY_VERSION

    # Outcome (filled by the backtest / forward observation)
    outcome: str | None = None
    max_r: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None

    def spec_state(self) -> str:
        return to_spec_state(self.state)

    def level_dict(self) -> dict[str, RetracementLevel]:
        """Return the exact levels keyed by ratio string (7 levels)."""
        return {
            "1.618": RetracementLevel(ratio=1.618, price=self.fib_1_618, label="BLACK_LINE_1") if self.fib_1_618 is not None else None,
            "1.000": RetracementLevel(ratio=1.000, price=self.fib_1_000, label="TP") if self.fib_1_000 is not None else None,
            "0.618": RetracementLevel(ratio=0.618, price=self.fib_0_618, label="ENTRY") if self.fib_0_618 is not None else None,
            "0.500": RetracementLevel(ratio=0.500, price=self.fib_0_500, label="FIB") if self.fib_0_500 is not None else None,
            "0.382": RetracementLevel(ratio=0.382, price=self.fib_0_382, label="FIB") if self.fib_0_382 is not None else None,
            "0.236": RetracementLevel(ratio=0.236, price=self.fib_0_236, label="SL") if self.fib_0_236 is not None else None,
            "0.000": RetracementLevel(ratio=0.000, price=self.fib_0, label="BLACK_LINE_2") if self.fib_0 is not None else None,
        }

    def level_order_valid(self) -> bool:
        """Verify 0.000 < 0.236 < 0.382 < 0.500 < 0.618 < 1.000 < 1.618."""
        prices = [self.fib_0, self.fib_0_236, self.fib_0_382, self.fib_0_500,
                  self.fib_0_618, self.fib_1_000, self.fib_1_618]
        if any(p is None for p in prices):
            return False
        for a, b in zip(prices, prices[1:]):
            if not (a < b):
                return False
        return True

    def touch_entry(self, candle_low: float, candle_high: float, tolerance: float = 0.0) -> bool:
        """Bullish ENTRY touch rule (configurable).

        Default: the candle RANGE intersects the 0.618 level
        (``candle_low <= 0.618 <= candle_high``).  A non-zero ``tolerance``
        widens the touch band symmetrically:
        ``candle_low - tolerance <= 0.618 <= candle_high + tolerance``.

        A near-miss (price only *close* to the level) never triggers Entry
        when ``tolerance == 0``.
        """
        if self.fib_0_618 is None:
            return False
        return (candle_low - tolerance) <= self.fib_0_618 <= (candle_high + tolerance)

    # ------------------------------------------------------------------
    # Point analysis — POINTS are the primary focus (never raw prices)
    # ------------------------------------------------------------------

    @property
    def total_point_range(self) -> float | None:
        """Total point range of the setup (1.000 TP minus 0.000 / Point 2)."""
        if self.fib_0 is None or self.fib_1_000 is None:
            return None
        return self.fib_1_000 - self.fib_0

    @property
    def entry_to_tp_points(self) -> float | None:
        """Points from ENTRY (0.618) to the effective TP (frozen if locked)."""
        tp = self.locked_tp if self.tp_locked and self.locked_tp is not None else self.dynamic_tp
        if tp is None or self.entry_price is None:
            return None
        return tp - self.entry_price

    @property
    def entry_to_sl_points(self) -> float | None:
        """Points from ENTRY (0.618) down to SL (0.236)."""
        if self.entry_price is None or self.sl_price is None:
            return None
        return self.entry_price - self.sl_price

    @property
    def entry_status(self) -> str:
        """WAITING / TOUCHED / LOCKED."""
        if self.tp_locked:
            return "LOCKED"
        if self.entry_touched:
            return "TOUCHED"
        if self.entry_price is not None:
            return "WAITING"
        return "NONE"

    def point_analysis(self) -> dict[str, float | str | None]:
        """POINTS-FIRST summary for the user-facing report."""
        return {
            "total_point_range": self.total_point_range,
            "entry_to_tp_points": self.entry_to_tp_points,
            "entry_to_sl_points": self.entry_to_sl_points,
            "entry_status": self.entry_status,
        }

    def report(self) -> dict[str, Any]:
        """Structured RETRACEMENT SETUP DETECTED report (spec section 27)."""
        levels = self.level_dict()
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "setup_id": self.setup_id,
            "direction": self.direction,
            "status": "RETRACEMENT SETUP DETECTED",
            "bos": {
                "confirmed": self.bos_price is not None,
                "point_1": self.point_1_price,
                "bos_price": self.bos_price,
                "bos_timestamp": self.bos_timestamp.isoformat() if self.bos_timestamp else None,
            },
            "point_2": {
                "identified": self.point_2_price is not None,
                "price": self.point_2_price,
                "timestamp": self.point_2_timestamp.isoformat() if self.point_2_timestamp else None,
            },
            "fib_structure": {
                ratio: (lvl.price if lvl else None)
                for ratio, lvl in sorted(levels.items(), key=lambda kv: -float(kv[0]))
            },
            "state": self.state.value,
            "spec_state": self.spec_state(),
            "tp": {
                "mode": "LOCKED" if self.tp_locked else "DYNAMIC — WAITING FOR ENTRY",
                "dynamic": self.dynamic_tp,
                "locked": self.locked_tp,
                "locked_at_freeze": self.tp_before_freeze,
            },
            "entry": {
                "status": self.entry_status,
                "price": self.entry_price,
                "touched_at": self.entry_timestamp.isoformat() if self.entry_timestamp else None,
            },
            "sl": {"price": self.sl_price},
            "point_analysis": self.point_analysis(),
            "validation": {
                "passed": self.validation_passed,
                "insufficient_structure_reason": self.insufficient_structure_reason or None,
            },
        }


class RetracementEvent(BaseModel):
    """Auditable event history entry for a setup."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    setup_id: str
    event_type: RetracementEventType
    timestamp: datetime = Field(default_factory=utcnow)
    price: float | None = None
    state_before: RetracementState
    state_after: RetracementState
    metadata: dict[str, Any] = Field(default_factory=dict)
