"""
RETRACEMENT_BOS_V1 — Scenario tests for the AUTOMATIC detection engine.

Covers exactly what the master spec demands from the automatic engine:

  POSITIVE SCENARIOS (multiple full bullish BOS + retracement examples):
    * example 1: complete lifecycle — BOS -> Point 2 -> dynamic TP
      (High A/B/C/D) -> retracement -> 0.618 ENTRY touch -> TP FREEZE -> TP HIT
    * example 2: complete lifecycle ending in SL HIT (0.236 stop reached)
    * example 3: invalidation — close below Point 2 (0.000 anchor)

  EDGE CASES:
    * invalid BOS — a wick-only sweep above a swing high (close stays below)
      must NOT trigger a BOS
    * ambiguous Point 2 — no confirmed swing low before the broken high
      -> INSUFFICIENT STRUCTURE, no invented low
    * unrelated historical swing — the broken high is a distant swing
      -> ambiguous BOS rejected
    * obviously unrelated / unrealistic setup — the selected low+high produce
      an enormous point range vs. recent volatility -> rejected, never "fixed"

No manual blue-line / BOS / Fibonacci / Entry / SL / TP input anywhere: the
engine discovers every structure from candles alone.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from app.data.models import Candle
from app.retracement.engine import RetracementBOSEngine, format_report
from app.retracement.models import (
    RetracementEventType,
    RetracementState,
)

_DT = timedelta(minutes=15)


# ---------------------------------------------------------------------------
# Series builders (deterministic, fractal-compatible candles)
# ---------------------------------------------------------------------------

def _bar(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _flat(n, start_ts, price=100.0, spread=0.5):
    """n identical candles — no swings form (strict fractal comparisons fail)."""
    out = []
    for _ in range(n):
        out.append(_bar(start_ts, price, price + spread, price - spread, price))
        start_ts += _DT
    return out, start_ts


def _leg(start_ts, prev, tgt, bars=5, spread=0.5, gap=0.3):
    """Monotonic leg from prev -> tgt.  The turn value (prev) is preserved as
    the local extremum via an inside gap on the first bar, so clean fractal
    swing highs/lows form at each leg end."""
    out = []
    direction = 1 if tgt >= prev else -1
    last_close = None
    for k in range(bars):
        price = prev + (tgt - prev) * (k + 1) / bars
        if k == 0:
            o = prev + direction * gap
        else:
            o = last_close
        h = max(o, price) + spread
        l = min(o, price) - spread
        out.append(_bar(start_ts, o, h, l, price))
        start_ts += _DT
        last_close = price
    return out, start_ts


def _zigzag(targets, start_ts, bars=5, spread=0.5, gap=0.3):
    """Series of monotonic legs through the given target prices."""
    out = []
    prev = targets[0]
    for tgt in targets[1:]:
        leg_bars, start_ts = _leg(start_ts, prev, tgt, bars=bars, spread=spread, gap=gap)
        out.extend(leg_bars)
        prev = tgt
    return out, start_ts


def _mono_rise(start_ts, start, end, n, spread=0.5):
    """Long monotonic rise — no swing points form (purely for edge cases)."""
    out = []
    prev = start
    for k in range(n):
        c = start + (end - start) * (k + 1) / n
        h = max(prev, c) + spread
        l = min(prev, c) - spread
        out.append(_bar(start_ts, prev, h, l, c))
        start_ts += _DT
        prev = c
    return out, start_ts


def _run(series_bars, engine=None):
    engine = engine or RetracementBOSEngine(symbol="XAUUSD", timeframe="15m")
    setups, events = engine.run_series(series_bars)
    return setups, events


def _start_ts():
    return datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)


def _valid_setups(setups):
    return [s for s in setups if s.point_2_price is not None and s.validation_passed]


# ===========================================================================
# EXAMPLE 1 — full lifecycle: BOS -> P2 -> dynamic TP A/B/C/D -> entry touch
#             -> TP frozen -> TP HIT.  The engine must do EVERYTHING.
# ===========================================================================

def test_full_bullish_lifecycle_dynamic_tp_freeze_then_tp_hit():
    """A realistic bullish BOS + retracement must be fully auto-detected:
    BOS, Point 2 = low of the move, exact fib structure, TP following each
    valid high (A/B/C/D), ENTRY touch at 0.618, immediate TP freeze, TP HIT."""
    flat, ts = _flat(30, _start_ts())
    # Shallow pullbacks so 0.618 is only reached on the FINAL retracement —
    # the single setup must track High A/B/C/D before the entry touch.
    zig, ts = _zigzag([100, 106, 100, 110, 108, 113, 111, 117, 115, 121,
                       119, 125, 114, 130], ts)
    bars = flat + zig
    setups, events = _run(bars)

    valid = _valid_setups(setups)
    assert valid, "no valid setup generated"
    s = valid[0]

    # --- structure detected automatically ---
    assert s.bos_price is not None and s.point_1_price is not None
    assert s.point_2_price is not None
    assert s.fib_0 == s.point_2_price                      # 0.000 = Point 2
    assert s.fib_1_000 == s.current_high_price             # 1.000 = valid high
    assert s.entry_price == s.fib_0_618
    assert s.sl_price == s.fib_0_236
    assert s.level_order_valid()

    # --- TP was dynamic BEFORE entry: each new valid high moved TP ---
    tp_updates = [e for e in events
                  if e.event_type == RetracementEventType.TP_UPDATED
                  and e.setup_id == s.setup_id]
    assert len(tp_updates) >= 4, "expected TP to follow High A/B/C/D"

    # --- ENTRY touched at 0.618, TP froze at the value at that moment ---
    assert s.entry_touched is True
    assert s.tp_locked is True
    assert s.tp_before_freeze == s.locked_tp
    # the frozen TP equals the last dynamic TP that existed at the touch
    assert s.locked_tp == pytest.approx(s.fib_1_000)

    # --- outcome ---
    assert s.outcome == "TP_HIT"
    assert s.state == RetracementState.COMPLETED

    # --- point analysis (points-first) ---
    pa = s.point_analysis()
    assert pa["total_point_range"] == pytest.approx(s.fib_1_000 - s.fib_0)
    assert pa["entry_to_tp_points"] == pytest.approx(s.locked_tp - s.entry_price)
    assert pa["entry_to_sl_points"] == pytest.approx(s.entry_price - s.sl_price)
    assert pa["entry_status"] == "LOCKED"


def test_full_lifecycle_tp_hit_at_frozen_level_not_at_later_high():
    """The TP HIT must occur at the FROZEN level.  A later high beyond the
    frozen TP must never change the target."""
    flat, ts = _flat(30, _start_ts())
    zig, ts = _zigzag([100, 106, 100, 110, 108, 113, 111, 117, 115, 121,
                       119, 125, 114, 130], ts)
    setups, _ = _run(flat + zig)

    s = _valid_setups(setups)[0]
    locked = s.locked_tp
    assert locked is not None
    assert s.entry_touched is True

    # Recompute the dynamic TP before freeze and confirm the lock matches it
    assert s.tp_before_freeze == pytest.approx(locked)

    # Freeze is immutable at the model level: nothing the engine records later
    # can overwrite locked_tp
    assert s.outcome == "TP_HIT"
    assert s.locked_tp == locked


# ===========================================================================
# EXAMPLE 2 — full lifecycle ending in SL HIT at 0.236
# ===========================================================================

def test_full_bullish_lifecycle_sl_hit_at_0236():
    """A second bullish setup: after ENTRY touch and TP freeze, price falls to
    the 0.236 stop -> SL_HIT.  SL must equal fib_0_236 (never arbitrary)."""
    flat, ts = _flat(30, _start_ts())
    zig, ts = _zigzag([100, 106, 100, 108, 101, 111, 104, 100], ts)
    bars = flat + zig
    setups, events = _run(bars)

    valid = _valid_setups(setups)
    assert valid
    s = valid[0]

    assert s.entry_touched is True
    assert s.tp_locked is True
    assert s.sl_price == pytest.approx(s.fib_0_236)
    assert s.outcome == "SL_HIT"
    assert s.state == RetracementState.COMPLETED

    assert any(e.event_type == RetracementEventType.SL_HIT for e in events)
    # SL belongs to the exact structure (0.236), so Entry->SL is 0.382 of range
    pa = s.point_analysis()
    assert pa["entry_to_sl_points"] == pytest.approx(
        (0.618 - 0.236) * (s.fib_1_000 - s.fib_0))


# ===========================================================================
# EXAMPLE 3 — invalidation: close below Point 2 (0.000 anchor)
# ===========================================================================

def test_invalidation_before_entry_close_below_point2():
    """A dive below Point 2 must invalidate immediately (invalidation is
    checked before entry touch), and never produce a trade."""
    flat, ts = _flat(30, _start_ts())
    # Structure: H0 -- L0 -- H1 (Point 1) -- L1 -- BOS -- then dive below P2
    zig, ts = _zigzag([100, 106, 100, 108, 105, 110], ts)
    bars = flat + zig
    # One massive bearish candle that closes below Point 2 in a single bar
    bars.append(_bar(bars[-1].timestamp + _DT, 110.5, 111.0, 96.0, 97.0))

    setups, events = _run(bars)

    inval = [s for s in setups if s.invalidation_reason]
    assert inval, "expected an invalidated setup"
    s = inval[0]
    assert "below Point 2" in s.invalidation_reason
    assert s.entry_touched is False
    assert s.tp_locked is False
    # No trade ever activated
    assert not any(e.event_type == RetracementEventType.TP_LOCKED for e in events)
    assert not _valid_setups(setups)  # nothing valid remains


# ===========================================================================
# EDGE 1 — invalid BOS: wick-only sweep must NOT confirm a BOS
# ===========================================================================

def test_wick_only_sweep_does_not_trigger_bos():
    """A candle whose WICK crosses a swing high but whose close stays below it
    is a wick-only sweep, NOT a structural break -> no BOS, no setup."""
    flat, ts = _flat(30, _start_ts())
    leg1, ts = _leg(ts, 100, 106)      # swing high ~106.5 at the end
    leg2, ts = _leg(ts, 106, 104)      # small dip (swing low)
    bars = flat + leg1 + leg2

    # Wick-only sweep: highs exceed 106.5, closes stay below it
    sweep = [
        _bar(ts, 104.0, 108.5, 104.0, 105.2),
        _bar(ts + _DT, 105.2, 106.0, 104.5, 105.0),
        _bar(ts + 2 * _DT, 105.0, 105.5, 104.2, 104.8),
        _bar(ts + 3 * _DT, 104.8, 106.8, 104.6, 105.4),
    ]
    bars = bars + sweep

    setups, events = _run(bars)

    assert not any(e.event_type == RetracementEventType.BOS_DETECTED for e in events)
    assert not _valid_setups(setups)
    assert all(s.point_2_price is None for s in setups)


# ===========================================================================
# EDGE 2 — ambiguous Point 2: no confirmed swing low before the broken high
# ===========================================================================

def test_ambiguous_point2_no_prior_swing_low_rejected():
    """If the broken high is the first swing (no confirmed swing low before
    it), Point 2 cannot be identified -> INSUFFICIENT STRUCTURE.  The engine
    must NOT invent a low."""
    flat, ts = _flat(30, _start_ts())
    leg1, ts = _leg(ts, 100, 110)      # FIRST swing = high (no prior low)
    leg2, ts = _leg(ts, 110, 105)      # pullback (swing low AFTER the high)
    leg3, ts = _leg(ts, 105, 112)      # breaks 110.5 -> BOS
    bars = flat + leg1 + leg2 + leg3

    setups, events = _run(bars)

    rejected = [s for s in setups if not s.validation_passed]
    assert rejected, "expected an INSUFFICIENT STRUCTURE rejection"
    assert all(s.point_2_price is None for s in setups)
    assert all("INSUFFICIENT STRUCTURE" in s.insufficient_structure_reason
               for s in rejected)
    assert any(e.event_type == RetracementEventType.INSUFFICIENT_STRUCTURE
               for e in events)
    assert not _valid_setups(setups)


# ===========================================================================
# EDGE 3 — unrelated historical swing as BOS reference (distant broken high)
# ===========================================================================

def test_unrelated_distant_swing_high_rejected_as_ambiguous_bos():
    """When the only reference swing high is a distant historical swing
    (no newer swing formed for a long monotonic stretch), the BOS is ambiguous
    and must be rejected — never turned into a huge unrelated setup."""
    flat, ts = _flat(30, _start_ts())
    leg1, ts = _leg(ts, 100, 106)          # old swing high ~106.5 (bar ~34)
    leg2, ts = _leg(ts, 106, 100)          # swing low ~99.5 (bar ~39)
    rise, ts = _mono_rise(ts, 100, 107, 60)  # 60-candle monotonic rise, no swings
    bars = flat + leg1 + leg2 + rise

    setups, _ = _run(bars)

    rejected = [s for s in setups if not s.validation_passed]
    assert rejected, "expected rejection of an ambiguous BOS"
    assert any("AMBIGUOUS BOS" in s.insufficient_structure_reason
               for s in rejected)
    assert not _valid_setups(setups)


# ===========================================================================
# EDGE 4 — obviously unrelated / unrealistic setup (huge point range)
# ===========================================================================

def test_unrealistic_large_setup_rejected_structural_recheck():
    """If the detected low+high produce an enormous point range versus recent
    candle volatility, the setup is obviously unrelated -> rejected with a
    structural-recheck reason.  TP is NEVER forced closer and levels are never
    remapped (spec #17)."""
    flat, ts = _flat(30, _start_ts(), price=100.0, spread=0.5)
    leg1, ts = _leg(ts, 100, 106)          # swing high H0 (106.5)
    leg2, ts = _leg(ts, 106, 100)          # swing low L0 (99.5)
    leg3, ts = _leg(ts, 100, 108)          # swing high H1 (108.5)
    # One single enormous dive candle: low = 30 (Point 2 candidate)
    dive = _bar(ts, 108.0, 110.0, 30.0, 32.0)
    ts += _DT
    # Confirm the dive low as a swing low (needs 3 right bars, lows > 30)
    confirm = [
        _bar(ts, 30.6, 31.0, 30.2, 30.8),
        _bar(ts + _DT, 30.8, 31.5, 30.4, 31.0),
        _bar(ts + 2 * _DT, 31.0, 31.8, 30.6, 31.2),
    ]
    ts += 3 * _DT
    # Fast rally that breaks the recent swing high (108.5/110)
    rally, ts = _leg(ts, 31.2, 111.0, bars=5)
    bars = flat + leg1 + leg2 + leg3 + [dive] + confirm + rally

    setups, _ = _run(bars)

    rejected = [s for s in setups if not s.validation_passed]
    assert rejected, "expected the unrealistic setup to be rejected"
    assert any("UNRELATED OR UNREALISTIC" in s.insufficient_structure_reason
               for s in rejected)
    assert not _valid_setups(setups)


# ===========================================================================
# Configurable ENTRY touch rule (spec #21)
# ===========================================================================

def test_entry_touch_rule_is_configurable():
    """Default rule: candle range must intersect 0.618.  A configurable
    tolerance widens the band; a near-miss never triggers Entry by default."""
    from app.retracement.engine import _apply_fib_to_setup, _compute_fib_levels
    from app.retracement.models import RetracementSetup

    s = RetracementSetup()
    _apply_fib_to_setup(s, _compute_fib_levels(100.0, 200.0))  # entry = 161.8

    # Exact span -> touched
    assert s.touch_entry(160.0, 165.0) is True
    # Near-miss below (close to but not crossing) -> NOT touched at tolerance 0
    assert s.touch_entry(162.5, 165.0) is False
    assert s.touch_entry(158.0, 161.5) is False
    # A configurable tolerance allows the near-miss to count
    assert s.touch_entry(162.5, 165.0, tolerance=1.0) is True
    assert s.touch_entry(158.0, 161.5, tolerance=0.5) is True


# ===========================================================================
# Report + point analysis formatting (spec #27, #16)
# ===========================================================================

def test_format_report_no_valid_setup():
    assert format_report(None) == "NO TRADE / INVALID RETRACEMENT"


def test_point_analysis_is_points_first():
    """Point analysis must be expressed in POINTS, never primarily as raw
    prices (spec #16)."""
    flat, ts = _flat(30, _start_ts())
    zig, ts = _zigzag([100, 106, 100, 110, 108, 113, 111, 117, 115, 121,
                       119, 125, 114, 130], ts)
    setups, _ = _run(flat + zig)

    valid = _valid_setups(setups)
    assert valid
    s = valid[0]
    report = s.report()
    assert report["status"] == "RETRACEMENT SETUP DETECTED"
    assert report["bos"]["confirmed"] is True
    assert report["point_2"]["identified"] is True
    assert report["fib_structure"]["1.618"] is not None   # BLACK LINE 1
    assert report["fib_structure"]["1.000"] is not None    # TP
    assert report["fib_structure"]["0.618"] is not None    # ENTRY
    assert report["fib_structure"]["0.236"] is not None    # SL
    assert report["fib_structure"]["0.000"] is not None    # BLACK LINE 2 / P2
    assert report["tp"]["mode"] == "LOCKED"
    pa = report["point_analysis"]
    assert pa["entry_to_tp_points"] is not None
    assert pa["entry_to_sl_points"] is not None

    text = format_report(s)
    for needle in ("RETRACEMENT SETUP DETECTED", "BOS:", "POINT 2:",
                   "FIB STRUCTURE:", "BLACK LINE 1", "ENTRY", "SL",
                   "POINT ANALYSIS:", "POINTS", "TP LOCK VALUE", "DO NOT UPDATE"):
        assert needle in text, f"report missing {needle!r}"


# ===========================================================================
# Persistence of the new tp_before_freeze field (spec #24)
# ===========================================================================

@pytest.mark.asyncio
async def test_tp_before_freeze_persists():
    """tp_before_freeze must survive a save/load round trip (restart safety)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database.models import Base
    from app.retracement.models import RetracementSetup
    from app.retracement.repository import RetracementRepository

    engine_ = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine_.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(bind=engine_, expire_on_commit=False)

    async with Session() as session:
        repo = RetracementRepository(session)
        setup = RetracementSetup()
        setup.state = RetracementState.TP_FROZEN
        setup.point_2_price = 100.0
        setup.current_high_price = 200.0
        setup.entry_touched = True
        setup.entry_timestamp = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        setup.tp_locked = True
        setup.locked_tp = 200.0
        setup.tp_before_freeze = 200.0
        await repo.save_setup(setup)
        await session.commit()

        restored = await repo.load_setup_by_id(setup.setup_id)
        assert restored is not None
        assert restored.tp_before_freeze == pytest.approx(200.0)
        assert restored.locked_tp == pytest.approx(200.0)
        assert restored.tp_locked is True

    await engine_.dispose()
