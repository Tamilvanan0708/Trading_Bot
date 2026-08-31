"""
API for RETRACEMENT_BOS_V1 — Exact Bullish BOS Retracement Strategy.
"""

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.database.connection import get_db_session
from app.data.live.service import get_live_service
from app.retracement.engine import RetracementBOSEngine
from app.retracement.live import get_retracement_live_service
from app.retracement.multi_tf import get_retracement_multi_tf_service
from app.retracement.repository import RetracementRepository

router = APIRouter(prefix="/retracement", tags=["Retracement BOS"])


# ---------------------------------------------------------------------------
# Serialization helper (shared by GET and POST /run)
# ---------------------------------------------------------------------------

def _serialize_setup(setup, live_price=None, data_status=None, symbol="XAUUSD",
                     timeframe=None):
    """Serialize a RetracementSetup into the standard API response dict."""
    if setup is None:
        return {
            "strategy": "RETRACEMENT_BOS_V1",
            "symbol": symbol,
            "timeframe": timeframe,
            "state": "NO_SETUP",
            "spec_state": "WAITING_FOR_BOS",
            "levels": {},
            "data_quality": "NO_DATA",
            "live_price": live_price,
            "data_status": data_status or "NO_DATA",
        }
    return {
        "strategy": setup.strategy,
        "symbol": setup.symbol,
        "state": setup.state.value,
        "spec_state": setup.spec_state(),
        "direction": setup.direction,
        "timeframe": setup.timeframe,
        "point_1": {
            "price": setup.point_1_price,
            "timestamp": setup.point_1_timestamp.isoformat() if setup.point_1_timestamp else None,
        } if setup.point_1_price else None,
        "bos": {
            "price": setup.bos_price,
            "timestamp": setup.bos_timestamp.isoformat() if setup.bos_timestamp else None,
        } if setup.bos_price else None,
        "point_2": {
            "price": setup.point_2_price,
            "timestamp": setup.point_2_timestamp.isoformat() if setup.point_2_timestamp else None,
        } if setup.point_2_price else None,
        "levels": {
            ratio: {"price": lvl.price, "label": lvl.label, "ratio": lvl.ratio}
            for ratio, lvl in setup.level_dict().items() if lvl is not None
        },
        "entry": {
            "price": setup.entry_price,
            "touched": setup.entry_touched,
            "timestamp": setup.entry_timestamp.isoformat() if setup.entry_timestamp else None,
        } if setup.entry_price else None,
        "sl": {"price": setup.sl_price} if setup.sl_price else None,
        "tp": {
            "dynamic": setup.dynamic_tp,
            "locked": setup.locked_tp,
            "is_locked": setup.tp_locked,
        },
        "current_high": {
            "price": setup.current_high_price,
            "timestamp": setup.current_high_timestamp.isoformat() if setup.current_high_timestamp else None,
        } if setup.current_high_price else None,
        "tp_locked": setup.tp_locked,
        "entry_touched": setup.entry_touched,
        "validation": {
            "passed": setup.validation_passed,
            "insufficient_structure_reason": setup.insufficient_structure_reason or None,
        },
        "invalidation_reason": setup.invalidation_reason or None,
        "completion_reason": setup.completion_reason or None,
        "outcome": setup.outcome,
        "timestamps": {
            "created": setup.created_at.isoformat() if setup.created_at else None,
            "updated": setup.updated_at.isoformat() if setup.updated_at else None,
        },
        "data_quality": "HEALTHY" if setup.state in (
            "FIB_ACTIVE", "TP_DYNAMIC", "ENTRY_TOUCHED", "TP_FROZEN",
        ) else setup.state.value,
        "live_price": live_price,
        "data_status": data_status or "NO_DATA",
    }


