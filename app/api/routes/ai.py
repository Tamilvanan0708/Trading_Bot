"""
AI Validation & Decision Audit API.

GET /ai/validation/{symbol} — consolidated decision-audit payload.

AI is ADVISORY ONLY.  Deterministic safety gates have final authority.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, get_settings
from app.api.routes.analysis import get_live_analysis
from app.core.constants import MarketBias, SignalDirection, SignalQuality
from app.core.logging import logger
from app.data.live.service import get_live_service
from app.database.connection import get_db_session
from app.database.repository import Repository
from app.retracement.live import get_retracement_live_service
from app.retracement.models import RetracementState, to_spec_state

router = APIRouter(prefix="/ai", tags=["AI Validation"])


# ---------------------------------------------------------------------------
# Deterministic gate evaluation
# ---------------------------------------------------------------------------

def _gate_eval(settings: Settings, analysis: dict, degraded: bool) -> dict:
    """Evaluate all deterministic safety gates and produce the final decision."""
    conf = analysis.get("confluence") or {}
    total = float(conf.get("total_score", 0.0))
    sig = analysis.get("signal") or {}
    direction = sig.get("direction", "NO_TRADE")
    quality = sig.get("signal_quality", "NO_TRADE")
    rr = float(sig.get("risk_reward", 0.0))
    mb = analysis.get("market_bias") or {}
    conflicts = conf.get("conflicts", [])

    gates = []
    # Data Quality
    dq_ok = not degraded
    gates.append({
        "name": "Data Quality",
        "status": "PASS" if dq_ok else "FAIL",
        "detail": "HEALTHY" if dq_ok else (analysis.get("data_quality", {}).get("degradation_reason", "DEGRADED")),
    })

    # Confluence
    conf_ok = total >= settings.THRESHOLD_STRONG
    gates.append({
        "name": "Confluence",
        "status": "PASS" if conf_ok else "FAIL",
        "detail": f"{total:.0f} / 100 (required >= {settings.THRESHOLD_STRONG})",
    })

    # Signal — a directional candidate exists (quality shown separately)
    signal_ok = direction != "NO_TRADE"
    gates.append({
        "name": "Signal",
        "status": "PASS" if signal_ok else "FAIL",
        "detail": f"{direction} / {quality}",
    })

    # Risk
    risk_ok = rr >= settings.MIN_RISK_REWARD
    gates.append({
        "name": "Risk",
        "status": "PASS" if risk_ok else "FAIL",
        "detail": f"R:R 1:{rr:.2f} (min 1:{settings.MIN_RISK_REWARD})",
    })

    # Timeframe direct conflict
    tf_conflict = len(conflicts) > 0
    gates.append({
        "name": "Timeframe Alignment",
        "status": "FAIL" if tf_conflict else "PASS",
        "detail": conflicts[0] if tf_conflict else "No direct conflicts",
    })

    # Final authority: deterministic hard gates.
    # A missing directional candidate is NOT a hard fail — it is precisely the
    # state between "NO_TRADE" and "WATCH": confluence is monitored while no
    # setup exists yet.  Risk quality only hard-fails when a candidate IS
    # present (rr defaults to 0.0 for NO_TRADE signals).
    has_candidate = signal_ok
    risk_hard_fail = has_candidate and not risk_ok
    hard_fail = (not dq_ok) or risk_hard_fail or tf_conflict
    if hard_fail or (total >= settings.THRESHOLD_STRONG and not has_candidate):
        decision = "NO_TRADE"
    elif total >= settings.THRESHOLD_STRONG:
        decision = "VALIDATED_SIGNAL"
    elif total >= settings.THRESHOLD_MODERATE:
        decision = "WATCH"
    else:
        decision = "NO_TRADE"

    return {
        "decision": decision,
        "confluence": total,
        "confluence_threshold": settings.THRESHOLD_STRONG,
        "signal_direction": direction,
        "signal_quality": quality,
        "risk_gate": "PASS" if risk_ok else "FAIL",
        "risk_reward": rr,
        "min_risk_reward": settings.MIN_RISK_REWARD,
        "data_quality": "PASS" if dq_ok else "FAIL",
        "gates": gates,
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# Timeframe alignment
# ---------------------------------------------------------------------------

def _timeframe_alignment(analysis: dict) -> dict:
    """Build the timeframe confluence matrix."""
    mb = analysis.get("market_bias") or {}
    conf = analysis.get("confluence") or {}
    sig = analysis.get("signal") or {}
    direction = sig.get("direction", "NO_TRADE")
    # Macro bias = 4h trend (reference for alignment)
    h4 = (mb.get("4h") or {}).get("trend", "NEUTRAL")
    h1 = (mb.get("1h") or {}).get("trend", "NEUTRAL")
    m15 = (mb.get("15m") or {}).get("trend", "NEUTRAL")

    # 30m setup: fib direction + smc zone
    fib = analysis.get("fibonacci_setup")
    smc = analysis.get("smc_analysis") or {}
    m30_dir = "NEUTRAL"
    if fib and fib.get("direction"):
        m30_dir = "BULLISH" if fib["direction"] == "LONG" else "BEARISH" if fib["direction"] == "SHORT" else "NEUTRAL"
    smc_zone = smc.get("current_zone", "N/A")

    def align(trend: str) -> tuple[str, str]:
        if trend == "NEUTRAL" or trend == "RANGING":
            return "NEUTRAL", "NEUTRAL"
        if trend == h4.replace("RANGING", "NEUTRAL"):
            return "ALIGNED", "ALIGNED"
        return "CONFLICT", "CONFLICT"

    rows = []
    rows.append({"timeframe": "4H", "direction": h4, "structure": mb.get("4h", {}).get("summary", ""), "signal": "—", "status": "ALIGNED"})
    h1_st, h1_al = align(h1)
    rows.append({"timeframe": "1H", "direction": h1, "structure": mb.get("1h", {}).get("summary", ""), "signal": "—", "status": h1_st or h1_al})
    m30_st = "NEUTRAL" if m30_dir == "NEUTRAL" else ("ALIGNED" if m30_dir == h4.replace("RANGING", "NEUTRAL") else "CONFLICT")
    rows.append({"timeframe": "30M", "direction": m30_dir, "structure": f"Setup (SMC: {smc_zone})", "signal": fib.get("direction", "—") if fib else "—", "status": m30_st})
    m15_st, m15_al = align(m15)
    rows.append({"timeframe": "15M", "direction": m15, "structure": mb.get("15m", {}).get("summary", ""), "signal": direction, "status": m15_st or m15_al})

    aligned = sum(1 for r in rows if r["status"] == "ALIGNED")
    conflicts = sum(1 for r in rows if r["status"] == "CONFLICT")
    neutrals = sum(1 for r in rows if r["status"] == "NEUTRAL")
    total = len(rows)
    if aligned == total:
        status = "ALIGNED"
    elif conflicts > 0:
        status = "CONFLICT"
    elif neutrals > 0:
        status = "PARTIAL"
    else:
        status = "ALIGNED"

    return {"rows": rows, "aligned_count": aligned, "total": total, "status": status}


# ---------------------------------------------------------------------------
# Risk validation
# ---------------------------------------------------------------------------

def _risk_validation(settings: Settings, analysis: dict) -> dict:
    """Validate entry/SL/TP geometry and risk gate."""
    sig = analysis.get("signal") or {}
    direction = sig.get("direction", "NO_TRADE")
    entry = float(sig.get("entry", 0.0))
    sl = float(sig.get("stop_loss", 0.0))
    tp1 = float(sig.get("take_profit_1", 0.0))
    rr = float(sig.get("risk_reward", 0.0))
    quality = sig.get("signal_quality", "NO_TRADE")
    tradable = direction != "NO_TRADE" and quality in ("STRONG", "VERY_STRONG")

    entry_valid = entry > 0
    if direction == "LONG":
        sl_valid = sl > 0 and sl < entry
        tp_valid = tp1 > entry
    elif direction == "SHORT":
        sl_valid = sl > 0 and sl > entry
        tp_valid = tp1 < entry and tp1 > 0
    else:
        sl_valid = sl > 0
        tp_valid = tp1 > 0

    rr_ok = rr >= settings.MIN_RISK_REWARD
    gate = "PASS" if (tradable and rr_ok and entry_valid and sl_valid and tp_valid) else "FAIL"

    # Derive risk level from R:R
    if not tradable:
        level = "N/A"
    elif rr >= 2.5:
        level = "LOW"
    elif rr >= settings.MIN_RISK_REWARD:
        level = "MEDIUM"
    else:
        level = "HIGH"

    return {
        "gate": gate,
        "level": level,
        "entry_valid": entry_valid,
        "sl_valid": sl_valid,
        "tp_valid": tp_valid,
        "risk_reward": rr,
        "min_risk_reward": settings.MIN_RISK_REWARD,
        "details": [
            f"R:R 1:{rr:.2f} (min 1:{settings.MIN_RISK_REWARD})",
            f"Entry {'valid' if entry_valid else 'INVALID'} ({entry:.2f})",
            f"SL {'valid' if sl_valid else 'INVALID'} ({sl:.2f})",
            f"TP {'valid' if tp_valid else 'INVALID'} ({tp1:.2f})",
        ],
    }


# ---------------------------------------------------------------------------
# Evidence verification
# ---------------------------------------------------------------------------

def _verify_evidence(settings: Settings, analysis: dict, retr: dict | None,
                     ai: dict | None) -> list[dict]:
    """Verify AI claims against deterministic backend data."""
    total = float(analysis.get("confluence", {}).get("total_score", 0.0))
    sig = analysis.get("signal") or {}
    mb = analysis.get("market_bias") or {}
    deg = analysis.get("degraded", False)
    evidence = []

    def add(claim: str, actual: str, ok: bool, note: str = "") -> None:
        evidence.append({
            "claim": claim,
            "actual": str(actual),
            "status": "VERIFIED" if ok else ("NOT_VERIFIED" if not note else "MISMATCH"),
            "note": note,
        })

    ai_status = (ai or {}).get("status", "UNAVAILABLE")
    add("AI decision", str(ai_status), ai_status in ("APPROVE", "CAUTION", "REJECT", "UNAVAILABLE"))
    add("Confluence score", f"{total:.0f} / 100", True)
    add("4H bias", str((mb.get("4h") or {}).get("trend", "NEUTRAL")), True)
    add("1H bias", str((mb.get("1h") or {}).get("trend", "NEUTRAL")), True)
    add("15M bias", str((mb.get("15m") or {}).get("trend", "NEUTRAL")), True)
    add("Signal direction", sig.get("direction", "NO_TRADE"), True)

    entry_touched = bool(retr and retr.get("entry_touched"))
    add("Entry touched", "TRUE" if entry_touched else "FALSE", entry_touched)

    tp_locked = bool(retr and retr.get("tp_locked"))
    add("TP frozen", "TRUE" if tp_locked else "FALSE", tp_locked)

    # AI-vs-deterministic mismatch detection
    if ai_status == "APPROVE":
        if total < settings.THRESHOLD_STRONG:
            add("AI approve vs confluence",
                f"AI APPROVE but confluence {total:.0f} < {settings.THRESHOLD_STRONG}",
                False, "AI approved below the deterministic confluence threshold.")
        rr = float(sig.get("risk_reward", 0.0))
        if rr < settings.MIN_RISK_REWARD:
            add("AI approve vs risk",
                f"AI APPROVE but R:R 1:{rr:.2f} < 1:{settings.MIN_RISK_REWARD}",
                False, "AI approved while the risk gate fails.")

    # Scan AI text for specific claims
    if ai:
        ai_text = " ".join(filter(None, [
            ai.get("explanation", "") or "",
            ai.get("reasoning", "") or "",
            *ai.get("identified_risks", []),
            *ai.get("missing_confirmations", []),
        ]))
        ai_text_lower = ai_text.lower()
        if "touch" in ai_text_lower or "entry" in ai_text_lower:
            et = bool(retr and retr.get("entry_touched"))
            if not et:
                add("AI claim 'entry touched'", "FALSE", False,
                    "AI mentions entry touch but backend says entry not touched.")
        if "frozen" in ai_text_lower or "lock" in ai_text_lower:
            fl = bool(retr and retr.get("tp_locked"))
            if not fl:
                add("AI claim 'TP frozen'", "FALSE", False,
                    "AI mentions frozen/locked TP but TP is not locked.")

    return evidence


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def _lifecycle(retr: dict | None, analysis: dict) -> dict:
    """Build the signal lifecycle step table."""
    if retr is None:
        return {"steps": [], "no_setup": True}
    state = retr.get("state", "NO_SETUP")
    bos = retr.get("bos") and retr["bos"].get("price")
    p2 = retr.get("point_2") and retr["point_2"].get("price")
    entry_touched = bool(retr.get("entry_touched"))
    tp_locked = bool(retr.get("tp_locked"))
    steps = [
        ("BOS", "✅" if bos else "❌", True),
        ("POINT 2", "✅" if p2 else "❌", bool(p2)),
        ("FIB ACTIVE", "✅" if state in ("FIB_ACTIVE", "TP_DYNAMIC", "ENTRY_TOUCHED", "TP_FROZEN", "TRADE_ACTIVE") else "⏳", bool(state not in ("NO_SETUP", "BOS_DETECTED", "POINT_2_IDENTIFIED"))),
        ("TRACKING HIGH", "✅" if state in ("TP_DYNAMIC", "ENTRY_TOUCHED", "TP_FROZEN", "TRADE_ACTIVE") else "⏳", bool(state in ("TP_DYNAMIC", "ENTRY_TOUCHED", "TP_FROZEN", "TRADE_ACTIVE"))),
        ("WAITING FOR ENTRY", "✅" if not entry_touched and state in ("TP_DYNAMIC",) else "⏳", bool(not entry_touched and state in ("TP_DYNAMIC",))),
        ("ENTRY TOUCHED", "✅" if entry_touched and not tp_locked else ("✅" if entry_touched else "⏳"), bool(entry_touched)),
        ("TP FROZEN", "✅" if tp_locked else "⏳", bool(tp_locked)),
        ("TRADE ACTIVE", "✅" if state == "TRADE_ACTIVE" else "⏳", bool(state == "TRADE_ACTIVE")),
    ]
    return {"steps": steps, "no_setup": False}


# ---------------------------------------------------------------------------
# What must change
# ---------------------------------------------------------------------------

def _what_must_change(settings: Settings, det: dict, retr: dict | None) -> dict:
    """Generate the "what must change" action items."""
    items = []
    total = float(det["confluence"])
    if total < settings.THRESHOLD_STRONG:
        items.append({
            "area": "Confluence",
            "current": f"{total:.0f} / 100",
            "required": f">= {settings.THRESHOLD_STRONG}",
            "status": "FAIL",
        })
    if det["risk_gate"] == "FAIL":
        items.append({
            "area": "Risk",
            "current": f"R:R 1:{det['risk_reward']:.2f}",
            "required": f">= 1:{settings.MIN_RISK_REWARD}",
            "status": "FAIL",
        })
    if det["signal_direction"] == "NO_TRADE":
        items.append({
            "area": "Signal Direction",
            "current": "NO_TRADE",
            "required": "LONG / SHORT",
            "status": "FAIL",
        })
    if det["data_quality"] == "FAIL":
        items.append({
            "area": "Data Quality",
            "current": "DEGRADED",
            "required": "HEALTHY",
            "status": "FAIL",
        })
    if det.get("conflicts"):
        items.append({
            "area": "Timeframe Conflict",
            "current": "; ".join(det["conflicts"]),
            "required": "No direct conflicts",
            "status": "FAIL",
        })
    if retr and not retr.get("entry_touched"):
        items.append({
            "area": "Entry",
            "current": "NOT TOUCHED",
            "required": "TOUCHED",
            "status": "WAITING",
        })
    action = "WAIT" if det["decision"] == "NO_TRADE" else ("MONITOR" if det["decision"] == "WATCH" else "READY")
    return {"items": items, "action": action}


# ---------------------------------------------------------------------------
# AI health
# ---------------------------------------------------------------------------

def _ai_health(settings: Settings) -> dict:
    """Build AI system health status."""
    from app.ai.providers import get_provider_status
    providers = get_provider_status(settings)
    active = [p for p, d in providers.items() if d.get("configured")]
    any_healthy = any(d.get("status") == "AVAILABLE" for p, d in providers.items() if d.get("configured"))
    overall = "HEALTHY" if any_healthy else ("DEGRADED" if active else "OFFLINE")
    return {
        "status": overall,
        "configured_providers": active,
        "providers": providers,
        "advisory_only": True,
    }


# ---------------------------------------------------------------------------
# Performance summary
# ---------------------------------------------------------------------------

def _performance_summary(history: list[dict]) -> dict:
    """Compute AI validation performance metrics from history."""
    if len(history) < 5:
        return {"status": "INSUFFICIENT_DATA", "total_validations": len(history)}
    total = len(history)
    approved = sum(1 for h in history if h.get("ai_decision") == "APPROVE")
    rejected = sum(1 for h in history if h.get("ai_decision") == "REJECT")
    caution = sum(1 for h in history if h.get("ai_decision") == "CAUTION")
    none_ = sum(1 for h in history if h.get("ai_decision") in ("NONE", "UNAVAILABLE"))
    return {
        "status": "AVAILABLE",
        "total_validations": total,
        "approved": approved,
        "rejected": rejected,
        "caution": caution,
        "unavailable": none_,
        "approval_rate": round(approved / total * 100, 1) if total else 0.0,
        "rejection_rate": round(rejected / total * 100, 1) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def _timeline(history: list[dict]) -> list[dict]:
    """Build a decision-change timeline from history."""
    events = []
    for h in history:
        events.append({
            "time": h.get("time"),
            "direction": h.get("direction", "—"),
            "ai_decision": h.get("ai_decision", "NONE"),
            "ai_confidence": h.get("ai_confidence", 0.0),
            "confluence": h.get("confidence_score", 0.0),
        })
    return events[:50]


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@router.get("/validation/{symbol}")
async def ai_validation_dashboard(symbol: str = "XAUUSD",
                                   db: AsyncSession = Depends(get_db_session)):
    """Consolidated AI decision-audit payload.

    AI is ADVISORY ONLY.  Deterministic safety gates have final authority.
    """
    settings = get_settings()
    live_service = get_live_service()
    now = datetime.now(timezone.utc)

    # 1. Data quality gate
    try:
        data_quality = await live_service.data_quality()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AI] data_quality failed: %s", exc)
        data_quality = None
    degraded = data_quality is not None and bool(data_quality.degraded)

    # 2. Live price
    live_price = None
    try:
        live_price = await live_service.get_latest_price(symbol)
    except Exception:  # noqa: BLE001
        pass

    # 3. Run analysis pipeline (best-effort, time-bounded)
    # NOTE: get_live_analysis is imported at module level so callers/tests can
    # monkeypatch this module's attribute; do not re-import it locally here.
    analysis = None
    try:
        analysis = await get_live_analysis(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AI] analysis pipeline failed: %s", exc)
        analysis = {
            "symbol": symbol,
            "timestamp": now,
            "current_price": live_price,
            "degraded": True,
            "degradation_reason": "Analysis pipeline unavailable.",
            "data_quality": data_quality.model_dump(mode="json") if data_quality else {"degraded": True},
            "market_bias": {"4h": {"trend": "NEUTRAL", "summary": "Pipeline unavailable."},
                            "1h": {"trend": "NEUTRAL", "summary": ""},
                            "15m": {"trend": "NEUTRAL", "summary": ""}},
            "fibonacci_setup": None,
            "smc_analysis": {"current_zone": "N/A", "equilibrium_price": 0, "active_fvgs": []},
            "confluence": {"total_score": 0.0, "quality": "NO_TRADE", "is_tradable": False, "conflicts": []},
            "signal": {"direction": "NO_TRADE", "strategy": "CONFLUENCE", "entry": live_price or 0.0,
                       "stop_loss": live_price or 0.0, "take_profit_1": live_price or 0.0,
                       "take_profit_2": live_price or 0.0, "take_profit_3": live_price or 0.0,
                       "risk_reward": 0.0, "confidence_score": 0.0, "signal_quality": "NO_TRADE",
                       "reasons": ["Analysis pipeline unavailable."]},
            "ai_validation": {"status": "UNAVAILABLE", "confidence": 0.0,
                              "explanation": "Analysis pipeline unavailable.",
                              "identified_risks": [], "missing_confirmations": [],
                              "provider": "NONE"},
            "explanation": "NO TRADE — analysis pipeline unavailable.",
        }

    ai = analysis.get("ai_validation") or {}
    # Determine AI provider status (HEALTHY / DEGRADED / OFFLINE / UNAVAILABLE)
    ai_status = ai.get("status", "UNAVAILABLE")
    if ai_status == "UNAVAILABLE":
        ai_provider_status = "UNAVAILABLE"
        ai_provider_name = ai.get("provider", "NONE")
    elif ai.get("provider") in ("HEURISTIC", "NONE") or not ai.get("provider"):
        ai_provider_status = "DEGRADED"
        ai_provider_name = ai.get("provider", "HEURISTIC")
    else:
        ai_provider_status = "HEALTHY"
        ai_provider_name = ai.get("provider", "UNKNOWN")

    # 4. Retracement state
    retr = None
    try:
        live_svc = get_retracement_live_service(symbol)
        retr_setup = await live_svc.advance(db)
        if retr_setup is not None:
            from app.api.routes.retracement import _serialize_setup
            retr = _serialize_setup(retr_setup, live_price=live_price, data_status="HEALTHY" if not degraded else "NO_DATA")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AI] retracement state unavailable: %s", exc)
        try:
            from app.retracement.repository import RetracementRepository
            repo = RetracementRepository(db)
            retr_setup = await repo.load_latest_active(symbol, strategy="RETRACEMENT_BOS_V1")
            if retr_setup is not None:
                from app.api.routes.retracement import _serialize_setup
                retr = _serialize_setup(retr_setup, live_price=live_price, data_status="HISTORICAL")
        except Exception:  # noqa: BLE001
            pass

    # 5. Deterministic gate evaluation
    det = _gate_eval(settings, analysis, degraded)

    # 6. Timeframe alignment
    tf = _timeframe_alignment(analysis)

    # 7. Risk validation
    risk = _risk_validation(settings, analysis)

    # 8. Evidence verification
    evidence = _verify_evidence(settings, analysis, retr, ai)

    # 9. Lifecycle
    lifecycle = _lifecycle(retr, analysis)

    # 10. What must change
    wmc = _what_must_change(settings, det, retr)

    # 11. AI health
    health = _ai_health(settings)

    # 12. AI validation history
    history = []
    try:
        repo = Repository(db)
        history = await repo.list_ai_validation_history(limit=50)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AI] history unavailable: %s", exc)

    # 13. Performance
    performance = _performance_summary(history)

    # 14. Timeline
    timeline = _timeline(history)

    # 15. Market snapshot
    market_snapshot = {
        "symbol": symbol,
        "current_price": live_price,
        "data_status": "HEALTHY" if not degraded else "DEGRADED",
        "timestamp": now.isoformat(),
        "4h_bias": (analysis.get("market_bias") or {}).get("4h", {}).get("trend", "NEUTRAL"),
        "1h_bias": (analysis.get("market_bias") or {}).get("1h", {}).get("trend", "NEUTRAL"),
        "15m_bias": (analysis.get("market_bias") or {}).get("15m", {}).get("trend", "NEUTRAL"),
        "timeframe": "15m",
    }

    return {
        "symbol": symbol,
        "timestamp": now.isoformat(),
        "current_price": live_price,
        "data_status": "HEALTHY" if not degraded else "DEGRADED",
        "market_snapshot": market_snapshot,
        "ai": {
            "decision": ai_status,
            "confidence": float(ai.get("confidence", 0.0)),
            "status": ai_provider_status,
            "explanation": ai.get("explanation", ""),
            "reasoning": ai.get("reasoning", ""),
            "risk_flags": ai.get("identified_risks", []),
            "missing_confirmations": ai.get("missing_confirmations", []),
            "provider": ai_provider_name,
            "model": ai.get("model", ""),
            "reason_code": ai.get("reason_code", ""),
            "advisory_only": True,
        },
        "deterministic": det,
        "timeframes": tf,
        "retracement": retr,
        "risk": risk,
        "evidence": evidence,
        "lifecycle": lifecycle,
        "what_must_change": wmc,
        "health": health,
        "history": history,
        "performance": performance,
        "timeline": timeline,
    }