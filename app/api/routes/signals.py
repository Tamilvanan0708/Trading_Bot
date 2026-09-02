"""
Signals API Routes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db_session
from app.database.repository import Repository

router = APIRouter(prefix="/signals", tags=["Trading Signals"])


@router.get("")
async def list_signals(limit: int = 50, db: AsyncSession = Depends(get_db_session)):
    """Lists trading signals exclusively from our dedicated Layered Strategies:
    - SMC With Fib (L1 @ 0.680, L2 @ 0.790)
    - Fib With Retracement (L1 @ 0.618, L2 @ 0.500, L3 @ 0.382)

    Legacy multi-confluence and old method signals are purged and excluded.
    """
    from sqlalchemy import delete
    from app.database.models import SignalModel, AIValidationModel
    from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service

    # Auto-purge any old legacy signals from the DB
    try:
        legacy_ids = (await db.execute(
            select(SignalModel.id).where(
                SignalModel.strategy.in_(["FIBONACCI_RETRACEMENT", "MULTI_TF_CONFLUENCE"])
            )
        )).scalars().all()
        if legacy_ids:
            await db.execute(delete(AIValidationModel).where(AIValidationModel.signal_id.in_(legacy_ids)))
            await db.execute(delete(SignalModel).where(SignalModel.id.in_(legacy_ids)))
            await db.commit()
    except Exception:  # noqa: BLE001
        pass

    # Auto-sync active Layered Strategy setups into signals table
    try:
        smc_svc = get_smc_fib_multi_tf_service("XAUUSD")
        smc_states = await smc_svc.advance(db)
        repo = Repository(db)
        for tf_name, st_card in smc_states.items():
            if not st_card or not st_card.get("point_2"):
                continue
            dir_str = str(st_card.get("direction", "SHORT")).upper()
            levels = st_card.get("levels", {}) or {}
            layers = st_card.get("layers", {}) or {}

            p2 = st_card.get("point_2", {}).get("price")
            p1 = st_card.get("point_1", {}).get("price")
            entry_px = st_card.get("entry", {}).get("price")
            sl_px = st_card.get("sl", {}).get("price")
            tp_px = st_card.get("tp", {}).get("dynamic") or st_card.get("tp", {}).get("price")

            if entry_px and sl_px and tp_px and p2:
                # L1 Layer Signal (0.680 Entry)
                l1_state = "FILLED" if st_card.get("is_entry_touched") else "PENDING"
                sig_id = f"SMC_FIB_{tf_name.upper()}_L1_{int(p2)}"
                existing = await repo.get_signal_by_id(sig_id)
                if existing is None:
                    await repo.save_signal({
                        "id": sig_id,
                        "symbol": "XAUUSD",
                        "strategy": "SMC_WITH_FIB",
                        "strategy_version": "SMC_WITH_FIB_V1:L1",
                        "direction": dir_str,
                        "timeframe": tf_name,
                        "entry_price": float(entry_px),
                        "stop_loss": float(sl_px),
                        "take_profit_1": float(tp_px),
                        "take_profit_2": float(tp_px),
                        "take_profit_3": float(tp_px),
                        "risk_reward": round(abs(tp_px - entry_px) / max(0.1, abs(entry_px - sl_px)), 2),
                        "confidence_score": 92.0,
                        "signal_quality": "VERY_STRONG",
                        "market_bias": "BEARISH" if dir_str == "SHORT" else "BULLISH",
                        "regime": "TRENDING",
                        "session": "LONDON",
                        "outcome": l1_state,
                        "reasons": [
                            f"SMC L1 @ 0.680 Golden Pocket on {tf_name.upper()} ({dir_str}), 0.01 lots",
                            f"Anchor (1.000): ${p2:.2f} | BOS: ${p1:.2f} | Target (0.000): ${tp_px:.2f}",
                            f"Execution: Strict 0.01 Lots with Escape Plan at 0.500 Equilibrium",
                        ],
                    })
                    await db.commit()
                elif existing and existing.outcome != l1_state:
                    await repo.update_signal_outcome(sig_id, {"outcome": l1_state})

                # L2 Layer Signal (0.790 Deep Golden Pocket)
                gp_px = levels.get("0.790", {}).get("price")
                eq_px = levels.get("0.500", {}).get("price")
                if gp_px:
                    l2_state = "FILLED" if (layers.get("L2", {}).get("state") == "FILLED") else "PENDING"
                    l2_tp = eq_px if eq_px else tp_px
                    sig_id_l2 = f"SMC_FIB_{tf_name.upper()}_L2_{int(p2)}"
                    existing_l2 = await repo.get_signal_by_id(sig_id_l2)
                    if existing_l2 is None:
                        await repo.save_signal({
                            "id": sig_id_l2,
                            "symbol": "XAUUSD",
                            "strategy": "SMC_WITH_FIB",
                            "strategy_version": "SMC_WITH_FIB_V1:L2",
                            "direction": dir_str,
                            "timeframe": tf_name,
                            "entry_price": float(gp_px),
                            "stop_loss": float(sl_px),
                            "take_profit_1": float(l2_tp),
                            "take_profit_2": float(l2_tp),
                            "take_profit_3": float(l2_tp),
                            "risk_reward": round(abs(l2_tp - gp_px) / max(0.1, abs(gp_px - sl_px)), 2),
                            "confidence_score": 95.0,
                            "signal_quality": "VERY_STRONG",
                            "market_bias": "BEARISH" if dir_str == "SHORT" else "BULLISH",
                            "regime": "TRENDING",
                            "session": "LONDON",
                            "outcome": l2_state,
                            "reasons": [
                                f"SMC L2 @ 0.790 Deep Golden Pocket on {tf_name.upper()} ({dir_str}), 0.01 lots",
                                f"Anchor (1.000): ${p2:.2f} | Target: ${l2_tp:.2f} (0.500 Equilibrium Escape)",
                                f"Execution: Strict 0.01 Lots (Escape Plan: close L1+L2 at 0.500)",
                            ],
                        })
                        await db.commit()
                    elif existing_l2 and existing_l2.outcome != l2_state:
                        await repo.update_signal_outcome(sig_id_l2, {"outcome": l2_state})
    except Exception:  # noqa: BLE001
        pass

    repo = Repository(db)
    raw_signals = await repo.list_recent_signals(limit=limit * 5)
    signals = [
        s for s in raw_signals
        if s.strategy in ("SMC_WITH_FIB", "FIB_WITH_RETRACEMENT", "RETRACEMENT")
    ][:limit]

    return [
        {
            "id": s.id,
            "created_at": s.created_at.isoformat() + "Z" if (s.created_at and not str(s.created_at).endswith("Z")) else s.created_at,
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
            "reasons": s.reasons,
            "ai_validation": {
                "status": s.ai_validation.status if s.ai_validation else None,
                "confidence": s.ai_validation.confidence if s.ai_validation else None,
                "explanation": s.ai_validation.explanation if s.ai_validation else None,
            } if s.ai_validation else None,
        }
        for s in signals
    ]


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
