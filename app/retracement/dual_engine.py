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
 
3-TRANCHE SCALING SYSTEM (strict 0.01 lots each, max 0.03 lots):
   L1 @ 0.618: 0.01 lots, TP 1.000, SL 0.236
   L2 @ 0.500: 0.01 lots, TP 0.618, SL 0.236
   L3 @ 0.382: 0.01 lots, TP 0.618, SL 0.236
   Escape Plan: if price reaches 0.382 (all 3 filled) and bounces to 0.618,
   L2 and L3 hit TP, L1 closes at breakeven — no waiting for 1.000.
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

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m", left_bars: int | None = None, right_bars: int | None = None):
        self.symbol = symbol
        self.timeframe = timeframe
        # 2-bar fractal swings for low timeframes (5m/3m/1m) capture sharp local micro-structure
        default_bars = 2 if timeframe.lower() in ("5m", "1m", "3m") else 3
        self.left_bars = left_bars if left_bars is not None else default_bars
        self.right_bars = right_bars if right_bars is not None else default_bars
        self.setup: RetracementSetup | None = None
        self._candles: list[Candle] = []
        self._events: list[RetracementEvent] = []
        self._archived_setups: list[RetracementSetup] = []
        self._candles_since_bos: int = 0
        self._max_expiry_candles: int = 200

    def reset(self) -> None:
        self.setup = None
        self._candles = []
        self._events = []
        self._archived_setups = []
        self._candles_since_bos = 0

    def _anchor_lookback_bars(self) -> int:
        """Timeframe-aware anchor lookback.

        We isolate recent internal micro-structure on low timeframes (1m/3m/5m)
        to target 15-25 pt scalping moves, while higher timeframes keep macro swings:

          1m  → 15 bars  (15 min)
          3m  → 20 bars  (1 h)
          5m  → 25 bars  (2 h)
          15m → 60 bars  (15 h) — macro swing
          30m → 60 bars
          1h+ → 60 bars
        """
        tf = str(self.timeframe).lower()
        if tf == "1m":
            return 15
        if tf == "3m":
            return 20
        if tf == "5m":
            return 25
        return 60

    def process_candle(self, candle: Candle) -> list[RetracementEvent]:
        self._candles.append(candle)
        if len(self._candles) < 20:
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
            # Do NOT auto-archive or clear self.setup here — the caller
            # (live.py / multi_tf.py) needs to observe the completed state
            # so it can finalize the DB row.  The caller will call
            # archive_completed() after reading the state.
            pass

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

        # Timeframe-aware anchor lookback: low timeframes (1m/3m/5m) isolate
        # recent internal micro-structure, higher timeframes keep macro swings.
        lookback_bars = self._anchor_lookback_bars()

        # 1. Check Bullish BOS (Body Close > last confirmed swing high)
        if len(self._candles) >= 2 and self._candles[-2].close > last_sh.price:
            return []  # BOS was already confirmed on a prior candle
        if candle.close > last_sh.price and last_sh.index < len(self._candles) - 1:
            # Anchor Low: lowest confirmed swing low within lookback of the BOS swing
            lows_before_bos = [
                s for s in confirmed_lows
                if s.index <= last_sh.index and (last_sh.index - s.index) <= lookback_bars
            ]
            anchor_low = min(lows_before_bos, key=lambda s: s.price) if lows_before_bos else confirmed_lows[-1]
            if str(self.timeframe).lower() in ("1m", "3m", "5m") and (last_sh.price - anchor_low.price) > 25.0:
                recent_lows = [s for s in lows_before_bos if (last_sh.price - s.price) <= 30.0]
                if recent_lows:
                    anchor_low = recent_lows[-1]
                elif lows_before_bos:
                    anchor_low = lows_before_bos[-1]
            p2_low = anchor_low.price
            p2_ts = anchor_low.timestamp

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
            self._candles_since_bos = 0
            return [RetracementEvent(setup_id=setup.setup_id, event_type=RetracementEventType.BOS_DETECTED, state_before=RetracementState.NO_SETUP, state_after=RetracementState.TP_DYNAMIC, timestamp=candle.timestamp, price=candle.close)]

        # 2. Check Bearish BOS (Body Close < last confirmed swing low)
        if len(self._candles) >= 2 and self._candles[-2].close < last_sl.price:
            return []
        if candle.close < last_sl.price and last_sl.index < len(self._candles) - 1:
            # Anchor High: highest confirmed swing high within lookback of the BOS swing
            highs_before_bos = [
                s for s in confirmed_highs
                if s.index <= last_sl.index and (last_sl.index - s.index) <= lookback_bars
            ]
            anchor_high = max(highs_before_bos, key=lambda s: s.price) if highs_before_bos else confirmed_highs[-1]
            if str(self.timeframe).lower() in ("1m", "3m", "5m") and (anchor_high.price - last_sl.price) > 25.0:
                recent_highs = [s for s in highs_before_bos if (s.price - last_sl.price) <= 30.0]
                if recent_highs:
                    anchor_high = recent_highs[-1]
                elif highs_before_bos:
                    anchor_high = highs_before_bos[-1]
            p2_high = anchor_high.price
            p2_ts = anchor_high.timestamp

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
            self._candles_since_bos = 0
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

    def _detect_fresh_bos_if_available(self, candle: Candle, direction: str) -> list[RetracementEvent]:
        """Detect if a newer, sharper micro-BOS formed while waiting for entry.

        This prevents holding onto stale, oversized 40-120 point swings when fresh
        15-25 point micro-swings (stair-step breakouts) are active in the market.
        """
        setup = self.setup
        if setup is None or setup.layers:
            return []

        swings = detect_swings(self._candles, left_bars=self.left_bars, right_bars=self.right_bars)
        confirmed_highs = [s for s in swings if s.point_type == "HIGH" and s.index + self.right_bars <= len(self._candles) - 1]
        confirmed_lows = [s for s in swings if s.point_type == "LOW" and s.index + self.right_bars <= len(self._candles) - 1]

        if not confirmed_highs or not confirmed_lows:
            return []

        lookback_bars = self._anchor_lookback_bars()

        if direction == "LONG":
            last_sh = confirmed_highs[-1]
            if last_sh.timestamp > setup.bos_timestamp and last_sh.price > setup.bos_price:
                if len(self._candles) >= 2 and self._candles[-2].close > last_sh.price:
                    return []
                if candle.close > last_sh.price and last_sh.index < len(self._candles) - 1:
                    lows_before = [s for s in confirmed_lows if s.index <= last_sh.index and (last_sh.index - s.index) <= lookback_bars]
                    if not lows_before:
                        return []
                    anchor_low = lows_before[-1]
                    if (last_sh.price - anchor_low.price) < 5.0 and len(lows_before) > 1:
                        anchor_low = lows_before[-2]

                    new_setup = RetracementSetup(
                        symbol=self.symbol,
                        timeframe=self.timeframe,
                        direction="LONG",
                        state=RetracementState.TP_DYNAMIC,
                        point_1_price=last_sh.price,
                        point_1_timestamp=last_sh.timestamp,
                        bos_price=last_sh.price,
                        bos_timestamp=last_sh.timestamp,
                        point_2_price=anchor_low.price,
                        point_2_timestamp=anchor_low.timestamp,
                        current_high_price=candle.high,
                        current_high_timestamp=candle.timestamp,
                        dynamic_tp=candle.high,
                        validation_passed=True,
                    )
                    self._apply_bullish_fib(new_setup, anchor_low.price, candle.high)
                    self.setup = new_setup
                    self._candles_since_bos = 0
                    return [RetracementEvent(
                        setup_id=new_setup.setup_id,
                        event_type=RetracementEventType.BOS_DETECTED,
                        state_before=RetracementState.NO_SETUP,
                        state_after=RetracementState.TP_DYNAMIC,
                        timestamp=candle.timestamp,
                        price=candle.close,
                        metadata={"rollover": True, "reason": "Fresh micro BOS replacement"}
                    )]

        elif direction == "SHORT":
            last_sl = confirmed_lows[-1]
            if last_sl.timestamp > setup.bos_timestamp and last_sl.price < setup.bos_price:
                if len(self._candles) >= 2 and self._candles[-2].close < last_sl.price:
                    return []
                if candle.close < last_sl.price and last_sl.index < len(self._candles) - 1:
                    highs_before = [s for s in confirmed_highs if s.index <= last_sl.index and (last_sl.index - s.index) <= lookback_bars]
                    if not highs_before:
                        return []
                    anchor_high = highs_before[-1]
                    if (anchor_high.price - last_sl.price) < 5.0 and len(highs_before) > 1:
                        anchor_high = highs_before[-2]

                    new_setup = RetracementSetup(
                        symbol=self.symbol,
                        timeframe=self.timeframe,
                        direction="SHORT",
                        state=RetracementState.TP_DYNAMIC,
                        point_1_price=last_sl.price,
                        point_1_timestamp=last_sl.timestamp,
                        bos_price=last_sl.price,
                        bos_timestamp=last_sl.timestamp,
                        point_2_price=anchor_high.price,
                        point_2_timestamp=anchor_high.timestamp,
                        current_high_price=candle.low,
                        current_high_timestamp=candle.timestamp,
                        dynamic_tp=candle.low,
                        validation_passed=True,
                    )
                    self._apply_bearish_fib(new_setup, anchor_high.price, candle.low)
                    self.setup = new_setup
                    self._candles_since_bos = 0
                    return [RetracementEvent(
                        setup_id=new_setup.setup_id,
                        event_type=RetracementEventType.BOS_DETECTED,
                        state_before=RetracementState.NO_SETUP,
                        state_after=RetracementState.TP_DYNAMIC,
                        timestamp=candle.timestamp,
                        price=candle.close,
                        metadata={"rollover": True, "reason": "Fresh micro BOS replacement"}
                    )]

        return []

    def _track_and_check_entry(self, candle: Candle) -> list[RetracementEvent]:
        setup = self.setup
        if setup is None:
            return []
        events: list[RetracementEvent] = []

        # Time-stop: invalidate the setup if the entry is never touched
        # within the expiry window (mirrors the SMC engine's max expiry).
        self._candles_since_bos += 1
        if self._candles_since_bos > self._max_expiry_candles:
            setup.state = RetracementState.INVALIDATED
            setup.invalidation_reason = f"Setup expired after {self._max_expiry_candles} candles without entry touch."
            return events

        # As the impulse wave expands higher/lower, dynamically update Target 1.000 (Keep Anchor locked!)

        if setup.direction == "LONG":
            # 1. Check if a fresh micro-BOS formed while waiting for entry
            if not setup.layers:
                fresh_events = self._detect_fresh_bos_if_available(candle, "LONG")
                if fresh_events:
                    setup.state = RetracementState.INVALIDATED
                    setup.invalidation_reason = "Superceded by fresh recent micro BOS."
                    self._archived_setups.append(setup)
                    return fresh_events

                # Update dynamic target if new high forms (before any layer fills)
                if candle.high > (setup.current_high_price or 0.0):
                    setup.current_high_price = candle.high
                    setup.current_high_timestamp = candle.timestamp
                    # Bounded span for scalping: roll anchor up if span > 25 pts and a higher swing low exists
                    if str(self.timeframe).lower() in ("1m", "3m", "5m") and (candle.high - setup.point_2_price) > 25.0:
                        swings = detect_swings(self._candles, left_bars=self.left_bars, right_bars=self.right_bars)
                        c_lows = [s for s in swings if s.point_type == "LOW" and s.index + self.right_bars <= len(self._candles) - 1]
                        higher_lows = [s for s in c_lows if s.timestamp > setup.point_2_timestamp and s.price > setup.point_2_price and (candle.high - s.price) >= 8.0]
                        if higher_lows:
                            setup.point_2_price = higher_lows[-1].price
                            setup.point_2_timestamp = higher_lows[-1].timestamp
                    self._apply_bullish_fib(setup, setup.point_2_price, candle.high)

            # 2. Fill layers on pullback touch (3-Tranche Scaling System)
            #    L1 @ 0.618, L2 @ 0.500, L3 @ 0.382 — all SL @ 0.236.
            new_fills = self._fill_long_layers(candle)
            for layer in new_fills:
                events.append(RetracementEvent(
                    setup_id=setup.setup_id,
                    event_type=RetracementEventType.ENTRY_TOUCHED,
                    state_before=RetracementState.TP_DYNAMIC,
                    state_after=RetracementState.TRADE_ACTIVE,
                    timestamp=candle.timestamp,
                    price=layer["entry_price"],
                    metadata={"layer": layer["layer"], "lots": layer["lots"]},
                ))

            # 3. Same-candle SL violation: any fill that also broke 0.236 is invalidated.
            if setup.layers and setup.sl_price is not None and candle.low <= setup.sl_price:
                setup.state = RetracementState.INVALIDATED
                setup.invalidation_reason = "Price breached Stop Loss (0.236) on entry candle."
                return events

            if setup.layers:
                setup.entry_touched = True
                setup.entry_timestamp = candle.timestamp
                if len(setup.layers) >= 3 or not new_fills:
                    setup.state = RetracementState.TRADE_ACTIVE
        else:
            # 1. Check if a fresh micro-BOS formed while waiting for entry
            if not setup.layers:
                fresh_events = self._detect_fresh_bos_if_available(candle, "SHORT")
                if fresh_events:
                    setup.state = RetracementState.INVALIDATED
                    setup.invalidation_reason = "Superceded by fresh recent micro BOS."
                    self._archived_setups.append(setup)
                    return fresh_events

                # Update dynamic target if new low forms (before any layer fills)
                if candle.low < (setup.current_high_price or float("inf")):
                    setup.current_high_price = candle.low
                    setup.current_high_timestamp = candle.timestamp
                    # Bounded span for scalping: roll anchor down if span > 25 pts and a lower swing high exists
                    if str(self.timeframe).lower() in ("1m", "3m", "5m") and (setup.point_2_price - candle.low) > 25.0:
                        swings = detect_swings(self._candles, left_bars=self.left_bars, right_bars=self.right_bars)
                        c_highs = [s for s in swings if s.point_type == "HIGH" and s.index + self.right_bars <= len(self._candles) - 1]
                        lower_highs = [s for s in c_highs if s.timestamp > setup.point_2_timestamp and s.price < setup.point_2_price and (s.price - candle.low) >= 8.0]
                        if lower_highs:
                            setup.point_2_price = lower_highs[-1].price
                            setup.point_2_timestamp = lower_highs[-1].timestamp
                    self._apply_bearish_fib(setup, setup.point_2_price, candle.low)

            # 2. Fill layers on pullback touch (SHORT: price rallies UP to the level)
            new_fills = self._fill_short_layers(candle)
            for layer in new_fills:
                events.append(RetracementEvent(
                    setup_id=setup.setup_id,
                    event_type=RetracementEventType.ENTRY_TOUCHED,
                    state_before=RetracementState.TP_DYNAMIC,
                    state_after=RetracementState.TRADE_ACTIVE,
                    timestamp=candle.timestamp,
                    price=layer["entry_price"],
                    metadata={"layer": layer["layer"], "lots": layer["lots"]},
                ))

            # 3. Same-candle SL violation
            if setup.layers and setup.sl_price is not None and candle.high >= setup.sl_price:
                setup.state = RetracementState.INVALIDATED
                setup.invalidation_reason = "Price breached Stop Loss (0.236) on entry candle."
                return events

            if setup.layers:
                setup.entry_touched = True
                setup.entry_timestamp = candle.timestamp
                # Keep state as TP_DYNAMIC so deeper layers can fill on the next candle.
                # state will transition to TRADE_ACTIVE only after all 3 layers fill
                # or the candle has no new fills.
                if len(setup.layers) >= 3 or not new_fills:
                    setup.state = RetracementState.TRADE_ACTIVE
                # Same-candle TP check: if a layer was just filled and the same candle
                # also reaches the layer's TP, mark it TP_HIT immediately.
                for layer in setup.layers.values():
                    if layer["state"] != "FILLED":
                        continue
                    if setup.direction == "LONG" and candle.high >= layer["tp"]:
                        layer["state"] = "TP_HIT"
                    elif setup.direction == "SHORT" and candle.low <= layer["tp"]:
                        layer["state"] = "TP_HIT"

        return events

    def _fill_long_layers(self, candle: Candle) -> list[dict]:
        """Fill L1/L2/L3 for a bullish setup as price retraces DOWN the grid."""
        setup = self.setup
        fills = []
        for layer, ratio, tp_ratio in (
            ("L1", 0.618, 1.000),
            ("L2", 0.500, 0.618),
            ("L3", 0.382, 0.618),
        ):
            if layer in setup.layers:
                continue  # already filled
            attr = f"fib_{ratio:.3f}".replace(".", "_")
            entry_level = getattr(setup, attr, None)
            if entry_level is None:
                continue
            if candle.low <= entry_level:
                if layer == "L1":
                    tp = setup.fib_1_000
                else:
                    tp = setup.fib_0_618
                layer_info = {
                    "layer": layer,
                    "entry_ratio": ratio,
                    "entry_price": round(entry_level, 2),
                    "tp": round(tp, 2),
                    "sl": round(setup.sl_price, 2) if setup.sl_price else None,
                    "lots": 0.01,
                    "state": "FILLED",
                    "filled_at": candle.timestamp.isoformat(),
                }
                setup.layers[layer] = layer_info
                fills.append(layer_info)
        return fills

    def _fill_short_layers(self, candle: Candle) -> list[dict]:
        """Fill L1/L2/L3 for a bearish setup as price retraces UP the grid."""
        setup = self.setup
        fills = []
        for layer, ratio, tp_ratio in (
            ("L1", 0.618, 1.000),
            ("L2", 0.500, 0.618),
            ("L3", 0.382, 0.618),
        ):
            if layer in setup.layers:
                continue
            attr = f"fib_{ratio:.3f}".replace(".", "_")
            entry_level = getattr(setup, attr, None)
            if entry_level is None:
                continue
            if candle.high >= entry_level:
                if layer == "L1":
                    tp = setup.fib_1_000
                else:
                    tp = setup.fib_0_618
                layer_info = {
                    "layer": layer,
                    "entry_ratio": ratio,
                    "entry_price": round(entry_level, 2),
                    "tp": round(tp, 2),
                    "sl": round(setup.sl_price, 2) if setup.sl_price else None,
                    "lots": 0.01,
                    "state": "FILLED",
                    "filled_at": candle.timestamp.isoformat(),
                }
                setup.layers[layer] = layer_info
                fills.append(layer_info)
        return fills

    def _track_active_trade(self, candle: Candle) -> list[RetracementEvent]:
        setup = self.setup
        if setup is None:
            return []
        events: list[RetracementEvent] = []

        # Freeze the L1 TP (1.000) on the first layer fill if not already locked.
        if setup.layers and "L1" in setup.layers:
            l1 = setup.layers["L1"]
            if not l1.get("locked_tp"):
                l1["locked_tp"] = l1["tp"]
                setup.tp_before_freeze = setup.dynamic_tp
                setup.locked_tp = l1["tp"]
                setup.tp_locked = True

        # ESCAPE PLAN: all 3 layers filled (price reached 0.382) and price
        # bounces back to 0.618 → L2/L3 hit TP, L1 closes at breakeven.
        all_filled = {"L1", "L2", "L3"}.issubset(setup.layers.keys())
        if all_filled and setup.escape_armed is False:
            setup.escape_armed = True
        if setup.escape_armed:
            escape_level = setup.fib_0_618
            if escape_level is not None:
                if setup.direction == "LONG" and candle.high >= escape_level:
                    setup.state = RetracementState.COMPLETED
                    setup.outcome = "ESCAPE"
                    setup.completion_reason = (
                        f"Escape plan: all layers filled, price returned to 0.618 — "
                        f"L2/L3 TP hit, L1 closed at breakeven."
                    )
                    for layer in setup.layers.values():
                        layer["state"] = "ESCAPE_CLOSED"
                    events.append(RetracementEvent(
                        setup_id=setup.setup_id,
                        event_type=RetracementEventType.COMPLETED,
                        state_before=RetracementState.TRADE_ACTIVE,
                        state_after=RetracementState.COMPLETED,
                        timestamp=candle.timestamp,
                        price=escape_level,
                        metadata={"reason": "ESCAPE_PLAN"},
                    ))
                    return events
                elif setup.direction == "SHORT" and candle.low <= escape_level:
                    setup.state = RetracementState.COMPLETED
                    setup.outcome = "ESCAPE"
                    setup.completion_reason = (
                        f"Escape plan: all layers filled, price returned to 0.618 — "
                        f"L2/L3 TP hit, L1 closed at breakeven."
                    )
                    for layer in setup.layers.values():
                        layer["state"] = "ESCAPE_CLOSED"
                    events.append(RetracementEvent(
                        setup_id=setup.setup_id,
                        event_type=RetracementEventType.COMPLETED,
                        state_before=RetracementState.TRADE_ACTIVE,
                        state_after=RetracementState.COMPLETED,
                        timestamp=candle.timestamp,
                        price=escape_level,
                        metadata={"reason": "ESCAPE_PLAN"},
                    ))
                    return events

        # SL: shared stop at 0.236 — stops out ALL open layers.
        if setup.sl_price is not None:
            sl_hit = (candle.low <= setup.sl_price) if setup.direction == "LONG" else (candle.high >= setup.sl_price)
            if sl_hit:
                setup.state = RetracementState.COMPLETED
                setup.outcome = "SL_HIT"
                setup.completion_reason = f"Stop Loss hit at {setup.sl_price}"
                for layer in setup.layers.values():
                    if layer["state"] == "FILLED":
                        layer["state"] = "SL_HIT"
                events.append(RetracementEvent(
                    setup_id=setup.setup_id,
                    event_type=RetracementEventType.SL_HIT,
                    state_before=RetracementState.TRADE_ACTIVE,
                    state_after=RetracementState.COMPLETED,
                    timestamp=candle.timestamp,
                    price=setup.sl_price,
                ))
                return events

        # Individual TP checks per layer.
        if setup.direction == "LONG":
            for layer in setup.layers.values():
                if layer["state"] != "FILLED" or layer.get("tp") is None:
                    continue
                if candle.high >= layer["tp"]:
                    layer["state"] = "TP_HIT"
                    # Breakeven Shield: When L2 or L3 hits TP at 0.618, lock L1 Stop Loss to Breakeven (0.618)
                    if layer.get("layer") in ("L2", "L3") and "L1" in setup.layers and setup.layers["L1"]["state"] == "FILLED":
                        if setup.fib_0_618 is not None:
                            setup.layers["L1"]["sl"] = setup.fib_0_618
                            setup.sl_price = setup.fib_0_618
                            logger.info("[BREAKEVEN SHIELD] Locked L1 Stop Loss to Breakeven (0.618: %s)", setup.fib_0_618)
        else:
            for layer in setup.layers.values():
                if layer["state"] != "FILLED" or layer.get("tp") is None:
                    continue
                if candle.low <= layer["tp"]:
                    layer["state"] = "TP_HIT"
                    # Breakeven Shield: When L2 or L3 hits TP at 0.618, lock L1 Stop Loss to Breakeven (0.618)
                    if layer.get("layer") in ("L2", "L3") and "L1" in setup.layers and setup.layers["L1"]["state"] == "FILLED":
                        if setup.fib_0_618 is not None:
                            setup.layers["L1"]["sl"] = setup.fib_0_618
                            setup.sl_price = setup.fib_0_618
                            logger.info("[BREAKEVEN SHIELD] Locked L1 Stop Loss to Breakeven (0.618: %s)", setup.fib_0_618)

        # Setup completes only when EVERY filled layer has resolved (TP/SL/escape).
        open_layers = [l for l in setup.layers.values() if l["state"] == "FILLED"]
        if not open_layers and setup.layers:
            setup.state = RetracementState.COMPLETED
            setup.outcome = "TP_HIT"
            setup.completion_reason = "All layers reached their take profit targets."
            events.append(RetracementEvent(
                setup_id=setup.setup_id,
                event_type=RetracementEventType.TP_HIT,
                state_before=RetracementState.TRADE_ACTIVE,
                state_after=RetracementState.COMPLETED,
                timestamp=candle.timestamp,
                price=setup.locked_tp,
            ))

        return events

    def archive_completed(self) -> RetracementSetup | None:
        if self.setup is not None and self.setup.state in (RetracementState.COMPLETED, RetracementState.INVALIDATED):
            completed = self.setup
            self.setup = None
            return completed
        return None
