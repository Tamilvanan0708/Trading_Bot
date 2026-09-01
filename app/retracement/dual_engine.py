"""
Dual-Direction Retracement BOS Engine (Bullish LONG & Bearish SHORT).

Exact User Fibonacci Semantics (Zero Look-Ahead & Instant Level Freeze):

BULLISH RETRACEMENT (LONG):
  - BOS: Price breaks above previous confirmed Swing High.
  - Point 2 (Anchor 0.000): Lowest low of the BOS leg.
  - Level 1.000 (Target TP): Dynamic trailing on every new swing high before entry touch.
  - Level 0.618 (Entry): Pullback entry price.
  - Level 0.236 (SL): Invalidation stop loss.
  - Level 1.618: Extension reference.
  - On Entry Touch (0.618): FREEZE & LOCK Level 1.000 TP and Level 0.236 SL immediately!

BEARISH RETRACEMENT (SHORT):
  - BOS: Price breaks below previous confirmed Swing Low.
  - Point 2 (Anchor 0.000): Highest high of the BOS leg.
  - Level 1.000 (Target TP): Dynamic trailing on every new swing low before entry touch.
  - Level 0.618 (Entry): Pullback entry price.
  - Level 0.236 (SL): Invalidation stop loss.
  - Level 1.618: Extension reference.
  - On Entry Touch (0.618): FREEZE & LOCK Level 1.000 TP and Level 0.236 SL immediately!
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.constants import SignalDirection
from app.core.logging import logger
from app.data.models import Candle
from app.indicators.swings import SwingPoint, detect_swings
from app.retracement.models import (
    RetracementEvent,
    RetracementEventType,
    RetracementLevel,
    RetracementSetup,
    RetracementState,
    utcnow,
)


class DualRetracementEngine:
    """Exact deterministic dual-direction (Bullish/Bearish) Retracement BOS Engine."""

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m", left_bars: int = 3, right_bars: int = 3):
        self.symbol = symbol
        self.timeframe = timeframe
        self.left_bars = left_bars
        self.right_bars = right_bars
        self.setup: RetracementSetup | None = None
        self._candles: list[Candle] = []
        self._events: list[RetracementEvent] = []
        self._archived_setups: list[RetracementSetup] = []

    def reset(self) -> None:
        self.setup = None
        self._candles = []
        self._events = []
        self._archived_setups = []

    def process_candle(self, candle: Candle) -> list[RetracementEvent]:
        self._candles.append(candle)
        if len(self._candles) < 25:
            return []

        # Keep rolling window bounded
        if len(self._candles) > 300:
            self._candles = self._candles[-300:]

        events: list[RetracementEvent] = []
        if self.setup is None:
            events.extend(self._detect_bos(candle))
        elif self.setup.state in (RetracementState.BOS_DETECTED, RetracementState.POINT_2_IDENTIFIED, RetracementState.FIB_ACTIVE, RetracementState.TP_DYNAMIC):
            events.extend(self._track_and_check_entry(candle))
        elif self.setup.state in (RetracementState.ENTRY_TOUCHED, RetracementState.TP_FROZEN, RetracementState.TRADE_ACTIVE):
            events.extend(self._track_active_trade(candle))

        # Auto-archive: if setup is now completed/invalidated, clear it and immediately try to detect a new BOS
        if self.setup is not None and self.setup.state in (RetracementState.COMPLETED, RetracementState.INVALIDATED):
            self._archived_setups.append(self.setup)
            self.setup = None
            # Try to detect a new BOS immediately on the same candle
            events.extend(self._detect_bos(candle))

        self._events.extend(events)
        return events


    def _detect_bos(self, candle: Candle) -> list[RetracementEvent]:
        swings = detect_swings(self._candles, left_bars=self.left_bars, right_bars=self.right_bars)
        confirmed_highs = [s for s in swings if s.point_type == "HIGH" and s.index + self.right_bars <= len(self._candles) - 1]
        confirmed_lows = [s for s in swings if s.point_type == "LOW" and s.index + self.right_bars <= len(self._candles) - 1]

        if not confirmed_highs or not confirmed_lows:
            return []

        last_sh = confirmed_highs[-1]
        last_sl = confirmed_lows[-1]

        # 1. Check Bullish BOS (Close > last confirmed swing high)
        if candle.close > last_sh.price and last_sh.index < len(self._candles) - 1:
            prior_lows = [s for s in confirmed_lows if s.index < last_sh.index]
            p2_low = prior_lows[-1].price if prior_lows else min(c.low for c in self._candles[last_sh.index:])
            p2_ts = prior_lows[-1].timestamp if prior_lows else candle.timestamp

            setup = RetracementSetup(
                symbol=self.symbol,
                timeframe=self.timeframe,
                direction="LONG",
                state=RetracementState.TP_DYNAMIC,
                point_1_price=last_sh.price,
                point_1_timestamp=last_sh.timestamp,
                bos_price=last_sh.price,
                bos_timestamp=last_sh.timestamp,
                point_2_price=p2_low,
                point_2_timestamp=p2_ts,
                current_high_price=candle.high,
                current_high_timestamp=candle.timestamp,
                dynamic_tp=candle.high,
                validation_passed=True,
            )
            self._apply_bullish_fib(setup, p2_low, candle.high)
            self.setup = setup
            return [RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.BOS_DETECTED, state_before=RetracementState.NO_SETUP, state_after=RetracementState.TP_DYNAMIC, timestamp=candle.timestamp, price=candle.close)]

        # 2. Check Bearish BOS (Close < last confirmed swing low)
        elif candle.close < last_sl.price and last_sl.index < len(self._candles) - 1:
            prior_highs = [s for s in confirmed_highs if s.index < last_sl.index]
            p2_high = prior_highs[-1].price if prior_highs else max(c.high for c in self._candles[last_sl.index:])
            p2_ts = prior_highs[-1].timestamp if prior_highs else candle.timestamp

            setup = RetracementSetup(
                symbol=self.symbol,
                timeframe=self.timeframe,
                direction="SHORT",
                state=RetracementState.TP_DYNAMIC,
                point_1_price=last_sl.price,
                point_1_timestamp=last_sl.timestamp,
                bos_price=last_sl.price,
                bos_timestamp=last_sl.timestamp,
                point_2_price=p2_high,
                point_2_timestamp=p2_ts,
                current_high_price=candle.low,
                current_high_timestamp=candle.timestamp,
                dynamic_tp=candle.low,
                validation_passed=True,
            )
            self._apply_bearish_fib(setup, p2_high, candle.low)
            self.setup = setup
            return [RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.BOS_DETECTED, state_before=RetracementState.NO_SETUP, state_after=RetracementState.TP_DYNAMIC, timestamp=candle.timestamp, price=candle.close)]

        return []

    def _apply_bullish_fib(self, setup: RetracementSetup, low_anchor: float, high_target: float) -> None:
        span = high_target - low_anchor if high_target > low_anchor else 1.0
        setup.fib_0 = low_anchor
        setup.fib_0_236 = round(low_anchor + span * 0.236, 2)
        setup.fib_0_382 = round(low_anchor + span * 0.382, 2)
        setup.fib_0_500 = round(low_anchor + span * 0.500, 2)
        setup.fib_0_618 = round(low_anchor + span * 0.618, 2)
        setup.fib_1_000 = high_target
        setup.fib_1_618 = round(low_anchor + span * 1.618, 2)

        setup.entry_price = setup.fib_0_618
        setup.sl_price = setup.fib_0_236
        setup.dynamic_tp = setup.fib_1_000

    def _apply_bearish_fib(self, setup: RetracementSetup, high_anchor: float, low_target: float) -> None:
        span = high_anchor - low_target if high_anchor > low_target else 1.0
        setup.fib_0 = high_anchor
        setup.fib_0_236 = round(high_anchor - span * 0.236, 2)
        setup.fib_0_382 = round(high_anchor - span * 0.382, 2)
        setup.fib_0_500 = round(high_anchor - span * 0.500, 2)
        setup.fib_0_618 = round(high_anchor - span * 0.618, 2)
        setup.fib_1_000 = low_target
        setup.fib_1_618 = round(high_anchor - span * 1.618, 2)

        setup.entry_price = setup.fib_0_618
        setup.sl_price = setup.fib_0_236
        setup.dynamic_tp = setup.fib_1_000

    def _track_and_check_entry(self, candle: Candle) -> list[RetracementEvent]:
        setup = self.setup
        if setup is None:
            return []
        events: list[RetracementEvent] = []

        if setup.direction == "LONG":
            # 1. Update dynamic target if new high forms
            if candle.high > (setup.current_high_price or 0.0):
                setup.current_high_price = candle.high
                setup.current_high_timestamp = candle.timestamp
                self._apply_bullish_fib(setup, setup.point_2_price, candle.high)

            # 2. Check Entry Touch (0.618 touched)
            if setup.entry_price is not None and candle.low <= setup.entry_price:
                setup.entry_touched = True
                setup.entry_timestamp = candle.timestamp
                setup.tp_locked = True
                setup.locked_tp = setup.dynamic_tp
                setup.state = RetracementState.TRADE_ACTIVE
                events.append(RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.ENTRY_TOUCHED, state_before=RetracementState.TP_DYNAMIC, state_after=RetracementState.TRADE_ACTIVE, timestamp=candle.timestamp, price=setup.entry_price))
        else:
            # 1. Update dynamic target if new low forms
            if candle.low < (setup.current_high_price or float("inf")):
                setup.current_high_price = candle.low
                setup.current_high_timestamp = candle.timestamp
                self._apply_bearish_fib(setup, setup.point_2_price, candle.low)

            # 2. Check Entry Touch (0.618 touched)
            if setup.entry_price is not None and candle.high >= setup.entry_price:
                setup.entry_touched = True
                setup.entry_timestamp = candle.timestamp
                setup.tp_locked = True
                setup.locked_tp = setup.dynamic_tp
                setup.state = RetracementState.TRADE_ACTIVE
                events.append(RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.ENTRY_TOUCHED, state_before=RetracementState.TP_DYNAMIC, state_after=RetracementState.TRADE_ACTIVE, timestamp=candle.timestamp, price=setup.entry_price))

        return events

    def _track_active_trade(self, candle: Candle) -> list[RetracementEvent]:
        setup = self.setup
        if setup is None:
            return []
        events: list[RetracementEvent] = []

        if setup.direction == "LONG":
            if setup.locked_tp is not None and candle.high >= setup.locked_tp:
                setup.state = RetracementState.COMPLETED
                setup.outcome = "TP_HIT"
                setup.completion_reason = f"Take Profit reached at {setup.locked_tp}"
                events.append(RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.TP_HIT, state_before=RetracementState.TRADE_ACTIVE, state_after=RetracementState.COMPLETED, timestamp=candle.timestamp, price=setup.locked_tp))
            elif setup.sl_price is not None and candle.low <= setup.sl_price:
                setup.state = RetracementState.COMPLETED
                setup.outcome = "SL_HIT"
                setup.completion_reason = f"Stop Loss hit at {setup.sl_price}"
                events.append(RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.SL_HIT, state_before=RetracementState.TRADE_ACTIVE, state_after=RetracementState.COMPLETED, timestamp=candle.timestamp, price=setup.sl_price))
        else:
            if setup.locked_tp is not None and candle.low <= setup.locked_tp:
                setup.state = RetracementState.COMPLETED
                setup.outcome = "TP_HIT"
                setup.completion_reason = f"Take Profit reached at {setup.locked_tp}"
                events.append(RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.TP_HIT, state_before=RetracementState.TRADE_ACTIVE, state_after=RetracementState.COMPLETED, timestamp=candle.timestamp, price=setup.locked_tp))
            elif setup.sl_price is not None and candle.high >= setup.sl_price:
                setup.state = RetracementState.COMPLETED
                setup.outcome = "SL_HIT"
                setup.completion_reason = f"Stop Loss hit at {setup.sl_price}"
                events.append(RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.SL_HIT, state_before=RetracementState.TRADE_ACTIVE, state_after=RetracementState.COMPLETED, timestamp=candle.timestamp, price=setup.sl_price))

        return events

    def archive_completed(self) -> RetracementSetup | None:
        if self.setup is not None and self.setup.state in (RetracementState.COMPLETED, RetracementState.INVALIDATED):
            completed = self.setup
            self.setup = None
            return completed
        return None
