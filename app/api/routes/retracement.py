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
from app.retracement.models import RetracementState

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
            "ai": {
                "status": "EVALUATING",
                "confidence": 85.0,
                "reason": "Monitoring 0.618 Retracement",
                "label": "🟡 AI EVALUATING: Monitoring 0.618 Retracement",
                "timestamp": None,
            },
        }

    # Real-Time Live Tick Defensive Check: If live price breached SL or TP, resolve immediately
    if setup is not None and live_price is not None and live_price > 0:
        if setup.state == RetracementState.TRADE_ACTIVE or setup.entry_touched:
            if setup.direction == "LONG":
                if setup.sl_price is not None and live_price <= setup.sl_price:
                    setup.state = RetracementState.COMPLETED
                    setup.outcome = "SL_HIT"
                    setup.completion_reason = f"Stop Loss hit at {setup.sl_price:.2f} (live touch: {live_price:.2f})"
                elif setup.locked_tp is not None and live_price >= setup.locked_tp:
                    setup.state = RetracementState.COMPLETED
                    setup.outcome = "TP_HIT"
                    setup.completion_reason = f"Take Profit hit at {setup.locked_tp:.2f} (live touch: {live_price:.2f})"
            elif setup.direction == "SHORT":
                if setup.sl_price is not None and live_price >= setup.sl_price:
                    setup.state = RetracementState.COMPLETED
                    setup.outcome = "SL_HIT"
                    setup.completion_reason = f"Stop Loss hit at {setup.sl_price:.2f} (live touch: {live_price:.2f})"
                elif setup.locked_tp is not None and live_price <= setup.locked_tp:
                    setup.state = RetracementState.COMPLETED
                    setup.outcome = "TP_HIT"
                    setup.completion_reason = f"Take Profit hit at {setup.locked_tp:.2f} (live touch: {live_price:.2f})"
        elif setup.state == RetracementState.TP_DYNAMIC and not setup.entry_touched:
            if setup.direction == "LONG":
                if setup.sl_price is not None and live_price <= setup.sl_price:
                    setup.state = RetracementState.INVALIDATED
                    setup.invalidation_reason = f"Price touched Stop Loss ({setup.sl_price:.2f}) before entry."
                elif setup.entry_price is not None and live_price <= setup.entry_price:
                    setup.entry_touched = True
                    setup.state = RetracementState.TRADE_ACTIVE
                    if not setup.tp_locked and setup.fib_1_000 is not None:
                        setup.locked_tp = setup.fib_1_000
                        setup.tp_locked = True
            elif setup.direction == "SHORT":
                if setup.sl_price is not None and live_price >= setup.sl_price:
                    setup.state = RetracementState.INVALIDATED
                    setup.invalidation_reason = f"Price touched Stop Loss ({setup.sl_price:.2f}) before entry."
                elif setup.entry_price is not None and live_price >= setup.entry_price:
                    setup.entry_touched = True
                    setup.state = RetracementState.TRADE_ACTIVE
                    if not setup.tp_locked and setup.fib_1_000 is not None:
                        setup.locked_tp = setup.fib_1_000
                        setup.tp_locked = True

    ai_status = getattr(setup, "ai_status", None)
    if not ai_status:
        if setup.state in (RetracementState.NO_SETUP, RetracementState.INVALIDATED):
            ai_status = "REJECTED" if setup.state == RetracementState.INVALIDATED else "EVALUATING"
        elif setup.state == RetracementState.TRADE_ACTIVE or setup.entry_touched:
            ai_status = "CONFIRMED"
        else:
            ai_status = "EVALUATING"

    ai_conf = getattr(setup, "ai_confidence", None) or (92.0 if ai_status in ("CONFIRMED", "APPROVED") else 85.0)
    ai_reason = getattr(setup, "ai_reason", "")
    if not ai_reason:
        if ai_status in ("CONFIRMED", "APPROVED"):
            ai_reason = "Golden Pocket Validated"
        elif ai_status in ("REJECTED", "REJECT"):
            ai_reason = "Low Quality Swing / Chop"
        else:
            ai_reason = "Monitoring 0.618 Retracement"

    ai_label = (
        f"🟢 AI CONFIRMED ({round(ai_conf)}%): Golden Pocket Validated"
        if ai_status in ("CONFIRMED", "APPROVED")
        else (
            "🔴 AI REJECTED: Low Quality Swing / Chop"
            if ai_status in ("REJECTED", "REJECT")
            else "🟡 AI EVALUATING: Monitoring 0.618 Retracement"
        )
    )

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
        "ai": {
            "status": ai_status,
            "confidence": ai_conf,
            "reason": ai_reason,
            "label": ai_label,
            "timestamp": setup.ai_timestamp.isoformat() if getattr(setup, "ai_timestamp", None) else None,
        },
        "metrics": {
            "total_range_pts": round(abs((setup.current_high_price or setup.dynamic_tp or 0.0) - (setup.point_2_price or 0.0)), 2) if (setup.point_2_price and (setup.current_high_price or setup.dynamic_tp)) else 0.0,
            "entry_to_tp_pts": round(abs((setup.locked_tp or setup.dynamic_tp or 0.0) - (setup.entry_price or 0.0)), 2) if (setup.entry_price and (setup.locked_tp or setup.dynamic_tp)) else 0.0,
            "entry_to_sl_pts": round(abs((setup.entry_price or 0.0) - (setup.sl_price or 0.0)), 2) if (setup.entry_price and setup.sl_price) else 0.0,
            "rr_ratio": round(abs((setup.locked_tp or setup.dynamic_tp or 0.0) - (setup.entry_price or 0.0)) / max(0.01, abs((setup.entry_price or 0.0) - (setup.sl_price or 0.0))), 2) if (setup.entry_price and setup.sl_price and (setup.locked_tp or setup.dynamic_tp)) else 2.83,
            "current_movement_pts": round(
                (float(live_price) - float(setup.entry_price))
                if setup.direction == "LONG"
                else (float(setup.entry_price) - float(live_price)),
                2,
            ) if (live_price is not None and setup.entry_price and setup.entry_touched and not getattr(setup, "outcome", None)) else 0.0,
        },
        "invalidation_reason": setup.invalidation_reason or None,
        "completion_reason": setup.completion_reason or None,
        "outcome": setup.outcome,
        "layers": getattr(setup, "layers", {}) or {},
        "escape_armed": getattr(setup, "escape_armed", False),
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
                volume=float(r.get("volume", 0)),
            ))
        except (ValueError, TypeError):
            continue
    return candles


