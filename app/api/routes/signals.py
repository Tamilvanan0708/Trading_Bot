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
    """Lists trading signals exclusively from our dedicated Layered Strategies:
    - SMC With Fib (Single @ 0.680)
    - Fib With Retracement (L1 @ 0.618, L2 @ 0.500, L3 @ 0.382)

    Legacy multi-confluence and old method signals are purged and excluded.
    """
    global _last_signals_sync_ts
    import time
    now = time.time()
    should_sync = (now - _last_signals_sync_ts) > 15.0

    if should_sync:
        _last_signals_sync_ts = now
        from sqlalchemy import delete
        from app.database.models import SignalModel, AIValidationModel
        from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service

        # Auto-purge any old legacy signals or previous SMC layer test signals from the DB
        try:
            legacy_ids = (await db.execute(
                select(SignalModel.id).where(
                    SignalModel.strategy.in_(["FIBONACCI_RETRACEMENT", "MULTI_TF_CONFLUENCE"])
                    | SignalModel.id.like("SMC_FIB_%_L1_%")
                    | SignalModel.id.like("SMC_FIB_%_L2_%")
                )
            )).scalars().all()
            if legacy_ids:
                await db.execute(delete(AIValidationModel).where(AIValidationModel.signal_id.in_(legacy_ids)))
                await db.execute(delete(SignalModel).where(SignalModel.id.in_(legacy_ids)))
                await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()

        # Auto-sync active strategies into signals table
        try:
            repo = Repository(db)

            # 1. SMC With Fib: SINGLE ENTRY ONLY at 0.680 (strict 0.01 lots)
            smc_svc = get_smc_fib_multi_tf_service("XAUUSD")
            smc_states = await smc_svc.advance(db)
            for tf_name, st_card in smc_states.items():
                if tf_name != "5m" or not st_card or not st_card.get("point_2"):
                    continue
                dir_str = str(st_card.get("direction", "SHORT")).upper()
                p2 = st_card.get("point_2", {}).get("price")
                p1 = st_card.get("point_1", {}).get("price")
                entry_px = st_card.get("entry", {}).get("price")
                sl_px = st_card.get("sl", {}).get("price")
                tp_px = st_card.get("tp", {}).get("dynamic") or st_card.get("tp", {}).get("price")

                if entry_px and sl_px and tp_px and p2:
                    sig_state = "FILLED" if st_card.get("is_entry_touched") else "PENDING"
                    sig_id = f"SMC_FIB_{tf_name.upper()}_{int(p2)}"
                    existing = await repo.get_signal_by_id(sig_id)
                    if existing is None:
                        await repo.save_signal({
                            "id": sig_id,
                            "symbol": "XAUUSD",
                            "strategy": "SMC_WITH_FIB",
                            "strategy_version": "SMC_WITH_FIB_V1",
                            "direction": dir_str,
                            "timeframe": tf_name,
                            "entry_price": float(entry_px),
                            "stop_loss": float(sl_px),
                            "take_profit_1": float(tp_px),
                            "take_profit_2": float(tp_px),
                            "take_profit_3": float(tp_px),
                            "risk_reward": round(abs(tp_px - entry_px) / max(0.1, abs(entry_px - sl_px)), 2),
                            "confidence_score": 95.0,
                            "signal_quality": "VERY_STRONG",
                            "market_bias": "BEARISH" if dir_str == "SHORT" else "BULLISH",
                            "regime": "TRENDING",
                            "session": "LONDON",
                            "outcome": sig_state,
                            "reasons": [
                                f"SMC 0.680 Golden Pocket Single Entry on {tf_name.upper()} ({dir_str}), 0.01 lots",
                                f"Anchor (1.000): ${p2:.2f} | BOS: ${p1:.2f} | Target (0.000): ${tp_px:.2f}",
                                f"Single Trade Execution: 0.01 Lots (No Layer Tranches)",
                            ],
                        })
                        await db.commit()
                    elif existing and existing.outcome != sig_state:
                        await repo.update_signal_outcome(sig_id, {"outcome": sig_state})
                        await db.commit()

            # 2. Fib With Retracement: LAYER ENTRY (L1 @ 0.618, L2 @ 0.500, L3 @ 0.382)
            from app.retracement.multi_tf import get_retracement_multi_tf_service
            retr_svc = get_retracement_multi_tf_service("XAUUSD")
            retr_states = await retr_svc.advance(db)
            for tf_name, setup in retr_states.items():
                if tf_name != "5m" or not setup or not setup.point_2_price:
                    continue
                layers = getattr(setup, "layers", {}) or {}
                for l_key in ["L1", "L2", "L3"]:
                    ratio_val = 0.618 if l_key == "L1" else (0.500 if l_key == "L2" else 0.382)
                    attr = f"fib_{ratio_val:.3f}".replace(".", "_")
                    l_entry = getattr(setup, attr, None)
                    if not l_entry:
                        continue
                    layer_info = layers.get(l_key)
                    l_state = layer_info.get("state", "PENDING") if layer_info else "PENDING"
                    l_tp = setup.fib_1_000 if l_key == "L1" else setup.fib_0_618
                    sig_id = f"FIB_RETR_{tf_name.upper()}_{l_key}_{int(setup.point_2_price)}"
                    existing = await repo.get_signal_by_id(sig_id)
                    if existing is None:
                        await repo.save_signal({
                            "id": sig_id,
                            "symbol": "XAUUSD",
                            "strategy": "FIB_WITH_RETRACEMENT",
                            "strategy_version": f"FIB_RETR_V1:{l_key}",
                            "direction": setup.direction,
                            "timeframe": tf_name,
                            "entry_price": float(l_entry),
                            "stop_loss": float(setup.sl_price or 0),
                            "take_profit_1": float(l_tp or 0),
                            "take_profit_2": float(l_tp or 0),
                            "take_profit_3": float(l_tp or 0),
                            "risk_reward": round(abs((l_tp or 0) - l_entry) / max(0.1, abs(l_entry - (setup.sl_price or 0))), 2),
                            "confidence_score": 92.0,
                            "signal_quality": "VERY_STRONG",
                            "market_bias": "BULLISH" if setup.direction == "LONG" else "BEARISH",
                            "regime": "TRENDING",
                            "session": "LONDON",
                            "outcome": l_state,
                            "reasons": [
                                f"Fib With Retracement {l_key} @ {ratio_val:.3f} on {tf_name.upper()} ({setup.direction}), 0.01 lots",
                                f"Anchor: ${setup.point_2_price:.2f} | BOS: ${setup.bos_price:.2f} | Target: ${l_tp:.2f}",
                                f"3-Tranche Layer Execution: 0.01 lots each (Escape Plan: close at 0.618)",
                            ],
                        })
                        await db.commit()
                    elif existing and existing.outcome != l_state:
                        await repo.update_signal_outcome(sig_id, {"outcome": l_state})
                        await db.commit()

            # 3. Fib Go With Trend: 9 EMA & 21 EMA + 0.618 Breakout Confirmation
            from app.retracement.fib_trend_multi_tf import get_fib_trend_multi_tf_service
            from app.retracement.fib_trend_engine import FibTrendState
            trend_svc = get_fib_trend_multi_tf_service("XAUUSD")
            trend_states = await trend_svc.advance(db)
            t5 = trend_states.get("5m")
            if t5 and t5.point_0_price and (t5.entry_price or t5.trigger_breakout_price):
                dir_str = "LONG" if t5.direction == SignalDirection.LONG else "SHORT"
                sig_id = f"FIB_TREND_5M_{dir_str}_{int(t5.point_0_price)}"
                t_state = "FILLED" if t5.state in (FibTrendState.TRADE_ACTIVE, FibTrendState.COMPLETED) else "PENDING"
                t_entry = float(t5.entry_price or t5.trigger_breakout_price or 0.0)
                t_sl = float(t5.sl_price or t5.fib_0_236 or 0.0)
                t_tp = float(t5.tp_price or t5.fib_1_618 or 0.0)
                existing = await repo.get_signal_by_id(sig_id)
                if existing is None and t_entry > 0:
                    await repo.save_signal({
                        "id": sig_id,
                        "symbol": "XAUUSD",
                        "strategy": "FIB_GO_WITH_TREND",
                        "strategy_version": "FIB_TREND_V1",
                        "direction": dir_str,
                        "timeframe": "5m",
                        "entry_price": t_entry,
                        "stop_loss": t_sl,
                        "take_profit_1": t_tp,
                        "take_profit_2": t_tp,
                        "take_profit_3": t_tp,
                        "risk_reward": round(abs(t_tp - t_entry) / max(0.1, abs(t_entry - t_sl)), 2),
                        "confidence_score": 95.0,
                        "signal_quality": "VERY_STRONG",
                        "market_bias": "BULLISH" if dir_str == "LONG" else "BEARISH",
                        "regime": "TRENDING",
                        "session": "LONDON",
                        "outcome": t_state,
                        "reasons": [
                            f"Fib Go With Trend 9/21 EMA ({dir_str}), 0.01 lots",
                            f"Anchor P0: ${t5.point_0_price:.2f} | Peak P1: ${t5.point_1_price:.2f} | TP (1.618): ${t_tp:.2f}",
                            f"Rule 8 Breakout Trigger Price: ${t_entry:.2f}",
                        ],
                    })
                    await db.commit()
                elif existing and existing.outcome != t_state:
                    await repo.update_signal_outcome(sig_id, {"outcome": t_state})
                    await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()

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
