"""
SMC_WITH_FIB — Exact Smart Money Concepts + Fibonacci Strategy Engine.

State Machine (Same as Retracement BOS):
  NO_SETUP -> BOS_DETECTED -> POINT_2_IDENTIFIED -> FIB_ACTIVE -> TP_DYNAMIC
  -> ENTRY_TOUCHED -> TP_FROZEN -> TRADE_ACTIVE -> COMPLETED / INVALIDATED

Exact User Fibonacci Semantics (Dual-Direction Support):
  BULLISH PULLBACK (LONG):
    1.618 -> Extension Reference
    1.000 -> Swing Anchor Low
    0.920 -> Stop Loss (SL)
    0.790 -> Golden Pocket Deep
    0.680 -> Entry Price (Golden Pocket)
    0.500 -> 50% Equilibrium (Premium/Discount Zone Separator)
    0.382 -> Retracement Level
    0.000 -> Target Take Profit (TP - Previous Swing High Liquidity)

  BEARISH PULLBACK (SHORT):
    1.618 -> Extension Reference
    1.000 -> Swing Anchor High
    0.920 -> Stop Loss (SL)
    0.790 -> Golden Pocket Deep
    0.680 -> Entry Price (Golden Pocket)
    0.500 -> 50% Equilibrium
    0.382 -> Retracement Level
    0.000 -> Target Take Profit (TP - Previous Swing Low Liquidity)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.constants import SignalDirection
from app.core.logging import logger
from app.data.models import Candle
from app.indicators.swings import SwingPoint, detect_swings
from app.retracement.models import (
    RetracementEvent,
    RetracementEventType,
    RetracementLevel,
    RetracementState,
    utcnow,
)


class SMCFibEngine:
    """Exact deterministic SMC With Fib Engine."""

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m", left_bars: int = 3, right_bars: int = 3):
        self.symbol = symbol
        self.timeframe = timeframe
        self.left_bars = left_bars
        self.right_bars = right_bars
        self.state = RetracementState.NO_SETUP
        self.direction = SignalDirection.LONG

        # Setup fields
        self.setup_id: str | None = None
        self.point_1_price: float | None = None  # BOS level
        self.point_1_ts: datetime | None = None
        self.point_2_price: float | None = None  # Anchor swing point
        self.point_2_ts: datetime | None = None
        self.target_tp_price: float | None = None  # Dynamic TP / 0.000 level
        self.target_tp_ts: datetime | None = None

        self.entry_price: float | None = None  # 0.680 Fib
        self.pocket_price: float | None = None  # 0.790 Fib
        self.sl_price: float | None = None  # 0.920 Fib
        self.equilibrium_50: float | None = None  # 0.500 Fib

        self.entry_touched: bool = False
        self.entry_timestamp: datetime | None = None
        self.tp_locked: bool = False
        self.locked_tp: float | None = None
        self.outcome: str | None = None
        self.completion_reason: str | None = None
        self.invalidation_reason: str | None = None

        self.candles_since_point_2: int = 0
        self.max_expiry_candles: int = 45

        self.levels: dict[str, RetracementLevel] = {}
        self._events: list[RetracementEvent] = []
        self._history: list[Candle] = []

    def reset(self) -> None:
        """Reset state machine."""
        self.state = RetracementState.NO_SETUP
        self.setup_id = None
        self.point_1_price = None
        self.point_1_ts = None
        self.point_2_price = None
        self.point_2_ts = None
        self.target_tp_price = None
        self.target_tp_ts = None
        self.entry_price = None
        self.pocket_price = None
        self.sl_price = None
        self.equilibrium_50 = None
        self.entry_touched = False
        self.entry_timestamp = None
        self.tp_locked = False
        self.locked_tp = None
        self.outcome = None
        self.completion_reason = None
        self.invalidation_reason = None
        self.candles_since_point_2 = 0
        self.levels = {}
        self._events = []
        self._history = []

    def process_candle(self, candle: Candle) -> None:
        """Process a newly closed candle in strict chronological order."""
        self._history.append(candle)
        if len(self._history) < 30:
            return

        # Keep rolling window bounded
        if len(self._history) > 300:
            self._history = self._history[-300:]

        if self.state == RetracementState.NO_SETUP:
            self._detect_setup(candle)
        elif self.state in (RetracementState.BOS_DETECTED, RetracementState.POINT_2_IDENTIFIED, RetracementState.FIB_ACTIVE, RetracementState.TP_DYNAMIC):
            self._track_and_check_entry(candle)
        elif self.state in (RetracementState.ENTRY_TOUCHED, RetracementState.TP_FROZEN, RetracementState.TRADE_ACTIVE):
            self._track_active_trade(candle)

    def _detect_setup(self, candle: Candle) -> None:
        swings = detect_swings(self._history, left_bars=self.left_bars, right_bars=self.right_bars)
        confirmed_highs = [s for s in swings if s.type == "HIGH" and s.index + self.right_bars < len(self._history)]
        confirmed_lows = [s for s in swings if s.type == "LOW" and s.index + self.right_bars < len(self._history)]

        if not confirmed_highs or not confirmed_lows:
            return

        last_high = confirmed_highs[-1]
        last_low = confirmed_lows[-1]

        # Check for Bullish BOS (Price broke above previous confirmed swing high)
        if candle.close > last_high.price and last_high.index < len(self._history) - 1:
            self._initiate_setup(
                direction=SignalDirection.LONG,
                p1_price=last_high.price,
                p1_ts=last_high.timestamp,
                p2_price=last_low.price,
                p2_ts=last_low.timestamp,
                current_candle=candle,
            )
        # Check for Bearish BOS (Price broke below previous confirmed swing low)
        elif candle.close < last_low.price and last_low.index < len(self._history) - 1:
            self._initiate_setup(
                direction=SignalDirection.SHORT,
                p1_price=last_low.price,
                p1_ts=last_low.timestamp,
                p2_price=last_high.price,
                p2_ts=last_high.timestamp,
                current_candle=candle,
            )

    def _initiate_setup(self, direction: SignalDirection, p1_price: float, p1_ts: datetime, p2_price: float, p2_ts: datetime, current_candle: Candle) -> None:
        self.setup_id = str(uuid.uuid4())
        self.direction = direction
        self.point_1_price = p1_price
        self.point_1_ts = p1_ts
        self.point_2_price = p2_price
        self.point_2_ts = p2_ts
        self.candles_since_point_2 = 0
        self.entry_touched = False
        self.tp_locked = False
        self.outcome = None

        if direction == SignalDirection.LONG:
            self.target_tp_price = current_candle.high
            self.target_tp_ts = current_candle.timestamp
        else:
            self.target_tp_price = current_candle.low
            self.target_tp_ts = current_candle.timestamp

        self._recompute_fib_levels()
        self.state = RetracementState.TP_DYNAMIC

    def _recompute_fib_levels(self) -> None:
        if self.point_2_price is None or self.target_tp_price is None:
            return

        if self.direction == SignalDirection.LONG:
            lo = self.point_2_price
            hi = self.target_tp_price
            span = hi - lo if hi > lo else 1.0
            self.equilibrium_50 = round((hi + lo) / 2.0, 2)
            self.entry_price = round(hi - span * 0.68, 2)
            self.pocket_price = round(hi - span * 0.79, 2)
            self.sl_price = round(hi - span * 0.92, 2)

            self.levels = {
                "1.618": RetracementLevel(ratio=1.618, price=round(hi + span * 0.618, 2), label="EXTENSION"),
                "1.000": RetracementLevel(ratio=1.000, price=lo, label="SWING_ANCHOR"),
                "0.920": RetracementLevel(ratio=0.920, price=self.sl_price, label="STOP_LOSS"),
                "0.790": RetracementLevel(ratio=0.790, price=self.pocket_price, label="GOLDEN_POCKET"),
                "0.680": RetracementLevel(ratio=0.680, price=self.entry_price, label="ENTRY"),
                "0.500": RetracementLevel(ratio=0.500, price=self.equilibrium_50, label="EQUILIBRIUM"),
                "0.382": RetracementLevel(ratio=0.382, price=round(hi - span * 0.382, 2), label="FIB_LEVEL"),
                "0.000": RetracementLevel(ratio=0.000, price=hi, label="TARGET_TP"),
            }
        else:
            hi = self.point_2_price
            lo = self.target_tp_price
            span = hi - lo if hi > lo else 1.0
            self.equilibrium_50 = round((hi + lo) / 2.0, 2)
            self.entry_price = round(lo + span * 0.68, 2)
            self.pocket_price = round(lo + span * 0.79, 2)
            self.sl_price = round(lo + span * 0.92, 2)

            self.levels = {
                "1.618": RetracementLevel(ratio=1.618, price=round(lo - span * 0.618, 2), label="EXTENSION"),
                "1.000": RetracementLevel(ratio=1.000, price=hi, label="SWING_ANCHOR"),
                "0.920": RetracementLevel(ratio=0.920, price=self.sl_price, label="STOP_LOSS"),
                "0.790": RetracementLevel(ratio=0.790, price=self.pocket_price, label="GOLDEN_POCKET"),
                "0.680": RetracementLevel(ratio=0.680, price=self.entry_price, label="ENTRY"),
                "0.500": RetracementLevel(ratio=0.500, price=self.equilibrium_50, label="EQUILIBRIUM"),
                "0.382": RetracementLevel(ratio=0.382, price=round(lo + span * 0.382, 2), label="FIB_LEVEL"),
                "0.000": RetracementLevel(ratio=0.000, price=lo, label="TARGET_TP"),
            }

    def _track_and_check_entry(self, candle: Candle) -> None:
        self.candles_since_point_2 += 1
        if self.candles_since_point_2 > self.max_expiry_candles:
            self.state = RetracementState.INVALIDATED
            self.invalidation_reason = f"Setup expired after {self.max_expiry_candles} candles without entry touch."
            return

        # 1. Update dynamic target if new extremes are formed before entry
        if self.direction == SignalDirection.LONG:
            if candle.high > (self.target_tp_price or 0.0):
                self.target_tp_price = candle.high
                self.target_tp_ts = candle.timestamp
                self._recompute_fib_levels()
            # Check entry touch (0.68 touched)
            if self.entry_price is not None and candle.low <= self.entry_price:
                # Check for instant SL violation
                if self.sl_price is not None and candle.low <= self.sl_price:
                    self.state = RetracementState.INVALIDATED
                    self.invalidation_reason = "Price breached Stop Loss on entry candle."
                    return
                self.entry_touched = True
                self.entry_timestamp = candle.timestamp
                self.tp_locked = True
                self.locked_tp = self.target_tp_price
                self.state = RetracementState.TRADE_ACTIVE
        else:
            if candle.low < (self.target_tp_price or float("inf")):
                self.target_tp_price = candle.low
                self.target_tp_ts = candle.timestamp
                self._recompute_fib_levels()
            # Check entry touch
            if self.entry_price is not None and candle.high >= self.entry_price:
                if self.sl_price is not None and candle.high >= self.sl_price:
                    self.state = RetracementState.INVALIDATED
                    self.invalidation_reason = "Price breached Stop Loss on entry candle."
                    return
                self.entry_touched = True
                self.entry_timestamp = candle.timestamp
                self.tp_locked = True
                self.locked_tp = self.target_tp_price
                self.state = RetracementState.TRADE_ACTIVE

    def _track_active_trade(self, candle: Candle) -> None:
        if self.direction == SignalDirection.LONG:
            # Check TP Hit
            if self.locked_tp is not None and candle.high >= self.locked_tp:
                self.state = RetracementState.COMPLETED
                self.outcome = "TP_HIT"
                self.completion_reason = f"Take Profit hit at {self.locked_tp}."
            # Check SL Hit
            elif self.sl_price is not None and candle.low <= self.sl_price:
                self.state = RetracementState.COMPLETED
                self.outcome = "SL_HIT"
                self.completion_reason = f"Stop Loss hit at {self.sl_price}."
        else:
            # Check TP Hit
            if self.locked_tp is not None and candle.low <= self.locked_tp:
                self.state = RetracementState.COMPLETED
                self.outcome = "TP_HIT"
                self.completion_reason = f"Take Profit hit at {self.locked_tp}."
            # Check SL Hit
            elif self.sl_price is not None and candle.high >= self.sl_price:
                self.state = RetracementState.COMPLETED
                self.outcome = "SL_HIT"
                self.completion_reason = f"Stop Loss hit at {self.sl_price}."

    def to_dict(self, live_price: float | None = None) -> dict[str, Any]:
        """Serialize state for API and frontend display."""
        tp_val = self.locked_tp if self.tp_locked else self.target_tp_price
        entry_val = self.entry_price
        sl_val = self.sl_price
        span = abs(self.target_tp_price - self.point_2_price) if (self.target_tp_price and self.point_2_price) else 0.0

        e2tp = round(abs(tp_val - entry_val), 2) if (tp_val and entry_val) else 0.0
        e2sl = round(abs(entry_val - sl_val), 2) if (entry_val and sl_val) else 0.0
        rr = round(e2tp / e2sl, 2) if e2sl > 0 else 2.83

        curr_move = 0.0
        if live_price is not None and entry_val is not None:
            curr_move = round(live_price - entry_val, 2) if self.direction == SignalDirection.LONG else round(entry_val - live_price, 2)

        return {
            "strategy": "SMC_WITH_FIB",
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value if isinstance(self.direction, SignalDirection) else str(self.direction),
            "state": self.state.value,
            "has_live_data": bool(self._history),
            "is_entry_ready": self.state in (RetracementState.TP_DYNAMIC, RetracementState.FIB_ACTIVE) and not self.entry_touched,
            "is_entry_touched": self.entry_touched,
            "is_trade_active": self.state == RetracementState.TRADE_ACTIVE,
            "entry": {
                "price": self.entry_price,
                "touched": self.entry_touched,
                "timestamp": self.entry_timestamp.isoformat() if self.entry_timestamp else None,
            },
            "sl": {"price": self.sl_price},
            "tp": {
                "price": tp_val,
                "dynamic": self.target_tp_price,
                "locked": self.locked_tp,
                "is_locked": self.tp_locked,
            },
            "point_1": {"price": self.point_1_price, "timestamp": self.point_1_ts.isoformat() if self.point_1_ts else None},
            "point_2": {"price": self.point_2_price, "timestamp": self.point_2_ts.isoformat() if self.point_2_ts else None},
            "bos": {"price": self.point_1_price},
            "levels": {
                r: {"price": lvl.price, "label": lvl.label, "ratio": lvl.ratio}
                for r, lvl in self.levels.items()
            },
            "metrics": {
                "total_range_pts": round(span, 2),
                "entry_to_tp_pts": e2tp,
                "entry_to_sl_pts": e2sl,
                "rr_ratio": rr,
                "current_movement_pts": curr_move,
            },
            "smc": {
                "zone": "PREMIUM" if (live_price and self.equilibrium_50 and live_price > self.equilibrium_50) else "DISCOUNT",
                "equilibrium_50": self.equilibrium_50,
                "golden_pocket_lo": self.entry_price,
                "golden_pocket_hi": self.pocket_price,
                "sl_092": self.sl_price,
                "target_00": self.target_tp_price,
            },
            "invalidation_reason": self.invalidation_reason,
            "completion_reason": self.completion_reason,
            "outcome": self.outcome,
        }
