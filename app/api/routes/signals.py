"""
Signals API Routes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db_session
from app.database.repository import Repository

router = APIRouter(prefix="/signals", tags=["Trading Signals"])

_last_signals_sync_ts: float = 0.0


@router.get("")
async def list_signals(limit: int = 50, db: AsyncSession = Depends(get_db_session)):
    """Lists trading signals exclusively from our dedicated Strategies:
    - SMC With Fib (Single @ 0.680)
    - Fib With Retracement (L1 @ 0.618, L2 @ 0.500, L3 @ 0.382)
    - Fib Go With Trend (9/21 EMA + 0.618 Breakout Confirmation)

    Reads directly from the database without blocking on live network or advance locks.
    """
    try:
        repo = Repository(db)
        raw_signals = await repo.list_recent_signals(limit=limit * 5)
        signals = [
            s for s in raw_signals
            if s.strategy in ("SMC_WITH_FIB", "FIB_WITH_RETRACEMENT", "RETRACEMENT", "FIB_GO_WITH_TREND")
        ][:limit]

        output = []
        for s in signals:
            ai_data = None
            try:
                if s.ai_validation is not None:
                    ai_data = {
                        "status": getattr(s.ai_validation, "status", None),
                        "confidence": getattr(s.ai_validation, "confidence", None),
                        "explanation": getattr(s.ai_validation, "explanation", None),
                    }
            except Exception:
                ai_data = None

            created_iso = None
            if s.created_at:
                try:
                    created_iso = s.created_at.isoformat()
                    if not created_iso.endswith("Z") and "+" not in created_iso:
                        created_iso += "Z"
                except Exception:
                    created_iso = str(s.created_at)

            output.append({
                "id": s.id,
                "created_at": created_iso,
                "symbol": s.symbol,
                "direction": s.direction,
                "strategy": s.strategy,
                "strategy_version": s.strategy_version,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "take_profit_1": s.take_profit_1,
                "take_profit_2": s.take_profit_2,
                "take_profit_3": s.take_profit_3,
                "risk_reward": s.risk_reward,
                "confidence_score": s.confidence_score,
                "signal_quality": s.signal_quality,
                "market_bias": s.market_bias,
                "regime": s.regime,
                "session": s.session,
                "outcome": s.outcome,
                "final_r": s.final_r,
                "max_favorable_excursion_r": s.max_favorable_excursion_r,
                "max_adverse_excursion_r": s.max_adverse_excursion_r,
                "reasons": s.reasons if isinstance(s.reasons, list) else [],
                "ai_validation": ai_data,
            })
        return output
    except Exception as exc:  # noqa: BLE001
        logger.error("[SIGNALS-API] Failed to retrieve signals: %s", exc)
        return []


@router.get("/{signal_id}")
async def get_signal_detail(signal_id: str, db: AsyncSession = Depends(get_db_session)):
    """Gets complete details for a specific signal."""
    repo = Repository(db)
    signal = await repo.get_signal_by_id(signal_id)
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found.")

    return {
        "id": signal.id,
        "created_at": signal.created_at,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "strategy": signal.strategy,
        "strategy_version": signal.strategy_version,
        "entry_price": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "take_profit_1": signal.take_profit_1,
        "take_profit_2": signal.take_profit_2,
        "take_profit_3": signal.take_profit_3,
        "risk_reward": signal.risk_reward,
        "confidence_score": signal.confidence_score,
        "signal_quality": signal.signal_quality,
        "market_bias": signal.market_bias,
        "reasons": signal.reasons,
        "invalidation_conditions": signal.invalidation_conditions,
        "metadata_payload": signal.metadata_payload,
        "regime": signal.regime,
        "session": signal.session,
        "outcome": signal.outcome,
        "final_r": signal.final_r,
        "max_favorable_excursion_r": signal.max_favorable_excursion_r,
        "max_adverse_excursion_r": signal.max_adverse_excursion_r,
        "time_to_outcome_hours": signal.time_to_outcome_hours,
        "ai_validation": {
            "status": signal.ai_validation.status,
            "confidence": signal.ai_validation.confidence,
            "explanation": signal.ai_validation.explanation,
            "identified_risks": signal.ai_validation.identified_risks,
            "missing_confirmations": signal.ai_validation.missing_confirmations,
        } if signal.ai_validation else None,
    }
