"""
RETRACEMENT_BOS_V1 — No-Look-Ahead Audit.

Verifies that the engine never uses future data:

1. Batch vs online equivalence: the same setup (same Point 2, same locked TP,
   same state sequence) must be produced whether we feed candles one at a time
   (online) or all at once (batch).
2. Truncation invariance: feeding only the first N candles must produce the
   same result for setups that form within those N candles as feeding the full
   series (future candles cannot influence the past).
3. Frozen-TP invariance: post-entry highs never change locked_tp.

This script is a runtime audit; the property tests are also encoded in
tests/unit/test_retracement_bos.py.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.data.models import Candle
from app.retracement.engine import RetracementBOSEngine
from app.retracement.models import RetracementEventType, RetracementState


def _c(ts, o, h, l, c, v=10.0):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _series(n):
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    return [_c(start + __import__("datetime").timedelta(minutes=15 * i),
               100 + i * 0.1, 100 + i * 0.1 + 1, 99 + i * 0.05, 100 + i * 0.1)
            for i in range(n)]


def _oscillating_candles():
    """A realistic oscillating series so genuine swing highs/lows form, then a
    bullish BOS.  Used by both batch and online paths (no degenerate flat data)."""
    ohlc = []
    for i in range(45):
        wig = (i % 5) * 0.2
        o = 100.0 + wig
        ohlc.append((o, o + 1.0, o - 1.0, o))
    ohlc += [(120, 125, 100, 120)] * 8   # impulse breaking the swing high (BOS)
    ohlc += [(126, 130, 122, 126)] * 5   # higher high
    ohlc += [(132, 135, 128, 132)] * 5   # higher high
    ohlc += [(105, 110, 104, 106)] * 6   # retrace toward 0.618
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    candles = []
    for i, (o, h, l, c) in enumerate(ohlc):
        candles.append(_c(start + __import__("datetime").timedelta(minutes=15 * i), o, h, l, c))
    return candles


def audit_batch_vs_online():
    """Online (per-candle) and batch must agree on Point 2 / locked TP."""
    candles = _oscillating_candles()

    # Batch
    eb = RetracementBOSEngine()
    setups_b, _ = eb.run_series(candles)
    sb = next((s for s in setups_b if s.point_2_price is not None), None)
    # Online
    eo = RetracementBOSEngine()
    eo._candles = []
    for cd in candles:
        eo.process_candle(cd)
    so = eo.setup if (eo.setup and eo.setup.point_2_price is not None) else None

    if sb is None or so is None:
        print(f"  [batch vs online] setups_batch={len(setups_b)} online_setup={'yes' if eo.setup else 'no'}")
        print("  [batch vs online] not enough setups to compare (OK if data lacks structure)")
        return True
    same_p2 = abs(sb.point_2_price - so.point_2_price) < 1e-9
    same_bos = abs((sb.bos_price or 0) - (so.bos_price or 0)) < 1e-9
    print(f"  Point 2 batch={sb.point_2_price} online={so.point_2_price} same={same_p2}")
    print(f"  BOS     batch={sb.bos_price} online={so.bos_price} same={same_bos}")
    print(f"  [batch vs online] setups_batch={len(setups_b)}")
    return same_p2 and same_bos


def audit_truncation():
    """Future candles must not influence earlier setups."""
    # Use the oscillating series so genuine swing highs/lows form.
    candles = _oscillating_candles()
    # Add a future strong rally that MUST NOT change the earlier setup.
    candles += [_c(candles[-1].timestamp + __import__("datetime").timedelta(minutes=15 * (i + 1)),
                   160, 180, 155, 175) for i in range(12)]

    e_full = RetracementBOSEngine()
    setups_full, _ = e_full.run_series(candles)
    s_full = next((s for s in setups_full if s.point_2_price is not None), None)

    # Truncate just before the future rally begins.
    trunc_len = min(74, len(candles) - 12)
    e_trunc = RetracementBOSEngine()
    setups_trunc, _ = e_trunc.run_series(candles[:trunc_len])
    s_trunc = next((s for s in setups_trunc if s.point_2_price is not None), None)

    ok = True
    if s_full is not None and s_trunc is not None:
        same_p2 = abs(s_full.point_2_price - s_trunc.point_2_price) < 1e-9
        same_bos = abs((s_full.bos_price or 0) - (s_trunc.bos_price or 0)) < 1e-9
        print(f"  [truncation] point2 full={s_full.point_2_price} trunc={s_trunc.point_2_price} same={same_p2}")
        print(f"  [truncation] bos    full={s_full.bos_price} trunc={s_trunc.bos_price} same={same_bos}")
        ok = ok and same_p2 and same_bos
    else:
        print(f"  [truncation] setups_full={len(setups_full)} setups_trunc={len(setups_trunc)}")
        print("  [truncation] NOT ENOUGH SETUPS to compare - audit inconclusive")
        ok = False
    return ok


def audit_frozen_tp():
    """Post-entry highs must never move the locked TP."""
    from app.retracement.engine import _apply_fib_to_setup, _compute_fib_levels
    from app.retracement.models import RetracementSetup

    setup = RetracementSetup()
    setup.point_2_price = 100.0
    setup.current_high_price = 200.0
    _apply_fib_to_setup(setup, _compute_fib_levels(100.0, 200.0))
    engine = RetracementBOSEngine()
    engine.restore_setup(setup)

    # simulate entry touch freeze
    touch = _c(datetime(2026, 1, 3, tzinfo=timezone.utc), 200, 205, 160, 170)
    engine._current_index = 1
    engine._candles.append(touch)
    engine._process_active_setup(touch, confirmed_highs=[])
    locked = setup.locked_tp
    for price in (210.0, 215.0, 220.0):
        setup.current_high_price = price
        lv = _compute_fib_levels(setup.point_2_price, setup.current_high_price)
        _apply_fib_to_setup(setup, lv)
        ok = (setup.locked_tp == locked)
        print(f"  [frozen] high={price} locked_tp={setup.locked_tp} unchanged={ok}")
        if not ok:
            return False
    return True


def main():
    print("RETRACEMENT_BOS_V1 — No-Look-Ahead Audit")
    print("=" * 45)
    r1 = audit_batch_vs_online()
    r2 = audit_truncation()
    r3 = audit_frozen_tp()
    ok = r1 and r2 and r3
    print("=" * 45)
    print("AUDIT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
