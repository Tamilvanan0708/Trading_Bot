"""
Signals API Routes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.database.connection import get_db_session
from app.database.repository import Repository
from app.config.execution_settings import calculate_lot_size, get_execution_settings

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
        filtered_signals = [
            s for s in raw_signals
            if s.strategy in ("SMC_WITH_FIB", "FIB_WITH_RETRACEMENT", "RETRACEMENT", "FIB_GO_WITH_TREND")
        ][:limit]

        # Enforce strictly 1 PENDING signal per (strategy, timeframe, layer) slot
        seen_pending_slots = set()
        signals = []
        for s in filtered_signals:
            outcome = (s.outcome or "PENDING").upper()
            if outcome == "PENDING":
                tf_key = (s.timeframe or "5m").lower()
                strat_key = s.strategy
                layer_key = s.strategy_version or ""
                slot_key = (strat_key, tf_key, layer_key if "RETR" in strat_key else "")
                if slot_key in seen_pending_slots:
                    s.outcome = "CANCELLED"
                else:
                    seen_pending_slots.add(slot_key)
            signals.append(s)

        exec_cfg = get_execution_settings()
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

            entry_val = float(s.entry_price or 0.0)
            sl_val = float(s.stop_loss or 0.0)
            calc_lot = calculate_lot_size(
                entry_val,
                sl_val,
                sizing_mode=exec_cfg.sizing_mode,
                target_risk_usd=exec_cfg.target_risk_usd,
                fixed_lot_size=exec_cfg.fixed_lot_size,
                risk_mode=exec_cfg.risk_mode,
                risk_percent=exec_cfg.risk_percent,
                account_balance=exec_cfg.account_balance,
                account_currency=exec_cfg.account_currency,
            ) if (entry_val > 0 and sl_val > 0) else 0.01

            output.append({
                "id": s.id,
                "created_at": created_iso,
                "symbol": s.symbol,
                "timeframe": s.timeframe,
                "direction": s.direction,
                "strategy": s.strategy,
                "strategy_version": s.strategy_version,
                "lot_size": round(calc_lot, 2),
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

    exec_cfg = get_execution_settings()
    entry_val = float(signal.entry_price or 0.0)
    sl_val = float(signal.stop_loss or 0.0)
    calc_lot = calculate_lot_size(
        entry_val,
        sl_val,
        sizing_mode=exec_cfg.sizing_mode,
        target_risk_usd=exec_cfg.target_risk_usd,
        fixed_lot_size=exec_cfg.fixed_lot_size,
        risk_mode=exec_cfg.risk_mode,
        risk_percent=exec_cfg.risk_percent,
        account_balance=exec_cfg.account_balance,
        account_currency=exec_cfg.account_currency,
    ) if (entry_val > 0 and sl_val > 0) else 0.01

    return {
        "id": signal.id,
        "created_at": signal.created_at,
        "symbol": signal.symbol,
        "timeframe": signal.timeframe,
        "direction": signal.direction,
        "strategy": signal.strategy,
        "strategy_version": signal.strategy_version,
        "lot_size": round(calc_lot, 2),
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
