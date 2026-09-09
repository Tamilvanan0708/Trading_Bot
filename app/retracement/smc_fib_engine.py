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

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m", left_bars: int = 2, right_bars: int = 2):
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
        self.max_expiry_candles: int = 200

        self.levels: dict[str, RetracementLevel] = {}
        self._events: list[RetracementEvent] = []
        self._history: list[Candle] = []

    def _anchor_lookback_bars(self) -> int:
        """Timeframe-aware anchor lookback.

        We need enough bars to cover the full impulse leg so the engine
        finds the CORRECT swing extreme that caused the BOS:

          1m  → 30 bars  (30 min)
          3m  → 40 bars  (2 h)
          5m  → 50 bars  (4 h 10 min) — covers typical intraday sessions
          15m → 60 bars  (15 h) — macro swing
          30m → 60 bars
          1h+ → 60 bars
        """
        tf = str(self.timeframe).lower()
        if tf == "1m":
            return 30
        if tf == "3m":
            return 40
        if tf == "5m":
            return 50
        return 60

    def _reset_setup(self) -> None:
        """Clear setup variables while preserving candle history for continuous scanning."""
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

    def process_candle(self, candle: Candle) -> None:
        """Process a newly closed candle in strict chronological order."""
        self._history.append(candle)
        if len(self._history) < 20:
            return

        # Keep rolling window bounded
        if len(self._history) > 300:
            self._history = self._history[-300:]

        if self.state in (RetracementState.COMPLETED, RetracementState.INVALIDATED):
            # Previous trade is finished -> Auto-clear to scan fresh BOS setup
            self._reset_setup()

        if self.state == RetracementState.NO_SETUP:
            self._detect_setup(candle)
        elif self.state in (RetracementState.BOS_DETECTED, RetracementState.POINT_2_IDENTIFIED, RetracementState.FIB_ACTIVE, RetracementState.TP_DYNAMIC):
            self._track_and_check_entry(candle)
        elif self.state in (RetracementState.ENTRY_TOUCHED, RetracementState.TP_FROZEN, RetracementState.TRADE_ACTIVE):
            self._track_active_trade(candle)

    def _detect_setup(self, candle: Candle) -> None:
        swings = detect_swings(self._history, left_bars=self.left_bars, right_bars=self.right_bars)
        confirmed_highs = [s for s in swings if s.point_type == "HIGH" and s.index + self.right_bars <= len(self._history) - 1]
        confirmed_lows = [s for s in swings if s.point_type == "LOW" and s.index + self.right_bars <= len(self._history) - 1]

        if not confirmed_highs or not confirmed_lows:
            return

        last_high = confirmed_highs[-1]
        last_low = confirmed_lows[-1]

        # Timeframe-aware anchor lookback: low timeframes (1m/3m/5m) isolate
        # recent internal micro-structure, higher timeframes keep macro swings.
        lookback_bars = self._anchor_lookback_bars()

        # Check for Bullish BOS (Price broke above previous confirmed swing high)
        if candle.close > last_high.price and last_high.index < len(self._history) - 1:
            # Bullish anchor = LOWEST swing low of the current swing leg
            if len(confirmed_highs) >= 2:
                prev_high = confirmed_highs[-2]
                leg_lows = [
                    s for s in confirmed_lows
                    if prev_high.index <= s.index and (len(self._history) - 1 - s.index) <= lookback_bars
                ]
            else:
                leg_lows = []

            if leg_lows:
                anchor_low = min(leg_lows, key=lambda s: s.price)
            else:
                lows_before_bos = [
                    s for s in confirmed_lows
                    if s.index <= last_high.index and (last_high.index - s.index) <= lookback_bars
                ]
                anchor_low = min(lows_before_bos, key=lambda s: s.price) if lows_before_bos else last_low

            self._initiate_setup(
                direction=SignalDirection.LONG,
                p1_price=last_high.price,
                p1_ts=last_high.timestamp,
                p2_price=anchor_low.price,
                p2_ts=anchor_low.timestamp,
                current_candle=candle,
            )
        # Check for Bearish BOS (Price broke below previous confirmed swing low)
        elif candle.close < last_low.price and last_low.index < len(self._history) - 1:
            # Bearish anchor = HIGHEST swing high of the current swing leg
            if len(confirmed_lows) >= 2:
                prev_low = confirmed_lows[-2]
                leg_highs = [
                    s for s in confirmed_highs
                    if prev_low.index <= s.index and (len(self._history) - 1 - s.index) <= lookback_bars
                ]
            else:
                leg_highs = []

            if leg_highs:
                anchor_high = max(leg_highs, key=lambda s: s.price)
            else:
                highs_before_bos = [
                    s for s in confirmed_highs
                    if s.index <= last_low.index and (last_low.index - s.index) <= lookback_bars
                ]
                anchor_high = max(highs_before_bos, key=lambda s: s.price) if highs_before_bos else last_high

            self._initiate_setup(
                direction=SignalDirection.SHORT,
                p1_price=last_low.price,
                p1_ts=last_low.timestamp,
                p2_price=anchor_high.price,
                p2_ts=anchor_high.timestamp,
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
            # Bullish CHOCH Reversal (Drawing 2):
            # Level 1.000 = Extreme Low Anchor (point_2_price)
            # Level 0.000 = Target High (target_tp_price)
            # Retracement dips down from 0.000 toward 1.000
            lo = self.point_2_price
            hi = self.target_tp_price
            span = hi - lo if hi > lo else 1.0

            self.equilibrium_50 = round(lo + span * 0.500, 2)
            self.entry_price = round(lo + span * 0.320, 2)    # 0.680 retracement from top = (1 - 0.680) = 0.320 from base
            self.pocket_price = round(lo + span * 0.210, 2)   # 0.790 retracement from top = (1 - 0.790) = 0.210 from base
            self.sl_price = round(lo + span * 0.080, 2)       # 0.920 retracement from top = (1 - 0.920) = 0.080 from base

            self.levels = {
                "1.000": RetracementLevel(ratio=1.000, price=lo, label="SWING_ANCHOR"),
                "0.920": RetracementLevel(ratio=0.920, price=self.sl_price, label="STOP_LOSS"),
                "0.790": RetracementLevel(ratio=0.790, price=self.pocket_price, label="GOLDEN_POCKET"),
                "0.680": RetracementLevel(ratio=0.680, price=self.entry_price, label="ENTRY"),
                "0.500": RetracementLevel(ratio=0.500, price=self.equilibrium_50, label="EQUILIBRIUM"),
                "0.000": RetracementLevel(ratio=0.000, price=hi, label="TARGET_TP"),
            }
        else:
            # Bearish CHOCH Reversal (Drawing 1):
            # Level 1.000 = Extreme High Anchor (point_2_price)
            # Level 0.000 = Target Low (target_tp_price)
            # Retracement pulls up from 0.000 toward 1.000
            hi = self.point_2_price
            lo = self.target_tp_price
            span = hi - lo if hi > lo else 1.0

            self.equilibrium_50 = round(lo + span * 0.500, 2)
            self.entry_price = round(lo + span * 0.680, 2)    # 0.680 retracement up from bottom
            self.pocket_price = round(lo + span * 0.790, 2)   # 0.790 retracement up from bottom (Order Block)
            self.sl_price = round(lo + span * 0.920, 2)       # 0.920 retracement up from bottom (SL)

            self.levels = {
                "1.000": RetracementLevel(ratio=1.000, price=hi, label="SWING_ANCHOR"),
                "0.920": RetracementLevel(ratio=0.920, price=self.sl_price, label="STOP_LOSS"),
                "0.790": RetracementLevel(ratio=0.790, price=self.pocket_price, label="GOLDEN_POCKET"),
                "0.680": RetracementLevel(ratio=0.680, price=self.entry_price, label="ENTRY"),
                "0.500": RetracementLevel(ratio=0.500, price=self.equilibrium_50, label="EQUILIBRIUM"),
                "0.000": RetracementLevel(ratio=0.000, price=lo, label="TARGET_TP"),
            }

    def _track_and_check_entry(self, candle: Candle) -> None:
        self.candles_since_point_2 += 1
        if self.candles_since_point_2 > self.max_expiry_candles:
            self.state = RetracementState.INVALIDATED
            self.invalidation_reason = f"Setup expired after {self.max_expiry_candles} candles without entry touch."
            return

        # Continuation BOS detection: while waiting for entry (single trade not touched yet),
        # if price makes a fresh BOS in the trend direction, update the dealing range
        # to the active impulse leg so we track the latest Lower High / Higher Low.
        if not self.entry_touched:
            swings = detect_swings(self._history, left_bars=self.left_bars, right_bars=self.right_bars)
            confirmed_highs = [s for s in swings if s.point_type == "HIGH" and s.index + self.right_bars <= len(self._history) - 1]
            confirmed_lows = [s for s in swings if s.point_type == "LOW" and s.index + self.right_bars <= len(self._history) - 1]
            lookback_bars = self._anchor_lookback_bars()

            if self.direction == SignalDirection.SHORT and confirmed_highs and confirmed_lows:
                last_low = confirmed_lows[-1]
                if candle.close < last_low.price and last_low.index < len(self._history) - 1:
                    if self.point_1_ts and last_low.timestamp > self.point_1_ts:
                        if len(confirmed_lows) >= 2:
                            prev_low = confirmed_lows[-2]
                            leg_highs = [
                                s for s in confirmed_highs
                                if prev_low.index <= s.index and (len(self._history) - 1 - s.index) <= lookback_bars
                            ]
                        else:
                            leg_highs = []

                        if leg_highs:
                            anchor_high = max(leg_highs, key=lambda s: s.price)
                        else:
                            highs_before_bos = [
                                s for s in confirmed_highs
                                if s.index <= last_low.index and (last_low.index - s.index) <= lookback_bars
                            ]
                            anchor_high = max(highs_before_bos, key=lambda s: s.price) if highs_before_bos else confirmed_highs[-1]

                        self._initiate_setup(
                            direction=SignalDirection.SHORT,
                            p1_price=last_low.price,
                            p1_ts=last_low.timestamp,
                            p2_price=anchor_high.price,
                            p2_ts=anchor_high.timestamp,
                            current_candle=candle,
                        )
                        return

            elif self.direction == SignalDirection.LONG and confirmed_highs and confirmed_lows:
                last_high = confirmed_highs[-1]
                if candle.close > last_high.price and last_high.index < len(self._history) - 1:
                    if self.point_1_ts and last_high.timestamp > self.point_1_ts:
                        if len(confirmed_highs) >= 2:
                            prev_high = confirmed_highs[-2]
                            leg_lows = [
                                s for s in confirmed_lows
                                if prev_high.index <= s.index and (len(self._history) - 1 - s.index) <= lookback_bars
                            ]
                        else:
                            leg_lows = []

                        if leg_lows:
                            anchor_low = min(leg_lows, key=lambda s: s.price)
                        else:
                            lows_before_bos = [
                                s for s in confirmed_lows
                                if s.index <= last_high.index and (last_high.index - s.index) <= lookback_bars
                            ]
                            anchor_low = min(lows_before_bos, key=lambda s: s.price) if lows_before_bos else confirmed_lows[-1]

                        self._initiate_setup(
                            direction=SignalDirection.LONG,
                            p1_price=last_high.price,
                            p1_ts=last_high.timestamp,
                            p2_price=anchor_low.price,
                            p2_ts=anchor_low.timestamp,
                            current_candle=candle,
                        )
                        return

        # 1. Update dynamic target if new extremes are formed before entry
        if self.direction == SignalDirection.LONG:
            if candle.high > (self.target_tp_price or 0.0):
                self.target_tp_price = candle.high
                self.target_tp_ts = candle.timestamp
                self._recompute_fib_levels()
            # Single Entry at 0.680 Golden Pocket (0.01 lots)
            if self.entry_price is not None and candle.low <= self.entry_price:
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
            # Single Entry at 0.680 Golden Pocket (0.01 lots)
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
        # 1. Opposite Market Structure Break (CHoCH Reversal):
        # If market structure breaks in the opposite direction, the current trade is invalidated / stopped out by CHoCH,
        # and we immediately initiate the reversal setup (e.g. Bearish Short stopped out -> Bullish Long setup).
        swings = detect_swings(self._history, left_bars=self.left_bars, right_bars=self.right_bars)
        confirmed_highs = [s for s in swings if s.point_type == "HIGH" and s.index + self.right_bars <= len(self._history) - 1]
        confirmed_lows = [s for s in swings if s.point_type == "LOW" and s.index + self.right_bars <= len(self._history) - 1]

        if self.direction == SignalDirection.SHORT and confirmed_highs:
            last_high = confirmed_highs[-1]
            if candle.close > last_high.price and last_high.index < len(self._history) - 1:
                self.state = RetracementState.COMPLETED
                self.outcome = "SL_HIT"
                self.completion_reason = f"CHoCH Reversal: Price broke above swing high at {last_high.price}."
                # Do NOT call _reset_setup here — the caller needs to observe the
                # completed state.  Reset + re-detect will happen on the next candle.
                return
        elif self.direction == SignalDirection.LONG and confirmed_lows:
            last_low = confirmed_lows[-1]
            if candle.close < last_low.price and last_low.index < len(self._history) - 1:
                self.state = RetracementState.COMPLETED
                self.outcome = "SL_HIT"
                self.completion_reason = f"CHoCH Reversal: Price broke below swing low at {last_low.price}."
                # Do NOT call _reset_setup here — the caller needs to observe the
                # completed state.  Reset + re-detect will happen on the next candle.
                return

        if self.direction == SignalDirection.LONG:
            # Check SL Hit at 0.920 Stop Loss first (matching research evaluator)
            if self.sl_price is not None and candle.low <= self.sl_price:
                self.state = RetracementState.COMPLETED
                self.outcome = "SL_HIT"
                self.completion_reason = f"Stop Loss hit at {self.sl_price}."
                return
            # Check TP Hit at 0.000 Target
            elif self.locked_tp is not None and candle.high >= self.locked_tp:
                self.state = RetracementState.COMPLETED
                self.outcome = "TP_HIT"
                self.completion_reason = f"Take Profit hit at {self.locked_tp}."
                return
        else:
            # Check SL Hit at 0.920 Stop Loss first (matching research evaluator)
            if self.sl_price is not None and candle.high >= self.sl_price:
                self.state = RetracementState.COMPLETED
                self.outcome = "SL_HIT"
                self.completion_reason = f"Stop Loss hit at {self.sl_price}."
                return
            # Check TP Hit at 0.000 Target
            elif self.locked_tp is not None and candle.low <= self.locked_tp:
                self.state = RetracementState.COMPLETED
                self.outcome = "TP_HIT"
                self.completion_reason = f"Take Profit hit at {self.locked_tp}."
                return

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
            "is_layered": False,
            "lots": 0.01,
        }