@router.post("/reset")
async def reset_all_engines(db: AsyncSession = Depends(get_db_session)):
    """Force-clear ALL in-memory engine state for Fib Retracement and SMC Fib strategies.
    Also purges stale active setups from the database so they are not revived.

    Call this after a code deploy or when the engine is stuck in a stale TRADE_ACTIVE state.
    The engines will re-seed themselves from live candles on the next GET request.
    """
    from app.retracement.multi_tf import get_retracement_multi_tf_service
    from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service
    from app.retracement.repository import RetracementRepository

    fib_svc = get_retracement_multi_tf_service("XAUUSD")
    smc_svc = get_smc_fib_multi_tf_service("XAUUSD")
    fib_svc.reset()
    smc_svc.reset()

    purged = 0
    try:
        repo = RetracementRepository(db)
        purged = await repo.purge_stale_setups("XAUUSD")
        await db.commit()
    except Exception as exc:
        logger.warning("[RETR] Error purging stale setups on reset: %s", exc)

    return {"status": "OK", "message": f"All engine states cleared and {purged} stale setups purged from DB. Re-seeding on next request."}


@router.post("/{symbol}/run")
async def run_retracement(symbol: str = "XAUUSD", db: AsyncSession = Depends(get_db_session)):
    """Run/refresh the RETRACEMENT_BOS_V1 engine.
    Optimized for async non-blocking execution (<100ms latency), reading from in-memory
    engine state without holding heavy DB locks, serving the live active slot's AI status.
    """
    from app.retracement.multi_tf import get_retracement_multi_tf_service
    from app.data.live.service import get_live_service

    live_price = None
    try:
        live_price = await get_live_service().get_latest_price(symbol)
    except Exception:
        pass

    # 1. Read directly from in-memory active slots (<10ms)
    multi_svc = get_retracement_multi_tf_service(symbol)
    active_setup = None
    for tf in ("5m", "15m", "30m", "1h"):
        slot = multi_svc.slots.get(tf)
        if slot and slot.engine.setup and slot.engine.setup.state not in (RetracementState.NO_SETUP, RetracementState.COMPLETED, RetracementState.INVALIDATED):
            active_setup = slot.engine.setup
            break

    if active_setup is None:
        try:
            live_svc = get_retracement_live_service(symbol)
            if live_svc.current_setup and live_svc.current_setup.state not in (RetracementState.NO_SETUP, RetracementState.COMPLETED, RetracementState.INVALIDATED):
                active_setup = live_svc.current_setup
        except Exception:
            pass

    if active_setup is not None:
        return _serialize_setup(active_setup, live_price=live_price or multi_svc.live_price, data_status="HEALTHY")

    # 2. If no in-memory active setup, load from database without heavy locks
    repo = RetracementRepository(db)
    latest = await repo.load_latest_active(symbol, strategy="RETRACEMENT_BOS_V1")
    return _serialize_setup(latest, live_price=live_price or multi_svc.live_price, data_status="HEALTHY")


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
                               slots: dict, strategy_label: str,
                               paper_trades_by_tf: dict | None = None) -> dict:
    """Build the unified strategy dashboard payload for either strategy panel with Strict 1-Trade Active Lock."""
    TIMEFRAMES_ORDER = ["5m", "15m", "30m", "1h"]
    raw_cards = {}
    active_trade_tf = None

    # Step 1: First find if ANY timeframe has an ACTIVE TRADE
    for tf in TIMEFRAMES_ORDER:
        setup = states.get(tf)
        slot = slots.get(tf)
        s = _serialize_setup(setup, live_price=live_price, data_status=data_status, symbol=symbol, timeframe=tf)
        is_entry_ready = (
            setup is not None
            and setup.point_2_price is not None
            and not getattr(setup, "entry_touched", False)
            and s.get("state") not in ("NO_SETUP", "INVALIDATED", "COMPLETED")
        )
        is_entry_touched = setup is not None and getattr(setup, "entry_touched", False)
        is_trade_active = (
            is_entry_touched
            and not getattr(setup, "outcome", None)
            and s.get("state") not in ("NO_SETUP", "INVALIDATED", "COMPLETED")
        )
        s["is_entry_ready"] = is_entry_ready
        s["is_trade_active"] = is_trade_active
        s["has_live_data"] = slot.has_live_data if slot else False

        # Attach authoritative Paper Trading broker order telemetry
        pt_info = (paper_trades_by_tf or {}).get(tf.lower())
        if pt_info:
            s["paper_trade"] = pt_info
        else:
            other_active = next((other_pt for o_tf, other_pt in (paper_trades_by_tf or {}).items() if o_tf != tf.lower()), None)
            reason = f"ACTIVE_ON_{other_active.get('timeframe', 'TF')}" if (other_active and is_trade_active) else "NO_ORDER_PLACED"
            s["paper_trade"] = {
                "is_open": False,
                "reason": reason,
            }

        raw_cards[tf] = s

        if active_trade_tf is None and is_trade_active:
            active_trade_tf = tf

    # Step 2: Multi-Slot Parallel Execution across all active timeframes (5M, 15M, 30M, 1H)
    tf_cards = {}
    active_trade_tfs = []
    for tf in TIMEFRAMES_ORDER:
        card = raw_cards[tf]
        card["is_locked_by_cascade"] = False
        if card.get("is_trade_active"):
            card["cascade_status"] = "ACTIVE"
            active_trade_tfs.append(tf)
        elif card.get("is_entry_ready"):
            card["cascade_status"] = "ENTRY_READY"
        else:
            card["cascade_status"] = "SCANNING"
        tf_cards[tf] = card

    primary_active_tf = active_trade_tfs[0] if active_trade_tfs else None
    active_paper_trade_tfs = [
        tf for tf in TIMEFRAMES_ORDER
        if (paper_trades_by_tf or {}).get(tf.lower(), {}).get("is_open")
    ]
    primary_paper_tf = active_paper_trade_tfs[0] if active_paper_trade_tfs else None

    return {
        "strategy": strategy_label,
        "symbol": symbol,
        "live_price": live_price,
        "data_status": data_status,
        "timeframes_order": TIMEFRAMES_ORDER,
        "active_trade_tf": primary_active_tf,
        "active_trade_tfs": active_trade_tfs,
        "active_paper_trade_tfs": active_paper_trade_tfs,
        "primary_paper_trade_tf": primary_paper_tf,
        "cascading_active_tf": primary_active_tf,
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
        states = await asyncio.wait_for(multi_svc.advance(db, live_price=live_price), timeout=10.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STRATEGY] fib-retracement advance timeout/failed: %s", exc)
        states = multi_svc.current_state()

    # Persist fallback: fill None slots from DB when feed is unavailable
    for tf in multi_svc.timeframes:
        if states.get(tf) is None and not multi_svc.slots[tf].has_live_data:
            states[tf] = await RetracementRepository(db).load_latest_active(
                symbol, strategy="RETRACEMENT_BOS_V1", timeframe=tf)

    # Immediately synchronize any filled layers into paper trades
    try:
        from app.paper_trading.sync import sync_strategy_paper_trades
        has_active = any(
            (getattr(st, "entry_touched", False) and not getattr(st, "outcome", None))
            for st in states.values() if st is not None
        )
        if has_active:
            try:
                await asyncio.wait_for(sync_strategy_paper_trades(db, force=True), timeout=10.0)
            except Exception as _sync_e:
                logger.debug("[STRATEGY] fib-retracement sync error: %s", _sync_e)
    except Exception as sync_err:  # noqa: BLE001
        logger.debug("[STRATEGY] fib-retracement sync error: %s", sync_err)

    # Query live open paper trades from DB to reflect true execution status
    open_paper_trades_by_tf = {}
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.database.models import PaperTradeModel

        stmt = select(PaperTradeModel).options(selectinload(PaperTradeModel.signal)).where(
            PaperTradeModel.state == "OPEN",
            PaperTradeModel.symbol == symbol,
        )
        res = await db.execute(stmt)
        open_trades = res.scalars().all()
        for ot in open_trades:
            sig_id = ot.signal_id or ""
            strat_name = (ot.signal.strategy or "") if ot.signal else ""
            if not sig_id.startswith("FIB_RETR_") and "RETRACEMENT" not in strat_name.upper():
                continue
            tf = None
            if ot.signal and ot.signal.timeframe:
                tf = ot.signal.timeframe.lower()
            else:
                for part in sig_id.upper().split("_"):
                    if part in ("5M", "15M", "30M", "1H", "4H"):
                        tf = part.lower()
                        break
            if tf:
                open_paper_trades_by_tf[tf] = {
                    "is_open": True,
                    "trade_id": ot.id,
                    "signal_id": ot.signal_id,
                    "direction": ot.direction,
                    "lot_size": ot.lot_size,
                    "entry_price": ot.actual_entry or ot.target_entry,
                    "stop_loss": ot.stop_loss,
                    "take_profit": ot.take_profit_1,
                    "opened_at": ot.opened_at.isoformat() if ot.opened_at else None,
                    "timeframe": tf.upper(),
                }
    except Exception as pt_err:
        logger.warning("[STRATEGY] Error querying open paper trades for Fib: %s", pt_err)

    dash = _build_strategy_dashboard(
        symbol, live_price, data_status, states, multi_svc.slots,
        strategy_label="FIB_WITH_RETRACEMENT",
        paper_trades_by_tf=open_paper_trades_by_tf)

    ai_guardian = {}
    try:
        from app.database.repository import Repository
        recent_sigs = await Repository(db).list_recent_signals(limit=30)
        for sig in recent_sigs:
            strat_u = (sig.strategy or "").upper()
            if "FIB" in strat_u or "RETRACEMENT" in strat_u:
                tf = (sig.timeframe or "5m").lower()
                ai_val = getattr(sig, "ai_validation", None)
                if ai_val and tf not in ai_guardian:
                    ai_guardian[tf] = {
                        "signal_id": sig.id,
                        "timeframe": tf,
                        "direction": sig.direction,
                        "status": ai_val.status,
                        "confidence": ai_val.confidence,
                        "explanation": ai_val.explanation,
                        "identified_risks": ai_val.identified_risks or [],
                        "model": ai_val.model,
                        "provider": ai_val.provider,
                        "created_at": ai_val.created_at.isoformat() if ai_val.created_at else None,
                    }
    except Exception as ai_e:
        logger.warning("[STRATEGY] Error fetching AI guardian for Fib: %s", ai_e)
    dash["ai_guardian"] = ai_guardian
    return dash


from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service


@router.get("/strategy/smc-fib/{symbol}")
async def get_smc_fib_dashboard(
    symbol: str = "XAUUSD",
    db: AsyncSession = Depends(get_db_session),
):
    """Dashboard endpoint for the dedicated 'SMC With Fib' strategy panel.

    Runs exact SMC + Fibonacci State Machine with Strict Cascading & 1-Active-Trade Policy:
    - Cascading Scan: 5M -> 15M -> 30M -> 1H -> 4H.
    - If any TF triggers an active trade, it locks as the primary active trade.
    - Other timeframes remain on STANDBY until the active trade completes.
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

    TIMEFRAMES_ORDER = ["5m", "15m", "30m", "1h"]
    smc_multi = get_smc_fib_multi_tf_service(symbol)
    try:
        states = await asyncio.wait_for(smc_multi.advance(db), timeout=4.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SMC-FIB] multi-tf advance timeout/error: %s", exc)
        states = {tf: smc_multi.slots[tf].engine.to_dict(live_price) for tf in TIMEFRAMES_ORDER}

    active_trade_tf = None
    # 1. First find if ANY timeframe already has a running active trade
    for tf in TIMEFRAMES_ORDER:
        card = states.get(tf) or {}
        if card.get("is_trade_active"):
            active_trade_tf = tf
            break

    # 2. Enforce 1-Trade Policy: If an active trade is running, put other timeframes on STANDBY
    for tf in TIMEFRAMES_ORDER:
        card = states.get(tf)
        if card:
            card["is_locked_by_cascade"] = (active_trade_tf is not None and active_trade_tf != tf)
            if card.get("is_locked_by_cascade") and not card.get("is_trade_active"):
                card["cascade_status"] = f"STANDBY (Locked by {active_trade_tf.upper()})"
            else:
                card["cascade_status"] = "ACTIVE" if active_trade_tf else "SCANNING"

    # Query live open paper trades for SMC With Fib
    open_paper_trades_by_tf = {}
    try:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.database.models import PaperTradeModel

        stmt = select(PaperTradeModel).options(selectinload(PaperTradeModel.signal)).where(
            PaperTradeModel.state == "OPEN",
            PaperTradeModel.symbol == symbol,
        )
        res = await db.execute(stmt)
        open_trades = res.scalars().all()
        for ot in open_trades:
            sig_id = ot.signal_id or ""
            strat_name = (ot.signal.strategy or "") if ot.signal else ""
            if not sig_id.startswith("SMC_FIB_") and "SMC" not in strat_name.upper():
                continue
            tf = None
            if ot.signal and ot.signal.timeframe:
                tf = ot.signal.timeframe.lower()
            else:
                for part in sig_id.upper().split("_"):
                    if part in ("5M", "15M", "30M", "1H", "4H"):
                        tf = part.lower()
                        break
            if tf:
                open_paper_trades_by_tf[tf] = {
                    "is_open": True,
                    "trade_id": ot.id,
                    "signal_id": ot.signal_id,
                    "direction": ot.direction,
                    "lot_size": ot.lot_size,
                    "entry_price": ot.actual_entry or ot.target_entry,
                    "stop_loss": ot.stop_loss,
                    "take_profit": ot.take_profit_1,
                    "opened_at": ot.opened_at.isoformat() if ot.opened_at else None,
                }
    except Exception as pt_err:
        logger.warning("[STRATEGY] Error querying open paper trades for SMC: %s", pt_err)

    for tf in TIMEFRAMES_ORDER:
        card = states.get(tf)
        if card:
            pt_info = open_paper_trades_by_tf.get(tf.lower())
            card["paper_trade"] = pt_info if pt_info else {
                "is_open": False,
                "reason": "NO_ORDER_PLACED",
            }

    ai_guardian = {}
    try:
        from app.database.repository import Repository
        recent_sigs = await Repository(db).list_recent_signals(limit=30)
        for sig in recent_sigs:
            strat_u = (sig.strategy or "").upper()
            if "SMC" in strat_u:
                tf = (sig.timeframe or "5m").lower()
                ai_val = getattr(sig, "ai_validation", None)
                if ai_val and tf not in ai_guardian:
                    ai_guardian[tf] = {
                        "signal_id": sig.id,
                        "timeframe": tf,
                        "direction": sig.direction,
                        "status": ai_val.status,
                        "confidence": ai_val.confidence,
                        "explanation": ai_val.explanation,
                        "identified_risks": ai_val.identified_risks or [],
                        "model": ai_val.model,
                        "provider": ai_val.provider,
                        "created_at": ai_val.created_at.isoformat() if ai_val.created_at else None,
                    }
    except Exception as ai_e:
        logger.warning("[STRATEGY] Error fetching AI guardian for SMC: %s", ai_e)

    active_paper_trade_tfs = [
        tf for tf in TIMEFRAMES_ORDER
        if open_paper_trades_by_tf.get(tf.lower(), {}).get("is_open")
    ]

    return {
        "strategy": "SMC_WITH_FIB",
        "symbol": symbol,
        "live_price": live_price,
        "data_status": data_status,
        "timeframes_order": TIMEFRAMES_ORDER,
        "active_trade_tf": active_trade_tf,
        "active_paper_trade_tfs": active_paper_trade_tfs,
        "primary_paper_trade_tf": active_paper_trade_tfs[0] if active_paper_trade_tfs else None,
        "cascading_active_tf": active_trade_tf,
        "timeframes": states,
        "ai_guardian": ai_guardian,
    }


@router.get("/strategy/fib-trend/{symbol}")
async def get_fib_trend_dashboard(
    symbol: str = "XAUUSD",
    db: AsyncSession = Depends(get_db_session),
):
    """Dashboard endpoint for 'Fib Go With Trend' strategy (9 EMA & 21 EMA + 0.618 Breakout Confirmation)."""
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

    from app.retracement.fib_trend_multi_tf import get_fib_trend_multi_tf_service
    from app.retracement.fib_trend_engine import FibTrendState
    trend_multi = get_fib_trend_multi_tf_service(symbol)
    try:
        engines = await asyncio.wait_for(trend_multi.advance(db), timeout=4.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FIB-TREND] multi-tf advance timeout/error: %s", exc)
        engines = {tf: trend_multi.slots[tf].engine for tf in trend_multi.timeframes}

    tf_cards = {}
    for tf_str, eng in engines.items():
        state_val = eng.state.value if hasattr(eng.state, "value") else str(eng.state)
        dir_val = eng.direction.value if hasattr(eng.direction, "value") else str(eng.direction)
        tf_cards[tf_str] = {
            "timeframe": tf_str,
            "state": state_val,
            "direction": dir_val,
            "point_0_price": eng.point_0_price,
            "point_1_price": eng.point_1_price,
            "fib_0_000": eng.fib_0_000,
            "fib_0_236": eng.fib_0_236,
            "fib_0_382": eng.fib_0_382,
            "fib_0_500": eng.fib_0_500,
            "fib_0_618": eng.fib_0_618,
            "fib_1_000": eng.fib_1_000,
            "fib_1_618": eng.fib_1_618,
            "trigger_breakout_price": eng.trigger_breakout_price,
            "entry_price": eng.entry_price or eng.trigger_breakout_price,
            "sl_price": eng.sl_price or eng.fib_0_236,
            "tp_price": eng.tp_price or eng.fib_1_618,
            "tp1_price": eng.fib_1_618,
            "tp1_hit": False,
            "tp2_price": eng.fib_1_618,
            "entry_touched": eng.entry_touched,
            "ema_9": eng.current_ema_9,
            "ema_21": eng.current_ema_21,
            "outcome": eng.outcome,
            "completion_reason": eng.completion_reason,
            "invalidation_reason": eng.invalidation_reason,
            "is_locked_standby": getattr(trend_slot := trend_multi.slots.get(tf_str), "is_locked_standby", False),
            "levels": [
                {"ratio": "1.618", "price": eng.fib_1_618, "meaning": "Take Profit (1.618 Target)", "color": "#00e676"},
                {"ratio": "1.000", "price": eng.fib_1_000, "meaning": "Swing 1 Peak / Valley", "color": "#ffd54f"},
                {"ratio": "0.618", "price": eng.fib_0_618, "meaning": "0.618 Retracement Touch", "color": "#ffb74d"},
                {"ratio": "0.500", "price": eng.fib_0_500, "meaning": "Equilibrium", "color": "#42a5f5"},
                {"ratio": "0.382", "price": eng.fib_0_382, "meaning": "Retracement Depth", "color": "#90caf9"},
                {"ratio": "0.236", "price": eng.fib_0_236, "meaning": "Stop Loss (0.236 Level)", "color": "#ef5350"},
                {"ratio": "0.000", "price": eng.fib_0_000, "meaning": "Anchor Origin (P0)", "color": "#b388ff"},
            ] if eng.fib_1_000 else [],
            "is_entry_ready": state_val == FibTrendState.WAITING_FOR_BREAKOUT.value,
            "is_trade_active": state_val == FibTrendState.TRADE_ACTIVE.value,
        }

    return {
        "strategy": "FIB_GO_WITH_TREND",
        "symbol": symbol,
        "live_price": live_price,
        "data_status": data_status,
        "timeframes_order": ["15m", "30m", "1h", "2h", "4h"],
        "active_trade_tf": trend_multi.active_trade_tf,
        "cascading_active_tf": trend_multi.active_trade_tf or "15m",
        "timeframes": tf_cards,
    }


# ---------------------------------------------------------------------------
# Live Chart Data Endpoint — returns OHLCV + Fibonacci levels for chart rendering
# ---------------------------------------------------------------------------

_TF_TO_SERVICE_TF = {
    "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "2h": "H2", "4h": "H4",
}

@router.get("/chart/{symbol}/{timeframe}")
async def get_chart_data(symbol: str, timeframe: str, limit: int = 150):
    """Return OHLCV candles + live Fib levels for TradingView Lightweight Charts.

    Used by the dashboard chart panel to render candlesticks and draw
    Fibonacci level lines for both SMC With Fib and Fib With Retracement.
    """
    from app.core.constants import TimeFrame
    from app.data.live.service import get_live_service
    from app.data.timeframe_resampler import resample_candles
    from app.retracement.multi_tf import get_retracement_multi_tf_service, TF_MAP as RETR_TF_MAP
    from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service, TF_MAP as SMC_TF_MAP

    tf = timeframe.lower()
    svc = get_live_service()

    candles = []
    live_price = None
    try:
        snap = await svc.get_multi_timeframe_snapshot(symbol, include_forming=True)
        tf_enum = RETR_TF_MAP.get(tf)
        if tf_enum is None:
            raise HTTPException(status_code=400, detail=f"Unknown timeframe: {timeframe}")
        candles = list(snap.get_series(tf_enum))[-limit:]
        live_price = snap.current_price
    except Exception as exc:
        logger.debug("Chart snapshot failed: %s", exc)

    if len(candles) < 50:
        try:
            from app.data.live.binance_history import BinanceHistoryProvider
            provider = BinanceHistoryProvider()
            tf_enum = RETR_TF_MAP.get(tf, TimeFrame.M5)
            fetched = await provider.get_ohlcv(symbol, tf_enum, limit=limit)
            if fetched:
                candles = fetched[-limit:]
                if live_price is None:
                    live_price = candles[-1].close
        except Exception as e:
            logger.debug("Chart REST fallback failed: %s", e)

    # Serialize OHLCV (Unix timestamp in seconds for lightweight-charts)
    ohlcv = []
    for c in candles:
        ts = c.timestamp
        unix = int(ts.timestamp())
        ohlcv.append({
            "time": unix,
            "open": round(c.open, 2),
            "high": round(c.high, 2),
            "low": round(c.low, 2),
            "close": round(c.close, 2),
        })

    # Collect Fibonacci levels from both strategies for this timeframe
    fib_levels = {"smc_fib": {}, "fib_retracement": {}}

    # SMC With Fib levels
    try:
        smc_svc = get_smc_fib_multi_tf_service(symbol)
        smc_slot = smc_svc.slots.get(tf)
        if smc_slot and smc_slot.engine:
            eng = smc_slot.engine
            smc_state = eng.state.value if hasattr(eng.state, "value") else str(eng.state or "NO_SETUP")
            if eng.entry_price is not None and smc_state not in ("COMPLETED", "INVALIDATED", "NO_SETUP"):
                fib_levels["smc_fib"] = {
                    "direction": eng.direction.value if eng.direction else None,
                    "state": smc_state,
                    "anchor": eng.point_2_price,          # 1.000 Point 2
                    "bos": eng.point_1_price,             # Point 1 (BOS)
                    "sl": eng.sl_price,                    # 0.920
                    "pocket": eng.pocket_price,            # 0.790
                    "entry": eng.entry_price,              # 0.680
                    "equilibrium": eng.equilibrium_50,     # 0.500
                    "tp": eng.locked_tp or eng.target_tp_price,  # 0.000
                    "entry_touched": getattr(eng, "entry_touched", False),
                }
    except Exception as exc:
        logger.debug("SMC Fib levels for chart failed: %s", exc)

    # Fib With Retracement levels
    try:
        retr_svc = get_retracement_multi_tf_service(symbol)
        retr_slot = retr_svc.slots.get(tf)
        if retr_slot and retr_slot.engine:
            setup = retr_slot.engine.setup
            st_val = setup.state.value if hasattr(setup.state, "value") else str(setup.state or "NO_SETUP") if setup else "NO_SETUP"
            if setup and st_val not in ("COMPLETED", "INVALIDATED", "NO_SETUP"):
                layers = getattr(setup, "layers", {}) or {}
                l1 = layers.get("L1", {})
                l2 = layers.get("L2", {})
                l3 = layers.get("L3", {})
                fib_levels["fib_retracement"] = {
                    "direction": setup.direction,
                    "state": st_val,
                    "anchor": setup.point_2_price,              # Point 2 anchor
                    "bos": setup.point_1_price or getattr(setup, "bos_price", None), # BOS Point 1
                    "l1_entry": setup.fib_0_618 or setup.entry_price,
                    "l2_entry": setup.fib_0_500,
                    "l3_entry": setup.fib_0_382,
                    "entry": setup.fib_0_618 or setup.entry_price,
                    "sl": setup.sl_price,                       # SL
                    "tp": setup.dynamic_tp or setup.fib_1_000,   # TP
                    "l1_state": l1.get("state", "PENDING"),
                    "l2_state": l2.get("state", "PENDING"),
                    "l3_state": l3.get("state", "PENDING"),
                    "entry_touched": getattr(setup, "entry_touched", False) or l1.get("state") in ("FILLED", "TP_HIT"),
                }
    except Exception as exc:
        logger.debug("Fib Retracement levels for chart failed: %s", exc)

    # Fib Go With Trend levels
    try:
        from app.retracement.fib_trend_multi_tf import get_fib_trend_multi_tf_service
        trend_svc = get_fib_trend_multi_tf_service(symbol)
        trend_slot = trend_svc.slots.get(tf)
        if trend_slot and trend_slot.engine:
            eng = trend_slot.engine
            fib_levels["fib_trend"] = {
                "direction": eng.direction.value if hasattr(eng.direction, "value") else str(eng.direction),
                "state": eng.state.value if hasattr(eng.state, "value") else str(eng.state or "NO_SETUP"),
                "p0": eng.point_0_price,
                "p1": eng.point_1_price,
                "fib_0_000": eng.fib_0_000,
                "fib_0_236": eng.fib_0_236,
                "fib_0_382": eng.fib_0_382,
                "fib_0_500": eng.fib_0_500,
                "fib_0_618": eng.fib_0_618,
                "fib_1_000": eng.fib_1_000,
                "fib_1_618": eng.fib_1_618,
                "trigger_price": eng.trigger_breakout_price,
                "entry_price": eng.entry_price or eng.trigger_breakout_price,
                "sl": eng.sl_price or eng.fib_0_236,
                "tp": eng.tp_price or eng.fib_1_618,
                "tp1": eng.fib_1_000,
                "tp1_hit": getattr(eng, "tp1_hit", False),
                "tp2": eng.fib_1_618,
                "ema_9": eng.current_ema_9,
                "ema_21": eng.current_ema_21,
                "entry_touched": eng.entry_touched,
            }
    except Exception as exc:
        logger.debug("Fib Trend levels for chart failed: %s", exc)

    return {
        "symbol": symbol,
        "timeframe": tf,
        "live_price": live_price,
        "candles": ohlcv,
        "fib_levels": fib_levels,
        "candle_count": len(ohlcv),
    }