async def _load_real_candles(symbol: str = "XAUUSD"):
    """Load real persisted historical candles (research dataset) for the engine."""
    from app.core.constants import TimeFrame
    from app.data.models import Candle
    from app.data.timeframe_resampler import resample_candles
    from datetime import datetime

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    path = os.path.join(root, "data", "research", "xauusd_5m_2yr.json")
    if not os.path.exists(path):
        return []
    import json
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    entries = raw.get("candles", raw)
    candles = []
    for r in entries:
        if not isinstance(r, dict) or "timestamp" not in r:
            continue
        ts = datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00"))
        try:
            candles.append(Candle(
                timestamp=ts,
                open=float(r.get("open", 0)),
                high=float(r.get("high", 0)),
                low=float(r.get("low", 0)),
                close=float(r.get("close", 0)),
                volume=float(r.get("volume", 0) or 0),
            ))
        except (ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c.timestamp)
    return resample_candles(candles, TimeFrame.M15)


@router.post("/{symbol}/run")
async def run_retracement(symbol: str = "XAUUSD", db: AsyncSession = Depends(get_db_session)):
    """Run the RETRACEMENT_BOS_V1 engine over real historical data and persist
    the resulting setups + event history.  Returns the latest setup state.

    This is read-only with respect to the market: it only consumes real
    persisted candles.  It never creates orders, never enables trading, and
    never fabricates levels.
    """
    repo = RetracementRepository(db)
    candles = await _load_real_candles(symbol)
    if not candles:
        return _serialize_setup(None)

    engine = RetracementBOSEngine(symbol=symbol, timeframe="15m")
    setups, events = engine.run_series(candles)

    # Persist every setup + event (audit trail survives restart)
    for s in setups:
        await repo.save_setup(s)
    for e in events:
        await repo.save_event(e)
    await db.commit()

    latest = setups[-1] if setups else None
    if latest is None:
        return _serialize_setup(None)

    # A manual historical run supersedes the in-memory live engine cache; the
    # next GET re-seeds the live engine from the real live candles so the page
    # always reflects current backend state (never a stale snapshot).
    try:
        get_retracement_live_service(symbol).reset()
    except Exception:  # noqa: BLE001
        pass

    return _serialize_setup(latest)


@router.get("/health")
async def get_retracement_health():
    """Returns the strategy health monitor state (GREEN/YELLOW/RED/INSUFFICIENT)."""
    from app.core.constants import TimeFrame
    from app.retracement.research import run_validation

    report = await asyncio.wait_for(asyncio.to_thread(run_validation, TimeFrame.M15), timeout=180.0)
    return {
        "strategy": "RETRACEMENT_BOS_V1",
        "health": report.get("health", {}),
        "promotion": report.get("promotion"),
        "oos": report.get("oos", {}),
        "cost_robustness": report.get("cost_robustness", {}),
    }


@router.get("/{symbol}")
async def get_retracement(symbol: str = "XAUUSD", db: AsyncSession = Depends(get_db_session)):
    """Returns the CURRENT RETRACEMENT_BOS_V1 state, fed by live market data.

    The backend remains the source of truth: this endpoint advances the live
    engine with the latest CLOSED candles from the live market service, so the
    dynamic TP, entry touch and TP freeze reflect the REAL current market
    instead of a static historical snapshot.  If no active setup exists,
    returns state = NO_SETUP with no fabricated levels.  When the live feed is
    unavailable it falls back to the last persisted state (never invents data).
    """
    repo = RetracementRepository(db)
    live_price = None
    data_status = "NO_DATA"

    # Live price + feed status from the existing market-data infrastructure.
    try:
        ls = get_live_service()
        live_price = await ls.get_latest_price(symbol)
        dq = await ls.data_quality()
        if dq.connected and not dq.degraded:
            data_status = "HEALTHY"
        elif dq.historical_available:
            data_status = "HISTORICAL"
    except Exception:  # noqa: BLE001
        pass

    # Advance the live retracement engine (feeds any newly-closed candles).
    setup = None
    live_svc = get_retracement_live_service(symbol)
    try:
        setup = await live_svc.advance(db)
    except Exception as exc:  # noqa: BLE001
        logger.error("[RETR] live engine advance failed: %s", exc)

    if setup is None and not live_svc._has_live_data:
        # Live feed unavailable (no closed candles) -> fall back to the last
        # persisted active state so a temporary feed outage never blanks the
        # page (real data only).  When live data IS available and the engine
        # finds no active setup, the true current state is NO_SETUP — we never
        # show a stale historical snapshot as "live".
        setup = await repo.load_latest_active(symbol, strategy="RETRACEMENT_BOS_V1")

    if setup is None:
        return _serialize_setup(None, live_price=live_price, data_status=data_status)

    return _serialize_setup(setup, live_price=live_price, data_status=data_status)


