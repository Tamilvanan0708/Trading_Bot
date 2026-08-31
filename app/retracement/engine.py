"""
RETRACEMENT_BOS_V1 — Exact Bullish BOS Retracement Strategy Engine.

State machine:
  NO_SETUP -> BOS_DETECTED -> POINT_2_IDENTIFIED -> FIB_ACTIVE -> TP_DYNAMIC
  -> ENTRY_TOUCHED -> TP_FROZEN -> COMPLETED / INVALIDATED

Exact Fibonacci level mapping (NEVER swapped):
  1.618 -> BLACK LINE 1 (extension reference)
  1.000 -> TP (dynamic before entry, FROZEN after entry touch)
  0.618 -> ENTRY
  0.236 -> SL
  0.000 -> BLACK LINE 2 (Point 2 anchor)

Zero-look-ahead guarantee:
  - `process_candle` processes candles strictly chronologically.
  - `run_series` precomputes confirmed swing points once and then walks
    candles in order, exposing only swings confirmed at or before the current
    candle index (swing.index + right_bars <= current_index).
  - No future highs/lows/BOS/candles are ever used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import logger
from app.data.models import Candle
from app.indicators.swings import SwingPoint, detect_swings
from app.retracement.models import (
    STRATEGY_VERSION,
    RetracementEvent,
    RetracementEventType,
    RetracementSetup,
    RetracementState,
    utcnow,
)

# Minimum candles before swing detection is meaningful
_MIN_CANDLES = 30
# Right bars for swing confirmation (must match the swing detector)
_RIGHT_BARS = 3
# Left bars for swing detection
_LEFT_BARS = 3
# Online mode: recompute swings on a trailing window to bound cost
_ONLINE_WINDOW = 400

# ---------------------------------------------------------------------------
# Structural-validity guards (spec sections 3, 17, 26).
#
# These only ever REJECT a setup — they never force TP closer or remap the
# Fibonacci levels.  A rejected setup reports a structural recheck reason.
# ---------------------------------------------------------------------------
# Point 1 (the broken swing high) must not be a distant historical swing.
# If the last confirmed swing high is older than this, the BOS is AMBIGUOUS.
_MAX_BOS_LOOKBACK = 60
# Point 2's anchor swing low must belong to the same movement as the broken
# high.  If the leg between them exceeds this many candles, Point 2 is
# UNRELATED / AMBIGUOUS.
_MAX_BOS_LEG_CANDLES = 50
# If the total point range (1.000 - 0.000) is an outlier vs. recent candle
# ranges, the selected swing is UNRELATED / UNREALISTIC -> recheck structure.
_MAX_SETUP_RANGE_MULT = 50
_MEDIAN_RANGE_WINDOW = 50


def _make_event(
    setup: RetracementSetup,
    event_type: RetracementEventType,
    state_before: RetracementState,
    price: float | None = None,
    meta: dict[str, Any] | None = None,
) -> RetracementEvent:
    return RetracementEvent(
        setup_id=setup.setup_id,
        event_type=event_type,
        timestamp=utcnow(),
        price=price,
        state_before=state_before,
        state_after=setup.state,
        metadata=meta or {},
    )


def _insufficient_event(setup: RetracementSetup, state_before: RetracementState,
                        reason: str, price: float | None = None) -> RetracementEvent:
    setup.validation_passed = False
    setup.insufficient_structure_reason = reason
    setup.state = RetracementState.INVALIDATED
    return _make_event(setup, RetracementEventType.INSUFFICIENT_STRUCTURE,
                       state_before, price=price, meta={"reason": reason})


def median_candle_range(candles: list[Candle], window: int = _MEDIAN_RANGE_WINDOW) -> float:
    """Median high-low range of the most recent candles (volatility baseline).

    Used ONLY to reject structurally unrelated setups — never to move levels.
    """
    window_candles = candles[-window:]
    if not window_candles:
        return 0.0
    ranges = sorted(c.high - c.low for c in window_candles)
    n = len(ranges)
    return ranges[n // 2]


def _compute_fib_levels(point_2: float, high: float) -> dict[str, float]:
    """Compute the exact Fibonacci levels from the Point 2 anchor and the
    current valid high.

    For a bullish setup:
      range = high - point_2
      ratio r -> price = point_2 + r * range

    Ratios: 0.000, 0.236, 0.382, 0.500, 0.618, 1.000, 1.618
    """
    rng = high - point_2
    if rng <= 0:
        return {}
    return {
        "fib_0": point_2,
        "fib_0_236": point_2 + 0.236 * rng,
        "fib_0_382": point_2 + 0.382 * rng,
        "fib_0_500": point_2 + 0.500 * rng,
        "fib_0_618": point_2 + 0.618 * rng,
        "fib_1_000": high,
        "fib_1_618": point_2 + 1.618 * rng,
    }


def _apply_fib_to_setup(setup: RetracementSetup, levels: dict[str, float]) -> None:
    setup.fib_0 = levels.get("fib_0")
    setup.fib_0_236 = levels.get("fib_0_236")
    setup.fib_0_382 = levels.get("fib_0_382")
    setup.fib_0_500 = levels.get("fib_0_500")
    setup.fib_0_618 = levels.get("fib_0_618")
    setup.fib_1_000 = levels.get("fib_1_000")
    setup.fib_1_618 = levels.get("fib_1_618")
    setup.entry_price = setup.fib_0_618
    setup.sl_price = setup.fib_0_236
    setup.dynamic_tp = setup.fib_1_000


def _validate_level_order(setup: RetracementSetup) -> tuple[bool, str]:
    """Verify 0.000 < 0.236 < 0.382 < 0.500 < 0.618 < 1.000 < 1.618.

    Returns (valid, reason).  If invalid, the setup must NOT be generated.
    """
    prices = [setup.fib_0, setup.fib_0_236, setup.fib_0_382, setup.fib_0_500,
              setup.fib_0_618, setup.fib_1_000, setup.fib_1_618]
    if any(p is None for p in prices):
        return False, "INSUFFICIENT STRUCTURE — NO RETRACEMENT SETUP (incomplete levels)"
    for a, b in zip(prices, prices[1:]):
        if not (a < b):
            return False, f"INSUFFICIENT STRUCTURE — NO RETRACEMENT SETUP (level order violated: {a} >= {b})"
    return True, ""


class RetracementBOSEngine:
    """Deterministic bullish BOS retracement strategy engine."""

    def __init__(self, symbol: str = "XAUUSD", timeframe: str = "15m",
                 *, touch_tolerance: float = 0.0,
                 max_bos_lookback: int = _MAX_BOS_LOOKBACK,
                 max_bos_leg_candles: int = _MAX_BOS_LEG_CANDLES,
                 max_setup_range_mult: float = _MAX_SETUP_RANGE_MULT):
        self.symbol = symbol
        self.timeframe = timeframe
        self.touch_tolerance = touch_tolerance
        self.max_bos_lookback = max_bos_lookback
        self.max_bos_leg_candles = max_bos_leg_candles
        self.max_setup_range_mult = max_setup_range_mult
        self.setup: RetracementSetup | None = None
        self._candles: list[Candle] = []
        self._swings: list[SwingPoint] = []
        self._bos_ref_high: float | None = None
        self._bos_ref_high_idx: int | None = None
        self._events: list[RetracementEvent] = []
        self._batch = False
        self._current_index = -1

    @property
    def _cur_idx(self) -> int:
        """Index of the candle currently being processed."""
        if self._batch:
            return self._current_index
        return len(self._candles) - 1

    # ------------------------------------------------------------------
    # Public API — online mode
    # ------------------------------------------------------------------

    def process_candle(self, candle: Candle) -> list[RetracementEvent]:
        """Process one candle sequentially (online mode).

        Swing detection runs on a trailing window (bounded cost).  Returns the
        events generated.  Must be called chronologically.
        """
        self._candles.append(candle)
        events: list[RetracementEvent] = []

        if len(self._candles) < _MIN_CANDLES:
            return events

        window = self._candles[-_ONLINE_WINDOW:]
        self._swings = detect_swings(window, left_bars=_LEFT_BARS, right_bars=_RIGHT_BARS)
        # Rebase swing indices to the full-series coordinate system
        offset = len(self._candles) - len(window)
        for s in self._swings:
            s.index = s.index + offset

        # Confirmed swings visible at the current candle (respecting the
        # swing-confirmation delay so there is no look-ahead).
        confirmed_highs = [
            s for s in self._swings
            if s.point_type == "HIGH" and s.index + _RIGHT_BARS <= self._cur_idx
        ]
        confirmed_lows = [
            s for s in self._swings
            if s.point_type == "LOW" and s.index + _RIGHT_BARS <= self._cur_idx
        ]

        if self.setup is None:
            events.extend(self._detect_bos(candle, confirmed_highs=confirmed_highs,
                                           confirmed_lows=confirmed_lows))

        if self.setup is not None:
            events.extend(self._process_active_setup(candle, confirmed_highs=confirmed_highs))

        self._events.extend(events)
        return events

    # ------------------------------------------------------------------
    # Public API — batch mode (efficient for backtests/datasets)
    # ------------------------------------------------------------------

    def run_series(self, candles: list[Candle]) -> tuple[list[RetracementSetup], list[RetracementEvent]]:
        """Process a full series efficiently (O(n)).

        Precomputes all confirmed swings once, then walks candles in
        chronological order.  Returns (list of setups, full event list).
        No look-ahead: at candle i only swings with index + right_bars <= i
        are visible.
        """
        self._batch = True
        self._candles = list(candles)
        all_swings = detect_swings(self._candles, left_bars=_LEFT_BARS, right_bars=_RIGHT_BARS)

        # Precompute, for each candle index i, the last confirmed swing high/low
        # visible at that point (swing.index + right_bars <= i).
        n = len(self._candles)
        last_high_idx: list[int | None] = [None] * n
        last_low_idx: list[int | None] = [None] * n
        confirmed_highs: list[SwingPoint] = []
        confirmed_lows: list[SwingPoint] = []
        # events by confirmation index
        confirm_at: dict[int, list[SwingPoint]] = {}
        for s in all_swings:
            confirm_at.setdefault(s.index + _RIGHT_BARS, []).append(s)

        hi = None
        lo = None
        for i in range(n):
            for s in confirm_at.get(i, []):
                if s.point_type == "HIGH":
                    hi = s
                else:
                    lo = s
            last_high_idx[i] = hi.index if hi else None
            last_low_idx[i] = lo.index if lo else None

        setups: list[RetracementSetup] = []
        self.setup = None
        self._events = []
        self._bos_ref_high = None
        self._bos_ref_high_idx = None

        for i, candle in enumerate(self._candles):
            self._current_index = i
            if i < _MIN_CANDLES:
                continue
            # Confirmed swings visible at this candle
            confirmed_highs = [s for s in all_swings
                               if s.point_type == "HIGH" and s.index + _RIGHT_BARS <= i]
            confirmed_lows = [s for s in all_swings
                              if s.point_type == "LOW" and s.index + _RIGHT_BARS <= i]

            if self.setup is None:
                bos_events = self._detect_bos(candle, confirmed_highs=confirmed_highs,
                                              confirmed_lows=confirmed_lows)
                self._events.extend(bos_events)
            if self.setup is not None:
                active_events = self._process_active_setup(candle, confirmed_highs=confirmed_highs)
                self._events.extend(active_events)
                if self.setup.state in (RetracementState.COMPLETED, RetracementState.INVALIDATED):
                    setups.append(self.setup)
                    self.setup = None

        if self.setup is not None:
            setups.append(self.setup)

        self._batch = False
        return setups, list(self._events)

    # ------------------------------------------------------------------
    # Reset / restore
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.setup = None
        self._candles = []
        self._swings = []
        self._bos_ref_high = None
        self._bos_ref_high_idx = None
        self._events = []
        self._batch = False

    def restore_setup(self, setup: RetracementSetup) -> None:
        """Restore a persisted setup after restart (TP freeze must survive)."""
        self.setup = setup

    def archive_completed(self) -> RetracementSetup | None:
        """Online-mode lifecycle helper: if the current setup has completed or
        been invalidated, detach it and clear the engine so a NEW BOS can be
        detected on the next candle.

        This mirrors the batch-mode lifecycle (``run_series`` emits a completed
        setup and starts fresh).  It does NOT change any detection, Fibonacci,
        entry, SL/TP, or TP-freeze rule — the returned setup is a completed
        setup whose state/outcome were already produced by the engine.
        """
        if self.setup is not None and self.setup.state in (
            RetracementState.COMPLETED,
            RetracementState.INVALIDATED,
        ):
            completed = self.setup
            self.setup = None
            self._bos_ref_high = None
            self._bos_ref_high_idx = None
            return completed
        return None

    # ------------------------------------------------------------------
    # BOS Detection
    # ------------------------------------------------------------------

    def _detect_bos(self, candle: Candle, confirmed_highs: list[SwingPoint] | None = None,
                    confirmed_lows: list[SwingPoint] | None = None) -> list[RetracementEvent]:
        """Detect a bullish BOS: close > previous confirmed swing high.

        Conditions:
          - a confirmed swing high exists
          - candle.close > that swing high price
          - previous candle close <= that high (avoid double-counting)
          - the broken swing high is RECENT / structurally relevant
            (a distant historical swing is an AMBIGUOUS BOS -> rejected)
        """
        events: list[RetracementEvent] = []
        if confirmed_highs is None:
            confirmed_highs = [
                s for s in self._swings
                if s.point_type == "HIGH" and s.index + _RIGHT_BARS <= self._cur_idx
            ]
        if not confirmed_highs:
            return events

        last_sh = confirmed_highs[-1]
        prev_candle = self._candles[self._cur_idx - 1] if self._cur_idx >= 1 else None

        if candle.close > last_sh.price and (prev_candle is None or prev_candle.close <= last_sh.price):
            # Structural-validity guard: the broken high must not be a distant
            # historical swing (spec #3/#17/#26).  A BOS against an old high is
            # ambiguous and must NOT be turned into a setup.
            if self._cur_idx - last_sh.index > self.max_bos_lookback:
                self._bos_ref_high = None
                self._bos_ref_high_idx = None
                self.setup = RetracementSetup(
                    symbol=self.symbol, timeframe=self.timeframe,
                    state=RetracementState.BOS_DETECTED,
                )
                reason = (
                    "INSUFFICIENT STRUCTURE — NO RETRACEMENT SETUP "
                    "(AMBIGUOUS BOS: broken swing high is a distant historical "
                    f"swing {self._cur_idx - last_sh.index} candles old; recheck "
                    "BOS structure — do not force an unrelated setup)"
                )
                events.append(_insufficient_event(
                    self.setup, RetracementState.NO_SETUP, reason, price=candle.close))
                # self.setup is left as INVALIDATED so run_series collects it
                return events

            self._bos_ref_high = last_sh.price
            self._bos_ref_high_idx = last_sh.index
            self.setup = RetracementSetup(
                symbol=self.symbol, timeframe=self.timeframe,
                state=RetracementState.BOS_DETECTED,
            )
            # Point 1 = previous important swing high that price breaks (BOS break level)
            self.setup.point_1_price = last_sh.price
            self.setup.point_1_timestamp = last_sh.timestamp
            self.setup.bos_price = last_sh.price
            self.setup.bos_timestamp = last_sh.timestamp
            e = _make_event(self.setup, RetracementEventType.BOS_DETECTED,
                            RetracementState.NO_SETUP, price=candle.close,
                            meta={"point_1": last_sh.price, "bos_high_index": last_sh.index,
                                  "candle_index": self._cur_idx})
            events.append(e)

            events.extend(self._identify_point_2(candle, confirmed_lows or []))

        return events

    def _identify_point_2(self, candle: Candle, confirmed_lows: list[SwingPoint]) -> list[RetracementEvent]:
        """After bullish BOS, find Point 2 = the lowest low of the BOS move.

        The BOS move spans from the last confirmed swing low before the BOS
        reference high, up to and including the BOS candle.
        Point 2 = min(low) over that range.  ONLY candles up to the current
        candle are considered (no look-ahead).

        Structural guard: if the anchor swing low is too far from the broken
        high (an unrelated historical low), Point 2 is AMBIGUOUS -> reject.
        """
        if self.setup is None or self._bos_ref_high_idx is None:
            return []

        prior_lows = [s for s in confirmed_lows if s.index < self._bos_ref_high_idx]
        if not prior_lows:
            # No confirmed swing low before the BOS reference high — we cannot
            # confidently identify Point 2 as belonging to this BOS leg.
            # Per spec: return INSUFFICIENT STRUCTURE instead of inventing a low.
            reason = (
                "INSUFFICIENT STRUCTURE — NO RETRACEMENT SETUP "
                "(no confirmed swing low before BOS reference high — Point 2 ambiguous)"
            )
            return [_insufficient_event(self.setup, RetracementState.BOS_DETECTED, reason)]

        swing_low_idx = prior_lows[-1].index

        # Structural guard: Point 2 must belong to the SAME movement as the
        # broken high.  A leg longer than the configured bound means the anchor
        # swing low is an unrelated historical low (spec #17/#26).
        leg_candles = self._bos_ref_high_idx - swing_low_idx
        if leg_candles > self.max_bos_leg_candles:
            reason = (
                "INSUFFICIENT STRUCTURE — NO RETRACEMENT SETUP "
                "(Point 2 ambiguous: anchor swing low is "
                f"{leg_candles} candles before the broken high — unrelated "
                "historical swing; recheck BOS structure)"
            )
            return [_insufficient_event(self.setup, RetracementState.BOS_DETECTED, reason)]

        # BOS move = candles from swing_low_idx up to and including the BOS
        # candle (current index).  NEVER includes future candles.
        move_candles = self._candles[swing_low_idx:self._cur_idx + 1]
        point_2_low = min(c.low for c in move_candles)
        point_2_ts = next(c.timestamp for c in reversed(move_candles) if c.low == point_2_low)

        self.setup.point_2_price = point_2_low
        self.setup.point_2_timestamp = point_2_ts
        self.setup.current_high_price = self._bos_ref_high
        self.setup.current_high_timestamp = self._candles[self._cur_idx].timestamp
        self.setup.state = RetracementState.POINT_2_IDENTIFIED

        e = _make_event(self.setup, RetracementEventType.POINT_2_FOUND,
                        RetracementState.BOS_DETECTED, price=point_2_low,
                        meta={"swing_low_index": swing_low_idx,
                              "bos_high_index": self._bos_ref_high_idx})
        return [e] + self._activate_fib()

    def _activate_fib(self) -> list[RetracementEvent]:
        """Compute Fibonacci levels and transition to FIB_ACTIVE / TP_DYNAMIC.

        Before accepting the setup, validates:
          - level order (0.000 < 0.236 < 0.382 < 0.500 < 0.618 < 1.000 < 1.618)
          - the total point range is not an unrealistic outlier vs. recent
            volatility (an unrelated historical swing is rejected, NEVER
            "fixed" by moving TP closer or remapping levels)
        """
        if self.setup is None or self.setup.point_2_price is None or self.setup.current_high_price is None:
            return []
        levels = _compute_fib_levels(self.setup.point_2_price, self.setup.current_high_price)
        if not levels:
            return []
        _apply_fib_to_setup(self.setup, levels)

        valid, reason = _validate_level_order(self.setup)
        if not valid:
            return [_insufficient_event(self.setup, RetracementState.POINT_2_IDENTIFIED, reason)]

        # Unrealistic / unrelated setup size guard (spec #17): if the total
        # point range dwarfs the recent candle ranges, the selected low/high do
        # not belong to the same coherent movement.  We RECHECK the structure —
        # we never force TP closer or remap Fibonacci.
        median_range = median_candle_range(self._candles)
        total_range = levels["fib_1_000"] - levels["fib_0"]
        if median_range > 0 and total_range > self.max_setup_range_mult * median_range:
            ratio = total_range / median_range
            reason = (
                "INSUFFICIENT STRUCTURE — NO RETRACEMENT SETUP "
                "(UNRELATED OR UNREALISTIC SETUP: total point range "
                f"{total_range:.2f} is {ratio:.1f}x the median candle range "
                f"{median_range:.2f}; recheck BOS structure / Point 2 / the "
                "BOS-leg HIGH — an unrelated historical swing was likely used. "
                "Do NOT force TP closer.)"
            )
            return [_insufficient_event(self.setup, RetracementState.POINT_2_IDENTIFIED, reason)]

        self.setup.validation_passed = True
        self.setup.state = RetracementState.FIB_ACTIVE
        events = [_make_event(self.setup, RetracementEventType.SETUP_CREATED,
                              RetracementState.POINT_2_IDENTIFIED,
                              price=self.setup.current_high_price)]
        self.setup.state = RetracementState.TP_DYNAMIC
        events.append(_make_event(self.setup, RetracementEventType.NEW_VALID_HIGH,
                                  RetracementState.FIB_ACTIVE, price=self.setup.current_high_price))
        events.append(_make_event(self.setup, RetracementEventType.TP_UPDATED,
                                  RetracementState.TP_DYNAMIC, price=self.setup.dynamic_tp))
        return events

    # ------------------------------------------------------------------
    # Active Setup Processing
    # ------------------------------------------------------------------

    def _process_active_setup(self, candle: Candle,
                              confirmed_highs: list[SwingPoint] | None = None) -> list[RetracementEvent]:
        if self.setup is None:
            return []
        # A setup rejected as INSUFFICIENT_STRUCTURE (Point 2 not identified)
        # has no valid Fibonacci structure — nothing more to process.
        if self.setup.point_2_price is None:
            return []
        events: list[RetracementEvent] = []

        if self._check_invalidation(candle):
            self.setup.state = RetracementState.INVALIDATED
            self.setup.validation_passed = False
            events.append(_make_event(self.setup, RetracementEventType.INVALIDATED,
                                      RetracementState.TRADE_ACTIVE if self.setup.tp_locked
                                      else RetracementState.TP_DYNAMIC,
                                      price=candle.close,
                                      meta={"reason": self.setup.invalidation_reason}))
            return events

        if confirmed_highs is None:
            confirmed_highs = [
                s for s in self._swings
                if s.point_type == "HIGH" and s.index + _RIGHT_BARS <= self._cur_idx
            ]

        if not self.setup.entry_touched:
            # Before entry: dynamic TP updates on new valid highs
            events.extend(self._check_new_valid_high(candle, confirmed_highs))

            # Entry touch (price retraces to 0.618)
            if self.setup.touch_entry(candle.low, candle.high, self.touch_tolerance):
                self.setup.entry_touched = True
                self.setup.entry_timestamp = candle.timestamp
                self.setup.state = RetracementState.ENTRY_TOUCHED
                events.append(_make_event(self.setup, RetracementEventType.ENTRY_TOUCHED,
                                          RetracementState.TP_DYNAMIC, price=candle.close,
                                          meta={"entry_price": self.setup.entry_price}))

                # Freeze TP immediately at the CURRENT dynamic TP
                self.setup.tp_before_freeze = self.setup.dynamic_tp
                self.setup.tp_locked = True
                self.setup.locked_tp = self.setup.dynamic_tp
                self.setup.state = RetracementState.TP_FROZEN
                events.append(_make_event(self.setup, RetracementEventType.TP_LOCKED,
                                          RetracementState.ENTRY_TOUCHED,
                                          price=self.setup.locked_tp,
                                          meta={"locked_tp": self.setup.locked_tp}))
                # Trade is now active (monitoring the frozen TP / SL)
                self.setup.state = RetracementState.TRADE_ACTIVE
                events.append(_make_event(self.setup, RetracementEventType.COMPLETED,
                                          RetracementState.TP_FROZEN,
                                          price=self.setup.locked_tp,
                                          meta={"phase": "TRADE_ACTIVE"}))
        else:
            # After entry: TP is frozen; new highs are ignored (logged only)
            self._check_post_entry_new_highs(candle, confirmed_highs, events)

            # SL hit
            if self.setup.sl_price is not None and candle.low <= self.setup.sl_price:
                self.setup.state = RetracementState.COMPLETED
                self.setup.outcome = "SL_HIT"
                events.append(_make_event(self.setup, RetracementEventType.SL_HIT,
                                          RetracementState.TRADE_ACTIVE, price=candle.close,
                                          meta={"sl_price": self.setup.sl_price}))
                return events

            # TP hit (locked TP)
            if self.setup.locked_tp is not None and candle.high >= self.setup.locked_tp:
                self.setup.state = RetracementState.COMPLETED
                self.setup.outcome = "TP_HIT"
                events.append(_make_event(self.setup, RetracementEventType.TP_HIT,
                                          RetracementState.TRADE_ACTIVE, price=candle.close,
                                          meta={"locked_tp": self.setup.locked_tp}))
                return events

        return events

    def _check_new_valid_high(self, candle: Candle,
                              confirmed_highs: list[SwingPoint]) -> list[RetracementEvent]:
        """Before entry: a new confirmed swing high above the current high
        endpoint updates the dynamic TP (and 1.618 extension)."""
        if self.setup is None:
            return []
        events: list[RetracementEvent] = []
        if not confirmed_highs:
            return events
        last_sh = confirmed_highs[-1]
        cur_high = self.setup.current_high_price
        if cur_high is None or last_sh.price > cur_high:
            self.setup.current_high_price = last_sh.price
            self.setup.current_high_timestamp = last_sh.timestamp
            levels = _compute_fib_levels(self.setup.point_2_price, self.setup.current_high_price)
            if levels:
                _apply_fib_to_setup(self.setup, levels)
            events.append(_make_event(self.setup, RetracementEventType.NEW_VALID_HIGH,
                                      self.setup.state, price=last_sh.price,
                                      meta={"swing_index": last_sh.index}))
            events.append(_make_event(self.setup, RetracementEventType.TP_UPDATED,
                                      self.setup.state, price=self.setup.dynamic_tp))
        return events

    def _check_post_entry_new_highs(self, candle: Candle,
                                    confirmed_highs: list[SwingPoint],
                                    events: list[RetracementEvent]) -> None:
        """After entry: log new highs but NEVER change the locked TP."""
        if self.setup is None:
            return
        if not confirmed_highs:
            return
        last_sh = confirmed_highs[-1]
        cur_high = self.setup.current_high_price
        if cur_high is None or last_sh.price > cur_high:
            self.setup.current_high_price = last_sh.price
            self.setup.current_high_timestamp = last_sh.timestamp
            events.append(_make_event(self.setup, RetracementEventType.POST_ENTRY_HIGH_IGNORED,
                                      self.setup.state, price=last_sh.price,
                                      meta={"locked_tp": self.setup.locked_tp}))

    def _check_invalidation(self, candle: Candle) -> bool:
        """Invalidate when price closes below Point 2 (0.000 anchor)."""
        if self.setup is None:
            return False
        if self.setup.point_2_price is not None and candle.close < self.setup.point_2_price:
            self.setup.invalidation_reason = "Price closed below Point 2 (0.000 anchor)."
            return True
        return False


# ---------------------------------------------------------------------------
# User-facing report (spec #27)
# ---------------------------------------------------------------------------

def format_report(setup: RetracementSetup | None, live_price: float | None = None) -> str:
    """Produce the exact ASCII report format from spec section 27.

    Returns a human-readable string.  Returns ``"NO VALID RETRACEMENT SETUP"``
    when no setup or incomplete structure.
    """
    if setup is None or not setup.validation_passed:
        return "NO TRADE / INVALID RETRACEMENT"

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("RETRACEMENT SETUP DETECTED")
    lines.append("=" * 60)
    lines.append("")

    # BOS
    lines.append("BOS:")
    if setup.bos_price is not None:
        lines.append("  CONFIRMED")
        lines.append(f"  Point 1 (broken high): {setup.point_1_price}")
        lines.append(f"  BOS Level:             {setup.bos_price}")
    else:
        lines.append("  NOT CONFIRMED")
    lines.append("")

    # Point 2
    lines.append("POINT 2:")
    if setup.point_2_price is not None:
        lines.append("  IDENTIFIED")
        lines.append(f"  Price:  {setup.point_2_price}")
    else:
        lines.append("  NOT IDENTIFIED")
    lines.append("")

    # Fibonacci structure
    lines.append("FIB STRUCTURE:")
    levels = setup.level_dict()
    fib_labels = [
        ("1.618", "BLACK LINE 1"),
        ("1.000", "TP"),
        ("0.618", "ENTRY"),
        ("0.500", "FIB"),
        ("0.382", "FIB"),
        ("0.236", "SL"),
        ("0.000", "BLACK LINE 2"),
    ]
    for ratio, label in fib_labels:
        lvl = levels.get(ratio)
        if lvl and lvl.price is not None:
            lines.append(f"  {ratio} → {lvl.price:<12.4f} {label}")
    lines.append("")

    # Status
    lines.append("STATUS:")
    lines.append(f"  State:   {setup.spec_state()}")
    if setup.tp_locked:
        lines.append("  TP:      LOCKED")
    else:
        lines.append("  TP:      DYNAMIC — WAITING FOR ENTRY")
    lines.append(f"  Entry:   {setup.entry_status}")
    if live_price is not None:
        lines.append(f"  Price:   {live_price:.4f}")
    lines.append("")

    # TP lock info
    if setup.tp_locked and setup.locked_tp is not None:
        lines.append("TP LOCK VALUE:")
        lines.append(f"  {setup.locked_tp:.4f}")
        lines.append("  DO NOT UPDATE")
        lines.append("")

    # Point analysis
    pa = setup.point_analysis()
    lines.append("POINT ANALYSIS:")
    if pa["total_point_range"] is not None:
        lines.append(f"  Total Range:     {pa['total_point_range']:.2f} POINTS")
    if pa["entry_to_tp_points"] is not None:
        lines.append(f"  Entry -> TP:     {pa['entry_to_tp_points']:.2f} POINTS")
    if pa["entry_to_sl_points"] is not None:
        lines.append(f"  Entry -> SL:     {pa['entry_to_sl_points']:.2f} POINTS")
    lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)
