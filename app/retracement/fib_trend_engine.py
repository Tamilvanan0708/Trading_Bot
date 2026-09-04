"""
Fib Go With Trend Engine (9 EMA & 21 EMA + Fibonacci Retracement & Breakout Confirmation).

Rules:
1. TREND NEED TO CHANGE (Shift in market structure / EMA Golden or Death Cross)
2. EMA SHOULD INTERCROSS (9 EMA crosses 21 EMA)
3. 1 SWING NEED TO COMPLETE (Impulse Wave 1 completes from Anchor Point 0 to Peak Point 1)
4. TAKE A TRADE IN NEXT SWING (Prepare for entry on the Pullback / Retracement Swing 2)
5. EMA SHOULD NOT INTERCROSS (During retracement, 9 EMA must remain aligned with the trend)
6. IMPLEMENT FIB (Plot Fibonacci 0.000, 0.236, 0.382, 0.500, 0.618, 1.000, 1.618)
7. 0.618 NEED TO TOUCH (Pullback dips to touch the Golden Ratio 0.618 level)
8. TAKE A TRADE IN NEXT CANDLE ONCE IT CROSS 0.618 CANDLE (Reversal breakout trigger)
   - For LONG: Trigger line armed at touch candle's HIGH. Next candle crossing it executes BUY.
   - For SHORT: Trigger line armed at touch candle's LOW. Next candle crossing it executes SELL.
   - Stop Loss @ 0.236 Fib level.
   - Take Profit @ 1.618 Fib Extension target.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.constants import SignalDirection
from app.data.models import Candle

logger = logging.getLogger("xauusd_agent")


class FibTrendState(str, Enum):
    NO_SETUP = "NO_SETUP"
    SWING_1_EXPANSION = "SWING_1_EXPANSION"
    WAITING_FOR_0618 = "WAITING_FOR_0618"
    WAITING_FOR_BREAKOUT = "WAITING_FOR_BREAKOUT"
    TRADE_ACTIVE = "TRADE_ACTIVE"
    COMPLETED = "COMPLETED"
    INVALIDATED = "INVALIDATED"


class FibTrendEngine:
    """Deterministic implementation of FIB GO WITH TREND strategy."""

    def __init__(
        self,
        symbol: str = "XAUUSD",
        timeframe: str = "5m",
        fast_ema_len: int = 9,
        slow_ema_len: int = 21,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.fast_ema_len = fast_ema_len
        self.slow_ema_len = slow_ema_len

        self.state = FibTrendState.NO_SETUP
        self.direction = SignalDirection.LONG

        self.setup_id: str | None = None
        self.point_0_price: float | None = None  # Anchor / Origin of impulse
        self.point_0_ts: datetime | None = None
        self.point_1_price: float | None = None  # Peak of Swing 1
        self.point_1_ts: datetime | None = None

        # Fib levels
        self.fib_0_000: float | None = None
        self.fib_0_236: float | None = None  # SL
        self.fib_0_382: float | None = None
        self.fib_0_500: float | None = None
        self.fib_0_618: float | None = None  # Entry touch target
        self.fib_1_000: float | None = None  # Swing 1 Peak
        self.fib_1_618: float | None = None  # TP Extension target

        # Touch & Breakout trigger tracking (Rule 8)
        self.touch_candle_ts: datetime | None = None
        self.trigger_breakout_price: float | None = None  # Blue line on chart
        self.entry_touched: bool = False
        self.entry_price: float | None = None
        self.entry_ts: datetime | None = None
        self.sl_price: float | None = None
        self.tp_price: float | None = None  # TP2 (1.618)
        self.tp1_price: float | None = None  # TP1 (1.000)
        self.tp1_hit: bool = False
        self.tp1_ts: datetime | None = None

        self.outcome: str | None = None
        self.completion_reason: str | None = None
        self.invalidation_reason: str | None = None

        # EMAs
        self.current_ema_9: float | None = None
        self.current_ema_21: float | None = None

        self._candles: list[Candle] = []
        self._ema_9_series: list[float] = []
        self._ema_21_series: list[float] = []

        self._candles_since_touch: int = 0
        self._max_breakout_wait_candles: int = 6  # wait up to 6 candles for Rule 8 breakout

    def reset(self) -> None:
        self._reset_setup()
        self._candles = []
        self._ema_9_series = []
        self._ema_21_series = []

    def _reset_setup(self) -> None:
        self.state = FibTrendState.NO_SETUP
        self.direction = SignalDirection.LONG
        self.setup_id = None
        self.point_0_price = None
        self.point_0_ts = None
        self.point_1_price = None
        self.point_1_ts = None
        self.fib_0_000 = None
        self.fib_0_236 = None
        self.fib_0_382 = None
        self.fib_0_500 = None
        self.fib_0_618 = None
        self.fib_1_000 = None
        self.fib_1_618 = None
        self.touch_candle_ts = None
        self.trigger_breakout_price = None
        self.entry_touched = False
        self.entry_price = None
        self.entry_ts = None
        self.sl_price = None
        self.tp_price = None
        self.tp1_price = None
        self.tp1_hit = False
        self.tp1_ts = None
        self.outcome = None
        self.completion_reason = None
        self.invalidation_reason = None
        self._candles_since_touch = 0

    def _compute_emas(self, candle: Candle) -> tuple[float, float]:
        close = candle.close
        k9 = 2.0 / (self.fast_ema_len + 1.0)
        k21 = 2.0 / (self.slow_ema_len + 1.0)

        if not self._ema_9_series:
            ema9 = close
            ema21 = close
        else:
            ema9 = (close * k9) + (self._ema_9_series[-1] * (1.0 - k9))
            ema21 = (close * k21) + (self._ema_21_series[-1] * (1.0 - k21))

        self._ema_9_series.append(round(ema9, 3))
        self._ema_21_series.append(round(ema21, 3))
        self.current_ema_9 = round(ema9, 2)
        self.current_ema_21 = round(ema21, 2)
        return ema9, ema21

    def _apply_bullish_fib(self, p0: float, p1: float) -> None:
        span = p1 - p0
        self.fib_0_000 = round(p0, 2)
        self.fib_0_236 = round(p0 + 0.236 * span, 2)  # SL
        self.fib_0_382 = round(p0 + 0.382 * span, 2)
        self.fib_0_500 = round(p0 + 0.500 * span, 2)
        self.fib_0_618 = round(p0 + 0.618 * span, 2)  # Entry level
        self.fib_1_000 = round(p1, 2)
        self.fib_1_618 = round(p0 + 1.618 * span, 2)  # TP Extension Target

    def _apply_bearish_fib(self, p0: float, p1: float) -> None:
        span = p0 - p1
        self.fib_0_000 = round(p0, 2)
        self.fib_0_236 = round(p0 - 0.236 * span, 2)  # SL
        self.fib_0_382 = round(p0 - 0.382 * span, 2)
        self.fib_0_500 = round(p0 - 0.500 * span, 2)
        self.fib_0_618 = round(p0 - 0.618 * span, 2)  # Entry level
        self.fib_1_000 = round(p1, 2)
        self.fib_1_618 = round(p0 - 1.618 * span, 2)  # TP Extension Target

    def process_candle(self, candle: Candle) -> dict[str, Any] | None:
        self._candles.append(candle)
        ema9, ema21 = self._compute_emas(candle)

        if len(self._candles) < 25:
            return None

        # State 1: Look for EMA crossover (Rules 1 & 2)
        if self.state in (FibTrendState.NO_SETUP, FibTrendState.COMPLETED, FibTrendState.INVALIDATED):
            prev_ema9 = self._ema_9_series[-2]
            prev_ema21 = self._ema_21_series[-2]

            # Bullish Golden Cross
            if prev_ema9 <= prev_ema21 and ema9 > ema21:
                self._reset_setup()
                self.setup_id = str(uuid.uuid4())
                self.direction = SignalDirection.LONG

                # Find Anchor Low in the 10 candles before/at the crossover
                recent_lows = [c for c in self._candles[-10:]]
                anchor_c = min(recent_lows, key=lambda c: c.low)
                self.point_0_price = round(anchor_c.low, 2)
                self.point_0_ts = anchor_c.timestamp

                self.point_1_price = round(candle.high, 2)
                self.point_1_ts = candle.timestamp
                self.state = FibTrendState.SWING_1_EXPANSION
                logger.info(
                    "[FIB-TREND] Bullish EMA Cross (9 > 21) at $%.2f | Anchor P0=$%.2f",
                    candle.close, self.point_0_price,
                )
                return {"event": "EMA_CROSS", "direction": "LONG", "p0": self.point_0_price}

            # Bearish Death Cross
            if prev_ema9 >= prev_ema21 and ema9 < ema21:
                self._reset_setup()
                self.setup_id = str(uuid.uuid4())
                self.direction = SignalDirection.SHORT

                # Find Anchor High in the 10 candles before/at the crossover
                recent_highs = [c for c in self._candles[-10:]]
                anchor_c = max(recent_highs, key=lambda c: c.high)
                self.point_0_price = round(anchor_c.high, 2)
                self.point_0_ts = anchor_c.timestamp

                self.point_1_price = round(candle.low, 2)
                self.point_1_ts = candle.timestamp
                self.state = FibTrendState.SWING_1_EXPANSION
                logger.info(
                    "[FIB-TREND] Bearish EMA Cross (9 < 21) at $%.2f | Anchor P0=$%.2f",
                    candle.close, self.point_0_price,
                )
                return {"event": "EMA_CROSS", "direction": "SHORT", "p0": self.point_0_price}

            return None

        # State 2: Swing 1 Expansion (Rule 3: 1 Swing Need to Complete)
        if self.state == FibTrendState.SWING_1_EXPANSION:
            if self.direction == SignalDirection.LONG:
                if candle.high > (self.point_1_price or 0.0):
                    self.point_1_price = round(candle.high, 2)
                    self.point_1_ts = candle.timestamp
                elif candle.close < candle.open and (self.point_1_price - self.point_0_price) >= 4.0:
                    # Swing 1 reached its peak and formed a red reversal bar with >= 4 pts impulse
                    self._apply_bullish_fib(self.point_0_price, self.point_1_price)
                    self.state = FibTrendState.WAITING_FOR_0618
                    logger.info(
                        "[FIB-TREND] Bullish Swing 1 Complete: P0=$%.2f → P1=$%.2f (Fib 0.618: $%.2f, TP 1.618: $%.2f)",
                        self.point_0_price, self.point_1_price, self.fib_0_618, self.fib_1_618,
                    )
                    return {"event": "SWING_1_COMPLETE", "fib_0_618": self.fib_0_618}
            else:  # SHORT
                if candle.low < (self.point_1_price or float("inf")):
                    self.point_1_price = round(candle.low, 2)
                    self.point_1_ts = candle.timestamp
                elif candle.close > candle.open and (self.point_0_price - self.point_1_price) >= 4.0:
                    # Swing 1 reached its valley and formed a green reversal bar
                    self._apply_bearish_fib(self.point_0_price, self.point_1_price)
                    self.state = FibTrendState.WAITING_FOR_0618
                    logger.info(
                        "[FIB-TREND] Bearish Swing 1 Complete: P0=$%.2f → P1=$%.2f (Fib 0.618: $%.2f, TP 1.618: $%.2f)",
                        self.point_0_price, self.point_1_price, self.fib_0_618, self.fib_1_618,
                    )
                    return {"event": "SWING_1_COMPLETE", "fib_0_618": self.fib_0_618}

            return None

        # State 3: Waiting for 0.618 Touch (Rules 4, 5, 7)
        if self.state == FibTrendState.WAITING_FOR_0618:
            # Rule 5: EMA SHOULD NOT INTERCROSS
            if self.direction == SignalDirection.LONG and ema9 < ema21:
                self.state = FibTrendState.INVALIDATED
                self.invalidation_reason = "Rule 5 Violated: 9 EMA crossed below 21 EMA during pullback."
                logger.info("[FIB-TREND] Setup Invalidated: %s", self.invalidation_reason)
                return {"event": "INVALIDATED", "reason": self.invalidation_reason}

            if self.direction == SignalDirection.SHORT and ema9 > ema21:
                self.state = FibTrendState.INVALIDATED
                self.invalidation_reason = "Rule 5 Violated: 9 EMA crossed above 21 EMA during pullback."
                logger.info("[FIB-TREND] Setup Invalidated: %s", self.invalidation_reason)
                return {"event": "INVALIDATED", "reason": self.invalidation_reason}

            # Check if price touched 0.618 (Rule 7)
            if self.direction == SignalDirection.LONG:
                if candle.low <= self.fib_0_618:
                    self.entry_touched = True
                    self.touch_candle_ts = candle.timestamp
                    # Arm Rule 8 trigger on the HIGH of this 0.618 touch candle (Blue Line!)
                    self.trigger_breakout_price = round(candle.high, 2)
                    self.state = FibTrendState.WAITING_FOR_BREAKOUT
                    self._candles_since_touch = 0
                    logger.info(
                        "[FIB-TREND] Rule 7 Touch 0.618 ($%.2f) hit! Blue Trigger Line armed at High: $%.2f",
                        self.fib_0_618, self.trigger_breakout_price,
                    )
                    return {"event": "TOUCH_0618", "trigger_price": self.trigger_breakout_price}

                # If price drops below 0.236 before even touching 0.618, invalidate
                if self.fib_0_236 and candle.low <= self.fib_0_236:
                    self.state = FibTrendState.INVALIDATED
                    self.invalidation_reason = "Price violated 0.236 structure floor."
                    return {"event": "INVALIDATED", "reason": self.invalidation_reason}

            else:  # SHORT
                if candle.high >= self.fib_0_618:
                    self.entry_touched = True
                    self.touch_candle_ts = candle.timestamp
                    # Arm Rule 8 trigger on the LOW of this 0.618 touch candle
                    self.trigger_breakout_price = round(candle.low, 2)
                    self.state = FibTrendState.WAITING_FOR_BREAKOUT
                    self._candles_since_touch = 0
                    logger.info(
                        "[FIB-TREND] Rule 7 Touch 0.618 ($%.2f) hit! Blue Trigger Line armed at Low: $%.2f",
                        self.fib_0_618, self.trigger_breakout_price,
                    )
                    return {"event": "TOUCH_0618", "trigger_price": self.trigger_breakout_price}

                if self.fib_0_236 and candle.high >= self.fib_0_236:
                    self.state = FibTrendState.INVALIDATED
                    self.invalidation_reason = "Price violated 0.236 structure ceiling."
                    return {"event": "INVALIDATED", "reason": self.invalidation_reason}

            return None

        # State 4: Waiting for Breakout Trigger (Rule 8)
        if self.state == FibTrendState.WAITING_FOR_BREAKOUT:
            self._candles_since_touch += 1
            if self._candles_since_touch > self._max_breakout_wait_candles:
                self.state = FibTrendState.INVALIDATED
                self.invalidation_reason = "Breakout did not occur within expiry window."
                return {"event": "INVALIDATED", "reason": self.invalidation_reason}

            # Rule 5 check again
            if (self.direction == SignalDirection.LONG and ema9 < ema21) or (
                self.direction == SignalDirection.SHORT and ema9 > ema21
            ):
                self.state = FibTrendState.INVALIDATED
                self.invalidation_reason = "Rule 5 Violated: EMA intercrossed before breakout."
                return {"event": "INVALIDATED", "reason": self.invalidation_reason}

            if self.direction == SignalDirection.LONG:
                # Rule 8 Execution: Next candle crosses 0.618 candle's HIGH
                if candle.high > self.trigger_breakout_price:
                    self.entry_price = round(self.trigger_breakout_price, 2)
                    self.entry_ts = candle.timestamp
                    self.sl_price = round(self.fib_0_236, 2)
                    self.tp_price = round(self.fib_1_618, 2)
                    self.state = FibTrendState.TRADE_ACTIVE
                    logger.info(
                        "[FIB-TREND] 🚀 Rule 8 BUY TRIGGERED! Entry: $%.2f | SL: $%.2f (0.236) | TP: $%.2f (1.618)",
                        self.entry_price, self.sl_price, self.tp_price,
                    )
                    return {
                        "event": "TRADE_TRIGGERED",
                        "direction": "LONG",
                        "entry": self.entry_price,
                        "sl": self.sl_price,
                        "tp": self.tp_price,
                    }
                if self.fib_0_236 and candle.low <= self.fib_0_236:
                    self.state = FibTrendState.INVALIDATED
                    self.invalidation_reason = "Price hit 0.236 before breakout occurred."
                    return {"event": "INVALIDATED", "reason": self.invalidation_reason}

            else:  # SHORT
                # Rule 8 Execution: Next candle crosses 0.618 candle's LOW
                if candle.low < self.trigger_breakout_price:
                    self.entry_price = round(self.trigger_breakout_price, 2)
                    self.entry_ts = candle.timestamp
                    self.sl_price = round(self.fib_0_236, 2)
                    self.tp_price = round(self.fib_1_618, 2)
                    self.state = FibTrendState.TRADE_ACTIVE
                    logger.info(
                        "[FIB-TREND] 🚀 Rule 8 SELL TRIGGERED! Entry: $%.2f | SL: $%.2f (0.236) | TP: $%.2f (1.618)",
                        self.entry_price, self.sl_price, self.tp_price,
                    )
                    return {
                        "event": "TRADE_TRIGGERED",
                        "direction": "SHORT",
                        "entry": self.entry_price,
                        "sl": self.sl_price,
                        "tp": self.tp_price,
                    }
                if self.fib_0_236 and candle.high >= self.fib_0_236:
                    self.state = FibTrendState.INVALIDATED
                    self.invalidation_reason = "Price hit 0.236 before breakout occurred."
                    return {"event": "INVALIDATED", "reason": self.invalidation_reason}

            return None

        # State 5: Trade Active (2-Stage Take Profit & Breakeven Protection)
        if self.state == FibTrendState.TRADE_ACTIVE:
            if self.direction == SignalDirection.LONG:
                # Stage 1 TP: Check if price reached 1.000 (Swing 1 Peak)
                if not self.tp1_hit and self.fib_1_000 and candle.high >= self.fib_1_000:
                    self.tp1_hit = True
                    self.tp1_ts = candle.timestamp
                    self.tp1_price = self.fib_1_000
                    # Idea B: Lock Stop Loss to Breakeven (Entry Price + $0.20 buffer)
                    be_price = round(self.entry_price + 0.20, 2)
                    if self.sl_price is not None and self.sl_price < be_price:
                        self.sl_price = be_price
                    logger.info(
                        "[FIB-TREND] 🎯 TP1 Hit at $%.2f (1.000 Peak) → Stop Loss locked to Breakeven ($%.2f)",
                        self.fib_1_000, self.sl_price,
                    )

                # Stop Loss check (SL or Breakeven)
                if self.sl_price is not None and candle.low <= self.sl_price:
                    self.state = FibTrendState.COMPLETED
                    if self.tp1_hit:
                        self.outcome = "BREAKEVEN_CLOSED"
                        self.completion_reason = f"Trade closed at Breakeven (${self.sl_price:.2f}) with TP1 secured at ${self.tp1_price:.2f}"
                        logger.info("[FIB-TREND] 🛡 %s", self.completion_reason)
                    else:
                        self.outcome = "SL_HIT"
                        self.completion_reason = f"Stop Loss hit at {self.sl_price}"
                        logger.info("[FIB-TREND] %s", self.completion_reason)
                    return {"event": "COMPLETED", "outcome": self.outcome}

                # Stage 2 TP: Check if price reached 1.618 (Extension Target)
                if self.tp_price is not None and candle.high >= self.tp_price:
                    self.state = FibTrendState.COMPLETED
                    self.outcome = "TP_HIT"
                    self.completion_reason = f"TP2 (1.618 Extension) hit at {self.tp_price} — Full Profit Secured!"
                    logger.info("[FIB-TREND] 🏆 %s", self.completion_reason)
                    return {"event": "COMPLETED", "outcome": self.outcome}

            else:  # SHORT
                # Stage 1 TP: Check if price reached 1.000 (Swing 1 Valley)
                if not self.tp1_hit and self.fib_1_000 and candle.low <= self.fib_1_000:
                    self.tp1_hit = True
                    self.tp1_ts = candle.timestamp
                    self.tp1_price = self.fib_1_000
                    # Idea B: Lock Stop Loss to Breakeven (Entry Price - $0.20 buffer)
                    be_price = round(self.entry_price - 0.20, 2)
                    if self.sl_price is not None and self.sl_price > be_price:
                        self.sl_price = be_price
                    logger.info(
                        "[FIB-TREND] 🎯 TP1 Hit at $%.2f (1.000 Valley) → Stop Loss locked to Breakeven ($%.2f)",
                        self.fib_1_000, self.sl_price,
                    )

                # Stop Loss check (SL or Breakeven)
                if self.sl_price is not None and candle.high >= self.sl_price:
                    self.state = FibTrendState.COMPLETED
                    if self.tp1_hit:
                        self.outcome = "BREAKEVEN_CLOSED"
                        self.completion_reason = f"Trade closed at Breakeven (${self.sl_price:.2f}) with TP1 secured at ${self.tp1_price:.2f}"
                        logger.info("[FIB-TREND] 🛡 %s", self.completion_reason)
                    else:
                        self.outcome = "SL_HIT"
                        self.completion_reason = f"Stop Loss hit at {self.sl_price}"
                        logger.info("[FIB-TREND] %s", self.completion_reason)
                    return {"event": "COMPLETED", "outcome": self.outcome}

                # Stage 2 TP: Check if price reached 1.618 (Extension Target)
                if self.tp_price is not None and candle.low <= self.tp_price:
                    self.state = FibTrendState.COMPLETED
                    self.outcome = "TP_HIT"
                    self.completion_reason = f"TP2 (1.618 Extension) hit at {self.tp_price} — Full Profit Secured!"
                    logger.info("[FIB-TREND] 🏆 %s", self.completion_reason)
                    return {"event": "COMPLETED", "outcome": self.outcome}

        return None