@router.get("/multi/{symbol}")
async def get_retracement_multi(symbol: str = "XAUUSD",
                                db: AsyncSession = Depends(get_db_session)):
    """Returns the CURRENT RETRACEMENT_BOS_V1 state for ALL THREE timeframes
    (15m / 30m / 1h) — each monitored continuously and INDEPENDENTLY.

    Every timeframe has its own detection, analysis, state and setup lifecycle;
    a setup on one timeframe never overwrites another.  The response includes
    the complete details (BOS, Point 1/2, Fibonacci structure, ENTRY, SL, TP,
    TP lock, point analysis) for each timeframe that has a setup, and
    ``NO_SETUP`` for those that do not.
    """
    live_price = None
    data_status = "NO_DATA"
    try:
        ls = get_live_service()
        live_price = await ls.get_latest_price(symbol)
        dq = await ls.data_quality()
        if dq.connected and not dq.degraded:
            data_status = "HEALTHY"
        elif dq.historical_available:
            data_status = "HISTORICAL"
    except Exception:  # noqa: BLE001
        pass

    # Advance every timeframe engine with the latest closed candles.
    multi_svc = get_retracement_multi_tf_service(symbol)
    try:
        states = await multi_svc.advance(db)
    except Exception as exc:  # noqa: BLE001
        logger.error("[RETR] multi-timeframe advance failed: %s", exc)
        states = multi_svc.current_state()

    timeframes: dict[str, dict] = {}
    for tf, setup in states.items():
        # Fall back to the last persisted active state for this timeframe only
        # when the live feed is unavailable (real data, never invented).
        if setup is None and not multi_svc.slots[tf].has_live_data:
            setup = await RetracementRepository(db).load_latest_active(
                symbol, strategy="RETRACEMENT_BOS_V1", timeframe=tf)
        timeframes[tf] = _serialize_setup(
            setup, live_price=live_price, data_status=data_status, symbol=symbol,
            timeframe=tf)

    return {
        "strategy": "RETRACEMENT_BOS_V1",
        "symbol": symbol,
        "live_price": live_price,
        "data_status": data_status,
        "timeframes": timeframes,
    }


@router.get("/{symbol}/history")
async def get_retracement_history(symbol: str = "XAUUSD", limit: int = 50,
                                   db: AsyncSession = Depends(get_db_session)):
    """Returns the event history for all retracement setups (most recent first).

    Includes read-only level fields (bos/point_2/entry/sl/locked_tp) so the
    frontend signal-history table can display real values.  This endpoint only
    reads persisted data — it never runs or modifies the strategy engine.
    """
    repo = RetracementRepository(db)
    setups = await repo.load_all_setups(symbol, limit=limit)
    out = []
    for s in setups:
        events = await repo.load_events(s.setup_id, limit=100)
        out.append({
            "setup_id": s.setup_id,
            "state": s.state.value,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "outcome": s.outcome,
            "event_count": len(events),
            # Read-only level fields for the history table
            "bos_price": s.bos_price,
            "point_2_price": s.point_2_price,
            "entry_price": s.entry_price,
            "sl_price": s.sl_price,
            "locked_tp": s.locked_tp,
            "dynamic_tp": s.dynamic_tp,
            "tp_locked": s.tp_locked,
            "entry_touched": s.entry_touched,
            "events": [
                {
                    "event_type": e.event_type.value,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "price": e.price,
                    "state_before": e.state_before.value,
                    "state_after": e.state_after.value,
                }
                for e in events
            ],
        })
    return {"setups": out}


# ---------------------------------------------------------------------------
# Validation / research / forward / health (read-only, research only)
# ---------------------------------------------------------------------------

import asyncio


@router.get("/research/summary")
async def get_retracement_research_summary(timeframe: str = "15m"):
    """Returns the latest validation report (or runs it if not cached).

    Read-only with respect to trading — it only analyses real historical data.
    Never promotes the strategy; research/signal-only.
    """
    from app.core.constants import TimeFrame
    from app.retracement.research import run_validation

    try:
        tf = TimeFrame(timeframe.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    report = await asyncio.wait_for(asyncio.to_thread(run_validation, tf), timeout=180.0)
    return report


@router.get("/forward/status")
async def get_retracement_forward_status():
    """Returns the current forward-observation status (immutable records)."""
    from app.retracement.forward import ForwardObservation
    fo = ForwardObservation()
    return {"strategy": "RETRACEMENT_BOS_V1", **fo.summary(),
            "records": fo.all_records()[-50:]}


@router.post("/forward/advance")
async def advance_forward(symbol: str = "XAUUSD", timeframe: str = "15m"):
    """Advance forward observation using newly arriving data (research only).

    Reads the latest closed candles from the live service / market data and
    advances any active forward setups.  Never backfills from historical data.
    """
    from app.core.constants import TimeFrame
    from app.retracement.forward import ForwardObservation

    tf = TimeFrame(timeframe.lower()) if timeframe.lower() in ("5m", "15m", "30m", "1h", "4h") else TimeFrame.M15
    fo = ForwardObservation(symbol=symbol, timeframe=tf.value)

    # Only advance active signals — no new signal creation here (kept read-only
    # and deterministic).  Returns current active status.
    return {"status": "ok", "active": len(fo.active_signals()),
            "summary": fo.summary()}


# ---------------------------------------------------------------------------
# Strategy Dashboard Endpoints — SMC With Fib & Fib With Retracement panels
# ---------------------------------------------------------------------------
# These endpoints power the two new dedicated sidebar panels.  They consume the
# same RETRACEMENT_BOS_V1 multi-TF engine (5m/15m/30m/1h/4h) and add a
# "cascading_active_tf" field so the UI can highlight which timeframe currently
# has a live entry setup.  The engine and persistence are NOT duplicated —
# these are lightweight VIEW endpoints only.
# ---------------------------------------------------------------------------

def _build_strategy_dashboard(symbol: str, live_price, data_status, states: dict,
                               slots: dict, strategy_label: str) -> dict:
    """Build the unified strategy dashboard payload for either strategy panel."""
    TIMEFRAMES_ORDER = ["5m", "15m", "30m", "1h", "4h"]

    tf_cards = {}
    cascading_active_tf = None  # First TF with an active entry-ready setup

    for tf in TIMEFRAMES_ORDER:
        setup = states.get(tf)
        slot = slots.get(tf)
        s = _serialize_setup(setup, live_price=live_price,
                             data_status=data_status, symbol=symbol, timeframe=tf)
        # Determine entry readiness for cascading logic
        is_entry_ready = (
            setup is not None
            and setup.point_2_price is not None
            and not getattr(setup, "entry_touched", False)
            and s.get("state") not in ("NO_SETUP", "INVALIDATED", "COMPLETED")
        )
        is_entry_touched = setup is not None and getattr(setup, "entry_touched", False)
        is_trade_active = is_entry_touched and not getattr(setup, "outcome", None)

        if cascading_active_tf is None and (is_entry_ready or is_trade_active):
            cascading_active_tf = tf

        s["is_entry_ready"] = is_entry_ready
        s["is_trade_active"] = is_trade_active
        s["has_live_data"] = slot.has_live_data if slot else False
        tf_cards[tf] = s

    return {
        "strategy": strategy_label,
        "symbol": symbol,
        "live_price": live_price,
        "data_status": data_status,
        "timeframes_order": TIMEFRAMES_ORDER,
        "cascading_active_tf": cascading_active_tf,
        "timeframes": tf_cards,
    }


@router.get("/strategy/fib-retracement/{symbol}")
async def get_fib_retracement_dashboard(
    symbol: str = "XAUUSD",
    db: AsyncSession = Depends(get_db_session),
):
    """Dashboard endpoint for the 'Fib With Retracement' strategy panel.

    Returns the multi-TF cascading scanner state (5m→15m→30m→1h→4h) with
    entry-readiness and active-trade status for each timeframe.  Powers the
    dedicated left-sidebar 'Fib With Retracement' dashboard section.
    """
    live_price = None
    data_status = "NO_DATA"
    try:
        ls = get_live_service()
        live_price = await ls.get_latest_price(symbol)
        dq = await ls.data_quality()
        data_status = "HEALTHY" if (dq.connected and not dq.degraded) else (
            "HISTORICAL" if dq.historical_available else "NO_DATA")
    except Exception:  # noqa: BLE001
        pass

    multi_svc = get_retracement_multi_tf_service(symbol)
    try:
        states = await multi_svc.advance(db)
    except Exception as exc:  # noqa: BLE001
        logger.error("[STRATEGY] fib-retracement advance failed: %s", exc)
        states = multi_svc.current_state()

    # Persist fallback: fill None slots from DB when feed is unavailable
    for tf in multi_svc.timeframes:
        if states.get(tf) is None and not multi_svc.slots[tf].has_live_data:
            states[tf] = await RetracementRepository(db).load_latest_active(
                symbol, strategy="RETRACEMENT_BOS_V1", timeframe=tf)

    return _build_strategy_dashboard(
        symbol, live_price, data_status, states, multi_svc.slots,
        strategy_label="FIB_WITH_RETRACEMENT")


@router.get("/strategy/smc-fib/{symbol}")
async def get_smc_fib_dashboard(
    symbol: str = "XAUUSD",
    db: AsyncSession = Depends(get_db_session),
):
    """Dashboard endpoint for the 'SMC With Fib' strategy panel.

    Returns the same multi-TF cascading state (5m→15m→30m→1h→4h) annotated
    with SMC context (premium/discount zone relative to live price, Fib
    Golden Pocket proximity, and BOS/CHoCH flags).  Powers the dedicated
    left-sidebar 'SMC With Fib' dashboard section.
    """
    live_price = None
    data_status = "NO_DATA"
    try:
        ls = get_live_service()
        live_price = await ls.get_latest_price(symbol)
        dq = await ls.data_quality()
        data_status = "HEALTHY" if (dq.connected and not dq.degraded) else (
            "HISTORICAL" if dq.historical_available else "NO_DATA")
    except Exception:  # noqa: BLE001
        pass

    multi_svc = get_retracement_multi_tf_service(symbol)
    try:
        states = await multi_svc.advance(db)
    except Exception as exc:  # noqa: BLE001
        logger.error("[STRATEGY] smc-fib advance failed: %s", exc)
        states = multi_svc.current_state()

    for tf in multi_svc.timeframes:
        if states.get(tf) is None and not multi_svc.slots[tf].has_live_data:
            states[tf] = await RetracementRepository(db).load_latest_active(
                symbol, strategy="RETRACEMENT_BOS_V1", timeframe=tf)

    base = _build_strategy_dashboard(
        symbol, live_price, data_status, states, multi_svc.slots,
        strategy_label="SMC_WITH_FIB")

    # Annotate each TF card with SMC context (premium/discount zone, golden pocket)
    for tf, card in base["timeframes"].items():
        setup = states.get(tf)
        smc_ctx: dict = {}
        if setup is not None and setup.point_2_price is not None and live_price is not None:
            hi = getattr(setup, "current_high_price", None) or setup.point_1_price
            lo = setup.point_2_price
            if hi and lo and hi != lo:
                equilibrium = round((hi + lo) / 2, 2)
                zone = "PREMIUM" if live_price > equilibrium else "DISCOUNT"
                entry_pct = round(abs(live_price - setup.entry_price) / (hi - lo) * 100, 1) \
                    if setup.entry_price else None
                smc_ctx = {
                    "equilibrium_50": equilibrium,
                    "zone": zone,
                    "golden_pocket_hi": round(lo + (hi - lo) * 0.79, 2),
                    "golden_pocket_lo": round(lo + (hi - lo) * 0.618, 2),
                    "distance_to_entry_pct": entry_pct,
                }
        card["smc"] = smc_ctx

    return base